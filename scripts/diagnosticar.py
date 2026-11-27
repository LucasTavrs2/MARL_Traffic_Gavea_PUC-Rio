"""Descobre onde a rede trava.

Roda o programa atuado do SUMO num cenário de teste com o volume reduzido e cheio, e mostra:
- como tempo perdido, viagens concluídas e teleportes mudam com o volume;
- os pontos da rede onde os veículos ficam presos (teleportes), com nome da rua e coordenadas.

Leitura rápida:
- trava mesmo com 25% do volume  -> problema na rede (geometria, faixas, conexões);
- só trava com volume cheio       -> a demanda está pesada demais para a rede.
Motivo do teleporte: 'jam' = fila parada; 'yield' = ninguém cede passagem num cruzamento;
'wrong lane' = o veículo não consegue chegar à faixa de que precisa (quase sempre defeito na rede).

No fim, sugere o maior volume em que a rede ainda escoa (pelo menos 95% das viagens concluídas e
no máximo 5 teleportes) e o valor de --escala-global para regenerar a demanda.

Uso:  python scripts/diagnosticar.py
      python scripts/diagnosticar.py --cenario teste_s102 --escalas 0.25 0.5 0.75 1.0
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marl_gavea.comum import OSM, REDE, RESULTADOS, binario_sumo, carregar_manifesto, rotas_do_conjunto  # noqa: E402
from marl_gavea.geo import Projecao  # noqa: E402
from marl_gavea.metricas import resumo_saida  # noqa: E402

RE_FAIXA = re.compile(r"lane='([^']*)'")
RE_MOTIVO = re.compile(r"\((jam|yield|wrong lane|blocked)\)")


def ler_teleportes(log: Path) -> list[tuple[str, str]]:
    eventos = []
    if not log.exists():
        return eventos
    for linha in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Teleporting vehicle" not in linha:
            continue
        faixa = RE_FAIXA.search(linha)
        motivo = RE_MOTIVO.search(linha)
        eventos.append((faixa.group(1) if faixa else "?", motivo.group(1) if motivo else "outro"))
    return eventos


def nomes_osm(osm: Path = OSM) -> dict[str, str]:
    if not osm.exists():
        return {}
    nomes = {}
    for _, el in ET.iterparse(osm, events=("end",)):
        if el.tag == "way":
            for tag in el.findall("tag"):
                if tag.get("k") == "name":
                    nomes[el.get("id")] = tag.get("v")
            el.clear()
        elif el.tag == "node":
            el.clear()
    return nomes


def id_osm(aresta: str) -> str:
    return aresta.lstrip("-").split("#")[0].split("-Added")[0]


def _pontos(forma: str) -> list[tuple[float, float]]:
    return [tuple(map(float, p.split(",")[:2])) for p in forma.split()]


def _meio(pontos: list[tuple[float, float]]) -> tuple[float, float]:
    """Ponto na metade do comprimento da linha (e não o vértice do meio)."""
    if len(pontos) == 1:
        return pontos[0]
    trechos = [((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 for (x0, y0), (x1, y1) in zip(pontos, pontos[1:])]
    falta = sum(trechos) / 2
    for (x0, y0), (x1, y1), d in zip(pontos, pontos[1:], trechos):
        if falta <= d and d > 0:
            t = falta / d
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0)
        falta -= d
    return pontos[-1]


class Localizador:
    """Traduz uma faixa do SUMO em rua, comprimento e coordenadas.

    Lê a rede direto do XML (não depende do sumolib nem do pyproj). As coordenadas saem da inversa
    da projeção UTM usada pelo netconvert (marl_gavea.geo).
    """

    def __init__(self, rede: Path = REDE, osm: Path = OSM):
        self.nomes = nomes_osm(osm)
        self.arestas: dict[str, tuple[float, tuple[float, float]]] = {}
        self.nos: dict[str, tuple[float, float]] = {}
        self.entradas: dict[str, set[str]] = collections.defaultdict(set)
        self.projecao = Projecao()
        for _, el in ET.iterparse(rede, events=("end",)):
            if el.tag == "location":
                self.projecao = Projecao.do_location(el)
            elif el.tag == "edge":
                if el.get("function") != "internal":
                    faixa = el.find("lane")
                    if faixa is not None and faixa.get("shape"):
                        comprimento = float(faixa.get("length", "0"))
                        self.arestas[el.get("id")] = (comprimento, _meio(_pontos(faixa.get("shape"))))
                    if el.get("to"):
                        self.entradas[el.get("to")].add(el.get("id"))
                el.clear()
            elif el.tag == "junction":
                if el.get("x") is not None:
                    self.nos[el.get("id")] = (float(el.get("x")), float(el.get("y")))
                el.clear()

    def nome_aresta(self, aresta_id: str) -> str:
        return self.nomes.get(id_osm(aresta_id), f"(via OSM {id_osm(aresta_id)}, sem nome)")

    def para_lat_lon(self, x: float, y: float) -> str:
        ponto = self.projecao.para_lat_lon(x, y)
        return f"{ponto[0]:.6f},{ponto[1]:.6f}" if ponto else ""

    def _no_da_faixa_interna(self, faixa: str) -> str | None:
        corpo = faixa[1:]
        for partes in (2, 1):
            candidato = corpo.rsplit("_", partes)[0]
            if candidato in self.nos:
                return candidato
        return None

    def descrever(self, faixa: str) -> dict:
        if faixa.startswith(":"):
            no = self._no_da_faixa_interna(faixa)
            if no is None:
                return {"local": f"dentro de um cruzamento ({faixa})", "comprimento_m": "", "coordenadas": ""}
            ruas = sorted({self.nome_aresta(a) for a in self.entradas.get(no, ())}) or [no]
            return {"local": "dentro do cruzamento: " + " / ".join(ruas), "comprimento_m": "",
                    "coordenadas": self.para_lat_lon(*self.nos[no])}
        aresta = faixa.rsplit("_", 1)[0]
        local = self.nome_aresta(aresta)
        if aresta not in self.arestas:
            return {"local": local, "comprimento_m": "", "coordenadas": ""}
        comprimento, (x, y) = self.arestas[aresta]
        return {"local": local, "comprimento_m": f"{comprimento:.1f}", "coordenadas": self.para_lat_lon(x, y)}


def sugerir_escala(resultados: dict[float, dict], min_concluidos: float = 95.0, max_teleportes: int = 5) -> float | None:
    """Maior volume em que a rede ainda escoa: quase todos chegam e quase ninguém é teleportado."""
    boas = [e for e, r in resultados.items()
            if r.get("pct_concluidos", 0) >= min_concluidos and r.get("teleportes", 10**9) <= max_teleportes]
    return max(boas) if boas else None


def rodar(rotas: list[str], escala: float, duracao: int, pasta: Path) -> tuple[dict, list]:
    pasta.mkdir(parents=True, exist_ok=True)
    log = pasta / "sumo.log"
    base = [
        binario_sumo(), "-n", str(REDE), "-r", ",".join(rotas),
        "--scale", str(escala), "--end", str(duracao), "--seed", "0",
        "--time-to-teleport", "300", "--no-step-log", "true",
        "--tripinfo-output", str(pasta / "tripinfo.xml"), "--tripinfo-output.write-unfinished", "true",
        "--statistic-output", str(pasta / "estatisticas.xml"), "--log", str(log),
    ]
    r = subprocess.run(base + ["--aggregate-warnings", "-1"], capture_output=True, text=True)
    if r.returncode != 0 and "aggregate-warnings" in (r.stderr + r.stdout):
        r = subprocess.run(base, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise SystemExit(f"O SUMO falhou com escala {escala}")
    return resumo_saida(pasta), ler_teleportes(log)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cenario", help="nome do cenário (padrão: o primeiro de teste)")
    p.add_argument("--escalas", nargs="+", type=float, default=[0.3, 0.4, 0.5, 0.6, 0.8, 1.0])
    p.add_argument("--duracao", type=int, default=4200)
    p.add_argument("--top", type=int, default=15, help="quantos pontos de travamento mostrar")
    args = p.parse_args()

    manifesto = carregar_manifesto()
    todos = {c["nome"]: c for conjunto in ("teste", "validacao", "treino") for c in rotas_do_conjunto(manifesto, conjunto)}
    nome = args.cenario or rotas_do_conjunto(manifesto, "teste")[0]["nome"]
    if nome not in todos:
        raise SystemExit(f"Cenário {nome} não existe. Opções: {', '.join(sorted(todos))}")
    pasta = RESULTADOS / "diagnostico" / datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Cenário {nome}, programa atuado do SUMO\n")
    print(f"{'volume':>7s} {'veículos':>9s} {'concluídos %':>13s} {'perdido (s)':>12s} {'teleportes':>11s}  motivos")
    resultados, resumos = {}, {}
    for escala in sorted(args.escalas):
        resumo, eventos = rodar(todos[nome]["rotas"], escala, args.duracao, pasta / f"escala_{escala:g}")
        resumo.setdefault("teleportes", len(eventos))
        resultados[escala], resumos[escala] = eventos, resumo
        motivos = collections.Counter(m for _, m in eventos)
        print(f"{escala * 100:>6.0f}% {resumo.get('veiculos', 0):>9} {resumo.get('pct_concluidos', float('nan')):>13.1f} "
              f"{resumo.get('tempo_perdido_medio_s', float('nan')):>12.1f} {len(eventos):>11}  "
              + ", ".join(f"{m}: {n}" for m, n in motivos.most_common()))

    localizador = Localizador()
    linhas = []
    for escala, eventos in sorted(resultados.items()):
        por_faixa = collections.defaultdict(collections.Counter)
        for faixa, motivo in eventos:
            por_faixa[faixa][motivo] += 1
        for faixa, motivos in por_faixa.items():
            linhas.append({"volume_pct": int(escala * 100), "faixa": faixa, "teleportes": sum(motivos.values()),
                           "motivos": " ".join(f"{m}:{n}" for m, n in motivos.most_common()),
                           **localizador.descrever(faixa)})

    menor = min(resultados)
    for escala in (menor, max(resultados)) if menor != max(resultados) else (menor,):
        do_volume = sorted((l for l in linhas if l["volume_pct"] == int(escala * 100)), key=lambda l: -l["teleportes"])
        if not do_volume:
            print(f"\nCom {escala * 100:.0f}% do volume: nenhum teleporte.")
            continue
        print(f"\nOnde trava com {escala * 100:.0f}% do volume (os {args.top} piores):")
        print(f"{'tel.':>5s}  {'motivos':24s} {'compr. (m)':>10s}  {'coordenadas':22s} local")
        for l in do_volume[: args.top]:
            print(f"{l['teleportes']:>5}  {l['motivos']:24s} {l['comprimento_m']:>10s}  {l['coordenadas']:22s} {l['local']}")

    global_atual = float(manifesto.get("escala_global", 1.0))
    sugerida = sugerir_escala(resumos)
    print()
    if sugerida is None:
        print("Nenhum volume testado escoou bem (>= 95% concluídos e <= 5 teleportes).")
        print("Se nem o menor volume passa, o problema está na rede: veja os pontos acima no sumo-gui/netedit.")
    else:
        nova = round(global_atual * sugerida, 3)
        print(f"Volume sugerido: {sugerida * 100:.0f}% da demanda atual (escala global atual: {global_atual:g}).")
        print(f"Regenere a demanda com:  python scripts/gerar_demanda.py --escala-global {nova:g}")
        if sugerida == max(resumos):
            print("Esse foi o maior volume testado; dá para testar valores acima com --escalas.")

    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / "teleportes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["volume_pct", "teleportes", "motivos", "local", "comprimento_m", "coordenadas", "faixa"])
        w.writeheader()
        w.writerows(sorted(linhas, key=lambda l: (l["volume_pct"], -l["teleportes"])))
    print(f"\nTabela completa em {pasta / 'teleportes.csv'}")
    print("Cole as coordenadas no Google Maps para ver o ponto.")


if __name__ == "__main__":
    main()
