"""Lista os semáforos da rede: ruas que se cruzam, posição, fases verdes e vizinhos próximos.

Serve para duas coisas:
- conferir o agrupamento feito pelo netconvert: dois controladores a poucos metros um do outro
  costumam ser o mesmo cruzamento (rotatória, canteiro central) e deviam virar um só, no
  tls_grupos.nod.xml;
- montar a lista de cruzamentos do pedido de dados à CET-Rio.

Grava semaforos.csv na pasta do mapa. Lê só os arquivos da rede (não precisa do SUMO rodando).

Exemplos:
  python scripts/listar_semaforos.py
  $env:MARL_MAPA="gavea_ampliada"; python scripts/listar_semaforos.py
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnosticar import id_osm, nomes_osm  # noqa: E402
from marl_gavea.comum import OSM, REDE  # noqa: E402
from marl_gavea.geo import Projecao  # noqa: E402

PERTO_M = 60.0  # abaixo disso, dois controladores provavelmente são o mesmo cruzamento


def fases_verdes(estados: list[str]) -> list[str]:
    """Mesma regra do ambiente: estados distintos com verde e sem amarelo."""
    verdes = []
    for e in estados:
        if any(c in e for c in "yYu") or not any(c in "Gg" for c in e):
            continue
        if e not in verdes:
            verdes.append(e)
    return verdes


def ler_rede(rede: Path, osm: Path | None = None) -> list[dict]:
    nomes = nomes_osm(osm) if osm else {}
    projecao = Projecao()
    arestas: dict[str, dict] = {}
    nos: dict[str, tuple[float, float]] = {}
    logicas: dict[str, list[str]] = {}
    entradas: dict[str, set[str]] = {}
    for _, el in ET.iterparse(rede, events=("end",)):
        if el.tag == "location":
            projecao = Projecao.do_location(el)
        elif el.tag == "edge" and el.get("function") != "internal":
            arestas[el.get("id")] = {"para": el.get("to"), "nome": el.get("name")}
            el.clear()
        elif el.tag == "junction":
            if el.get("x") is not None:
                nos[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "tlLogic":
            logicas.setdefault(el.get("id"), [f.get("state") for f in el.findall("phase")])
            el.clear()
        elif el.tag == "connection" and el.get("tl"):
            entradas.setdefault(el.get("tl"), set()).add(el.get("from"))

    def nome(aresta: str) -> str:
        info = arestas.get(aresta, {})
        return info.get("nome") or nomes.get(id_osm(aresta)) or "(sem nome)"

    semaforos = []
    for tl, estados in logicas.items():
        de = sorted(entradas.get(tl, ()))
        cruzamentos = {arestas[a]["para"] for a in de if a in arestas and arestas[a]["para"] in nos}
        if cruzamentos:
            x = sum(nos[c][0] for c in cruzamentos) / len(cruzamentos)
            y = sum(nos[c][1] for c in cruzamentos) / len(cruzamentos)
        else:
            x = y = float("nan")
        n_verdes = len(fases_verdes(estados))
        semaforos.append({
            "id": tl,
            "tipo": "agente" if n_verdes >= 2 else "fixo",
            "fases_verdes": n_verdes,
            "ruas": " x ".join(sorted({nome(a) for a in de})) or "(sem entradas)",
            "entradas": len(de),
            "x": x,
            "y": y,
            "coord": projecao.para_lat_lon(x, y) if not math.isnan(x) else None,
            "cruzamentos": sorted(cruzamentos),
        })
    for s in semaforos:
        outros = [(math.dist((s["x"], s["y"]), (o["x"], o["y"])), o["id"]) for o in semaforos
                  if o is not s and not math.isnan(o["x"]) and not math.isnan(s["x"])]
        s["distancia_m"], s["vizinho"] = min(outros) if outros else (float("nan"), "")
    # do norte para o sul, e de oeste para leste
    return sorted(semaforos, key=lambda s: (-(s["coord"] or (0, 0))[0], (s["coord"] or (0, 0))[1]))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rede", default=str(REDE))
    p.add_argument("--saida", help="CSV de saída (padrão: semaforos.csv na pasta do mapa)")
    args = p.parse_args()
    rede = Path(args.rede)
    if not rede.exists():
        raise SystemExit(f"Rede não encontrada: {rede}. Rode antes o build.ps1 da pasta do mapa.")
    osm = rede.with_name(OSM.name)
    semaforos = ler_rede(rede, osm if osm.exists() else None)

    agentes = sum(s["tipo"] == "agente" for s in semaforos)
    print(f"{len(semaforos)} controladores: {agentes} viram agentes, {len(semaforos) - agentes} ficam fixos "
          f"(uma só fase verde)\n")
    print(f"{'controlador':28s} {'tipo':6s} {'fases':>5s} {'coordenadas':>22s}  ruas")
    for s in semaforos:
        coord = f"{s['coord'][0]:.5f},{s['coord'][1]:.5f}" if s["coord"] else "?"
        aviso = ""
        if s["distancia_m"] < PERTO_M:
            aviso = f"   <- a {s['distancia_m']:.0f} m de {s['vizinho']}: mesmo cruzamento?"
        print(f"{s['id'][:28]:28s} {s['tipo']:6s} {s['fases_verdes']:>5d} {coord:>22s}  {s['ruas']}{aviso}")

    saida = Path(args.saida) if args.saida else rede.with_name("semaforos.csv")
    with saida.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["controlador", "tipo", "fases_verdes", "ruas", "lat", "lon", "entradas",
                    "vizinho_mais_proximo", "distancia_m", "cruzamentos"])
        for s in semaforos:
            lat, lon = s["coord"] if s["coord"] else ("", "")
            w.writerow([s["id"], s["tipo"], s["fases_verdes"], s["ruas"],
                        f"{lat:.6f}" if lat != "" else "", f"{lon:.6f}" if lon != "" else "",
                        s["entradas"], s["vizinho"],
                        f"{s['distancia_m']:.0f}" if not math.isnan(s["distancia_m"]) else "",
                        " ".join(s["cruzamentos"])])
    perto = sum(s["distancia_m"] < PERTO_M for s in semaforos)
    if perto:
        print(f"\n{perto} controlador(es) a menos de {PERTO_M:.0f} m de outro: confira se são o mesmo cruzamento.")
    print(f"\nLista salva em {saida} (abre no Excel)")


if __name__ == "__main__":
    main()
