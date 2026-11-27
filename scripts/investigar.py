"""Investiga onde e por que um controlador trava.

Modo 1, rodar: roda um método (idqn, max_pressao, aleatorio ou atuado) em vários cenários e volumes,
registrando a cada decisão o estado de cada semáforo. Marca como COLAPSO as rodadas com tempo perdido
acima do dobro da mediana e, para a pior rodada, mostra:
- por semáforo: fila máxima e média (veículos parados na zona antes dele), trocas de fase, trocas
  forçadas pelo verde máximo e a faixa que ficou mais tempo sem verde com carros esperando;
- a fila de cada semáforo ao longo do episódio, em blocos de 10 minutos (onde a fila nasce primeiro);
- onde os veículos foram teleportados.
Para todas as rodadas, por volume: em quantas decisões cada agente estava "cego", com alguma zona
antes dele cheia de parados (a fila passou do que ele enxerga), e com a recompensa no piso (-1).

Modo 2, ler uma pasta de saída que já existe (por exemplo, uma validação que colapsou no treino):
mostra de quais ruas saíram os veículos mais atrasados e quantos não terminaram a viagem.

Uso:
  python scripts/investigar.py --modelo resultados/idqn/melhor.npz
  python scripts/investigar.py --modelo resultados/idqn/melhor.npz --escalas 1.0 1.25 1.5
  python scripts/investigar.py --metodo max_pressao --escalas 1.5
  python scripts/investigar.py --modelo resultados/idqn_v3.1_s0/melhor.npz --conjuntos teste --escalas 1.0 1.5
  python scripts/investigar.py --pasta resultados/idqn/validacao/ep1495
"""
from __future__ import annotations

import argparse
import collections
import csv
import statistics
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from avaliar import criar_politica  # noqa: E402
from diagnosticar import Localizador, ler_teleportes  # noqa: E402

from marl_gavea.ambiente import AmbienteGavea  # noqa: E402
from marl_gavea.comum import REDE, RESULTADOS, carregar_manifesto, espelhar_saida, rotas_do_conjunto  # noqa: E402

BLOCO = 600  # segundos por coluna na linha do tempo
CHEIA = 0.9  # zona com pelo menos 90% da capacidade ocupada por parados conta como cheia


class Rastreador:
    """Envolve uma política e registra, a cada decisão, fila e sinal de cada semáforo."""

    def __init__(self, politica):
        self.politica = politica
        self.controla = politica.controla

    def preparar(self, env) -> None:
        self.indices = {
            a: {f: [i for i, l in enumerate(s.links) if l and l[0] == f] for f in s.entradas}
            for a, s in env.semaforos.items()
        }
        t = env.conn.simulation.getTime()
        self.ultimo_verde = {(a, f): t for a in env.agentes for f in env.semaforos[a].entradas}
        self.pior_espera: dict[tuple[str, str], tuple[float, float, float]] = {}
        self.registros: list[dict] = []

    def __call__(self, env, obs):
        t = env.conn.simulation.getTime()
        estados, filas = {}, {}
        for a in env.agentes:
            s = env.semaforos[a]
            estado = env.conn.trafficlight.getRedYellowGreenState(a)
            estados[a], filas[a] = estado, env._fila(a)
            for f, indices in self.indices[a].items():
                verde = any(i < len(estado) and estado[i] in "Gg" for i in indices)
                parados = env._soma(env._parado, s.zona_entrada[f])
                if verde or parados == 0:
                    self.ultimo_verde[(a, f)] = t
                    continue
                espera = t - self.ultimo_verde[(a, f)]
                if espera > self.pior_espera.get((a, f), (0.0, 0.0, 0.0))[0]:
                    self.pior_espera[(a, f)] = (espera, t, parados)
        acoes = self.politica(env, obs)
        for a in env.agentes:
            s = env.semaforos[a]
            # a partir daqui a fila passa do que o agente enxerga (observação e recompensa)
            cheia = any(env._soma(env._parado, s.zona_entrada[f]) >= CHEIA * s.cap_zona[f] for f in s.entradas)
            self.registros.append({"tempo": t, "agente": a, "estado": estados[a],
                                   "acao": acoes.get(a, ""), "fila": filas[a],
                                   "zona_cheia": int(cheia), "recompensa_no_piso": int(filas[a] >= s.cap_montante)})
        return acoes


def rodar(politica, cenario: dict, escala: float, duracao: int, pasta: Path) -> dict:
    rastreador = Rastreador(politica)
    env = AmbienteGavea(REDE, cenario["rotas"], duracao=duracao, controlar=politica.controla,
                        semente=cenario["semente"], saida=pasta, escala=escala, avisos=True)
    obs = env.reset()
    rastreador.preparar(env)
    fim = False
    try:
        while not fim:
            acoes = rastreador(env, obs)
            obs, _, fim, _ = env.step(acoes)
        semaforos = {a: {"trocas": s.trocas, "forcadas": s.forcadas, "por_espera": s.por_espera}
                     for a, s in env.semaforos.items()}
    finally:
        resumo = env.fechar()
    return {"resumo": resumo, "rastreador": rastreador, "semaforos": semaforos, "pasta": pasta,
            "cenario": cenario["nome"], "escala": escala}


