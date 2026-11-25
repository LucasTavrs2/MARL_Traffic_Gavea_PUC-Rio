"""Compara métodos de controle nos mesmos cenários de demanda.

Métodos: atuado (programa do SUMO), aleatorio, max_pressao e idqn (precisa de --modelo).
Cada cenário usa a mesma semente do SUMO para todos os métodos, então a comparação é justa.

Teste de estresse: --escalas multiplica o volume de cada cenário (1.0 = volume calibrado), sem
treinar de novo. Ex.: --escalas 1.0 1.25 1.5 roda tudo com o volume normal, +25% e +50%.

Exemplos:
  python scripts/avaliar.py
  python scripts/avaliar.py --metodos atuado max_pressao idqn --modelo resultados/idqn/melhor.npz
  python scripts/avaliar.py --metodos idqn --modelo resultados/idqn/melhor.npz --gui
  python scripts/avaliar.py --metodos atuado max_pressao idqn --modelo resultados/idqn/melhor.npz --escalas 1.0 1.25 1.5
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from marl_gavea.ambiente import AmbienteGavea, rodar_episodio  # noqa: E402
from marl_gavea.comum import REDE, RESULTADOS, carregar_manifesto, espelhar_saida, rotas_do_conjunto  # noqa: E402
from marl_gavea.controladores import Aleatorio, Atuado, MaxPressao  # noqa: E402

METRICAS = [
    ("tempo_perdido_medio_s", "perdido (s)"),
    ("espera_media_s", "espera (s)"),
    ("tempo_viagem_medio_s", "viagem (s)"),
    ("pct_concluidos", "concluídos %"),
    ("teleportes", "teleportes"),
    ("tempo_perdido_onibus_s", "perdido ônibus (s)"),
]


def criar_politica(nome: str, modelo: str | None):
    if nome == "atuado":
        return Atuado()
    if nome == "aleatorio":
        return Aleatorio(semente=123)
    if nome == "max_pressao":
        return MaxPressao()
    if nome == "idqn":
        if not modelo:
            raise SystemExit("Para avaliar o idqn, informe --modelo, ex.: resultados/idqn/melhor.npz")
        from marl_gavea.idqn import PoliticaIDQN

        return PoliticaIDQN(modelo)
    raise SystemExit(f"Método desconhecido: {nome}")


def media_desvio(valores):
    v = [x for x in valores if isinstance(x, (int, float)) and not math.isnan(x)]
    if not v:
        return float("nan"), float("nan")
    return float(np.mean(v)), float(np.std(v))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--metodos", nargs="+", default=["atuado", "aleatorio", "max_pressao"],
                   choices=["atuado", "aleatorio", "max_pressao", "idqn"])
    p.add_argument("--modelo", help="arquivo .npz do IDQN")
    p.add_argument("--conjunto", default="teste", choices=["teste", "validacao", "treino"])
    p.add_argument("--duracao", type=int, default=4200)
    p.add_argument("--escalas", nargs="+", type=float, default=[1.0],
                   help="multiplicadores do volume para teste de estresse (padrão: 1.0)")
    p.add_argument("--gui", action="store_true", help="abre o sumo-gui (roda só o primeiro cenário)")
    p.add_argument("--saida", default=str(RESULTADOS / "avaliacao"))
    args = p.parse_args()

    cenarios = rotas_do_conjunto(carregar_manifesto(), args.conjunto)
    if args.gui:
        cenarios = cenarios[:1]
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta = Path(args.saida) / carimbo
    linhas = []
    politicas = {m: criar_politica(m, args.modelo) for m in args.metodos}  # falha logo se faltar o modelo
    espelhar_saida(pasta / "saida.txt")

    for escala in args.escalas:
        for metodo, politica in politicas.items():
            for c in cenarios:
                saida = pasta / (f"volume_{escala:g}" if len(args.escalas) > 1 else "") / metodo / c["nome"]
                env = AmbienteGavea(REDE, c["rotas"], duracao=args.duracao, controlar=politica.controla,
                                    gui=args.gui, semente=c["semente"], saida=saida, escala=escala)
                resumo = rodar_episodio(env, politica)
                linhas.append({"escala": escala, "metodo": metodo, "cenario": c["nome"], **resumo})
                print(f"{escala * 100:4.0f}%  {metodo:12s} {c['nome']:14s} "
                      f"perdido {resumo.get('tempo_perdido_medio_s', float('nan')):6.1f} s"
                      f" | teleportes {resumo.get('teleportes', '?')}", flush=True)

    pasta.mkdir(parents=True, exist_ok=True)
    fixas = ["escala", "metodo", "cenario"]
    colunas = fixas + sorted({k for l in linhas for k in l} - set(fixas))
    with (pasta / "detalhe.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=colunas, restval="")
        w.writeheader()
        w.writerows(linhas)

    with (pasta / "resumo.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["escala", "metodo"] + [f"{m}_{s}" for m, _ in METRICAS for s in ("media", "desvio")]
                   + ["perdido_vs_atuado_pct"])
        for escala in args.escalas:
            resumo_metodos = {}
            for metodo in args.metodos:
                do_metodo = [l for l in linhas if l["metodo"] == metodo and l["escala"] == escala]
                resumo_metodos[metodo] = {m: media_desvio([l.get(m) for l in do_metodo]) for m, _ in METRICAS}
            ref = resumo_metodos.get("atuado", {}).get("tempo_perdido_medio_s", (float("nan"),))[0]
            volume = f", volume {escala * 100:.0f}%" if escala != 1.0 or len(args.escalas) > 1 else ""
            print(f"\nResumo no conjunto '{args.conjunto}' ({len(cenarios)} cenário(s){volume}), média ± desvio:")
            print(f"{'método':12s}" + "".join(f"{rotulo:>22s}" for _, rotulo in METRICAS) + f"{'perdido vs atuado':>20s}")
            for metodo, r in resumo_metodos.items():
                perdido = r["tempo_perdido_medio_s"][0]
                variacao = 100.0 * (perdido - ref) / ref if ref and not math.isnan(ref) else float("nan")
                print(f"{metodo:12s}" + "".join(f"{r[m][0]:>14.1f} ± {r[m][1]:<5.1f}" for m, _ in METRICAS)
                      + f"{variacao:>+19.1f}%")
                w.writerow([escala, metodo] + [round(x, 3) for m, _ in METRICAS for x in r[m]] + [round(variacao, 2)])
    print(f"\nArquivos em {pasta} (o que apareceu aqui está em saida.txt)")


if __name__ == "__main__":
    main()
