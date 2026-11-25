"""Confere a instalação e mostra quais semáforos viram agentes. Roda 2 minutos de simulação."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from marl_gavea.ambiente import AmbienteGavea  # noqa: E402
from marl_gavea.comum import MANIFESTO, REDE, binario_sumo, carregar_manifesto, preparar_sumo, rotas_do_conjunto  # noqa: E402
from marl_gavea.controladores import MaxPressao  # noqa: E402


def main() -> None:
    print(f"Python {platform.python_version()} | numpy {np.__version__}")
    home = preparar_sumo()
    print(f"SUMO_HOME = {home}")
    versao = subprocess.run([binario_sumo(), "--version"], capture_output=True, text=True).stdout.splitlines()
    print(versao[0] if versao else "(não consegui ler a versão do SUMO)")
    if not REDE.exists():
        raise SystemExit(f"Rede não encontrada: {REDE}")
    print(f"Rede: {REDE}\n")

    rotas = rotas_do_conjunto(carregar_manifesto(), "validacao")[0]["rotas"] if MANIFESTO.exists() else []
    if not rotas:
        print("(Sem demanda gerada ainda: o teste roda com a rede vazia.)\n")

    env = AmbienteGavea(REDE, rotas, duracao=120)
    obs = env.reset()
    print(f"{'semáforo':32s} {'fases verdes':>12s} {'faixas entrada':>15s} {'dim obs':>8s}")
    for a in env.agentes:
        s = env.semaforos[a]
        print(f"{a:32s} {s.n_fases:12d} {len(s.entradas):15d} {env.dim_obs[a]:8d}")
    print(f"\nAgentes: {len(env.agentes)}")
    if env.fixos:
        print(f"Ficam no programa original (uma só fase verde): {', '.join(env.fixos)}")

    politica = MaxPressao()
    fim = False
    while not fim:
        obs, _, fim, info = env.step(politica(env, obs))
    resumo = env.fechar()
    print(f"\nSimulação de teste ok: {info['tempo']:.0f} s simulados, {resumo.get('trocas_de_fase', 0)} trocas de fase.")


if __name__ == "__main__":
    main()