def relatorio_teleportes(pasta: Path, localizador: Localizador, top: int) -> None:
    eventos = ler_teleportes(pasta / "sumo.log")
    if not eventos:
        print("\nNenhum teleporte.")
        return
    por_local = collections.defaultdict(collections.Counter)
    for faixa, motivo in eventos:
        d = localizador.descrever(faixa)
        por_local[(d["local"], d["coordenadas"])][motivo] += 1
    print(f"\nTeleportes ({len(eventos)}), por local:")
    for (local, coord), motivos in sorted(por_local.items(), key=lambda x: -sum(x[1].values()))[:top]:
        texto = " ".join(f"{m}:{n}" for m, n in motivos.most_common())
        print(f"{sum(motivos.values()):>5}  {texto:24s} {coord:22s} {local}")


def relatorio_rodada(r: dict, localizador: Localizador, top: int) -> None:
    reg = r["rastreador"].registros
    agentes = sorted({x["agente"] for x in reg})
    print(f"\n=== Pior rodada: {r['cenario']}, volume {r['escala'] * 100:.0f}% "
          f"(perdido {r['resumo'].get('tempo_perdido_medio_s', float('nan')):.1f} s, "
          f"teleportes {r['resumo'].get('teleportes', '?')}) ===")

    print("\nPor semáforo (fila = veículos parados na zona antes dele):")
    print("(forçadas = pelo verde máximo; por espera = pelo vermelho máximo)")
    print(f"{'semáforo':30s} {'fila máx':>8s} {'média':>6s} {'trocas':>7s} {'forçadas':>9s} {'por espera':>11s}  "
          f"maior tempo sem verde com fila")
    for a in agentes:
        filas = [x["fila"] for x in reg if x["agente"] == a]
        c = r["semaforos"][a]
        piores = [(v, f) for (ag, f), v in r["rastreador"].pior_espera.items() if ag == a]
        if piores:
            (espera, quando, parados), faixa = max(piores)
            rua = localizador.nome_aresta(faixa.rsplit("_", 1)[0])
            texto = f"{espera:5.0f} s em {rua} (faixa {faixa}, aos {quando / 60:.0f} min, {parados:.0f} parados)"
        else:
            texto = "-"
        print(f"{a:30s} {max(filas):>8.0f} {statistics.mean(filas):>6.1f} {c['trocas']:>7} {c['forcadas']:>9} "
              f"{c['por_espera']:>11}  {texto}")

    fim = max(x["tempo"] for x in reg)
    blocos = list(range(0, int(fim) + 1, BLOCO))
    print(f"\nFila média por semáforo a cada {BLOCO // 60} min (procure onde a fila cresce primeiro):")
    print(f"{'semáforo':30s}" + "".join(f"{b // 60:>4d}-{(b + BLOCO) // 60:<3d}" for b in blocos))
    for a in agentes:
        linha = f"{a:30s}"
        for b in blocos:
            v = [x["fila"] for x in reg if x["agente"] == a and b <= x["tempo"] < b + BLOCO]
            linha += f"{statistics.mean(v) if v else 0:>8.1f}"
        print(linha)

    relatorio_teleportes(r["pasta"], localizador, top)

    r["pasta"].mkdir(parents=True, exist_ok=True)
    with (r["pasta"] / "decisoes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["tempo", "agente", "estado", "acao", "fila", "zona_cheia",
                                          "recompensa_no_piso"])
        w.writeheader()
        w.writerows(reg)
    print(f"\nRegistro de todas as decisões em {r['pasta'] / 'decisoes.csv'}")


def ler_pasta(pasta: Path, localizador: Localizador, top: int) -> None:
    """Analisa uma saída já gravada: de onde saíram os veículos mais atrasados."""
    tripinfo = pasta / "tripinfo.xml"
    if not tripinfo.exists():
        raise SystemExit(f"Não achei {tripinfo}")
    por_origem = collections.defaultdict(list)
    nao_terminaram = collections.Counter()
    for _, el in ET.iterparse(tripinfo, events=("end",)):
        if el.tag == "tripinfo":
            aresta = el.get("departLane", "?").rsplit("_", 1)[0]
            perdido = float(el.get("timeLoss", "0"))
            por_origem[aresta].append(perdido)
            if float(el.get("arrival", "-1")) < 0:
                nao_terminaram[aresta] += 1
            el.clear()
    total = sum(len(v) for v in por_origem.values())
    todos = [x for v in por_origem.values() for x in v]
    print(f"{pasta}: {total} veículos, tempo perdido médio {statistics.mean(todos):.1f} s, "
          f"{sum(nao_terminaram.values())} não terminaram")
    print(f"\nRuas de origem com mais tempo perdido somado (os {top} piores):")
    print(f"{'veíc.':>6s} {'perdido médio':>14s} {'máx':>7s} {'não term.':>10s}  {'coordenadas':22s} rua")
    linhas = sorted(por_origem.items(), key=lambda x: -sum(x[1]))[:top]
    for aresta, valores in linhas:
        d = localizador.descrever(f"{aresta}_0")
        print(f"{len(valores):>6} {statistics.mean(valores):>14.1f} {max(valores):>7.0f} "
              f"{nao_terminaram[aresta]:>10}  {d['coordenadas']:22s} {localizador.nome_aresta(aresta)}")
    if (pasta / "sumo.log").exists():
        relatorio_teleportes(pasta, localizador, top)


def relatorio_saturacao(rodadas: list[dict]) -> dict:
    """Em quantas decisões o agente estava "cego": alguma zona antes dele cheia de parados.

    Com a zona cheia, uma fila maior que ela não muda nem a observação nem a recompensa. Se isso
    for raro com 100% do volume e comum com 150%, a perda de vantagem no trânsito pesado vem daí."""
    escalas = sorted({r["escala"] for r in rodadas})
    agentes = sorted({x["agente"] for r in rodadas for x in r["rastreador"].registros})
    tabela = {}
    for a in agentes + ["todos"]:
        for e in escalas:
            regs = [x for r in rodadas if r["escala"] == e for x in r["rastreador"].registros
                    if a == "todos" or x["agente"] == a]
            n = len(regs) or 1
            tabela[(a, e)] = (100.0 * sum(x["zona_cheia"] for x in regs) / n,
                              100.0 * sum(x["recompensa_no_piso"] for x in regs) / n)
    print(f"\nDecisões com alguma zona antes do semáforo cheia (>= {CHEIA:.0%} de parados) e com a recompensa no piso (-1):")
    print(f"{'semáforo':30s}" + "".join(f"{f'{e * 100:.0f}%: cheia':>14s}{'piso':>7s}" for e in escalas))
    for a in agentes + ["todos"]:
        print(f"{a:30s}" + "".join(f"{tabela[(a, e)][0]:>13.1f}%{tabela[(a, e)][1]:>6.1f}%" for e in escalas))
    return tabela


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--metodo", default="idqn", choices=["atuado", "aleatorio", "max_pressao", "idqn"])
    p.add_argument("--modelo", help="arquivo .npz do IDQN")
    p.add_argument("--conjuntos", nargs="+", default=["validacao", "teste", "treino"],
                   choices=["validacao", "teste", "treino"])
    p.add_argument("--escalas", nargs="+", type=float, default=[1.0])
    p.add_argument("--duracao", type=int, default=4200)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--pasta", help="em vez de rodar, analisa uma pasta de saída que já existe")
    args = p.parse_args()

    localizador = Localizador()
    if args.pasta:
        arquivo = espelhar_saida(Path(args.pasta) / "investigacao.txt")
        ler_pasta(Path(args.pasta), localizador, args.top)
        print(f"\n(salvo em {arquivo})")
        return

    politica = criar_politica(args.metodo, args.modelo)
    manifesto = carregar_manifesto()
    cenarios = [c for conjunto in args.conjuntos for c in rotas_do_conjunto(manifesto, conjunto)]
    raiz = RESULTADOS / "investigacao" / datetime.now().strftime("%Y%m%d_%H%M%S")

    espelhar_saida(raiz / "saida.txt")
    rodadas = []
    print(f"Método {args.metodo}: {len(cenarios)} cenário(s) x {len(args.escalas)} volume(s)\n")
    for escala in args.escalas:
        for c in cenarios:
            r = rodar(politica, c, escala, args.duracao, raiz / f"volume_{escala:g}" / c["nome"])
            rodadas.append(r)
            print(f"{escala * 100:4.0f}%  {c['nome']:14s} perdido {r['resumo'].get('tempo_perdido_medio_s', float('nan')):6.1f} s"
                  f" | teleportes {r['resumo'].get('teleportes', '?'):>4}"
                  f" | trocas forçadas {r['resumo'].get('trocas_forcadas', '-')}"
                  f" | por espera {r['resumo'].get('trocas_por_espera', '-')}", flush=True)

    perdidos = [r["resumo"].get("tempo_perdido_medio_s", float("nan")) for r in rodadas]
    mediana = statistics.median(perdidos)
    colapsos = [r for r, v in zip(rodadas, perdidos) if v > 2 * mediana]
    print(f"\nMediana do tempo perdido: {mediana:.1f} s. Colapsos (mais que o dobro): {len(colapsos)}")
    for r in colapsos:
        print(f"   COLAPSO: {r['cenario']}, volume {r['escala'] * 100:.0f}%")

    relatorio_saturacao(rodadas)
    pior = max(rodadas, key=lambda r: r["resumo"].get("tempo_perdido_medio_s", float("-inf")))
    relatorio_rodada(pior, localizador, args.top)
    print(f"\nArquivos em {raiz} (o que apareceu aqui está em saida.txt)")


if __name__ == "__main__":
    main()
