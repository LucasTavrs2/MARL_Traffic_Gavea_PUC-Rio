"""Controladores de referência (baselines)."""
from __future__ import annotations

import numpy as np


class Atuado:
    """Programa atuado gerado pelo netconvert, rodando no próprio SUMO. Não toma nenhuma decisão."""

    nome = "atuado"
    controla = False

    def __call__(self, env, obs):
        return {}


class Aleatorio:
    """Escolhe uma fase ao acaso a cada decisão (respeitando amarelo e verdes mínimo e máximo)."""

    nome = "aleatorio"
    controla = True

    def __init__(self, semente: int = 0):
        self.rng = np.random.default_rng(semente)

    def __call__(self, env, obs):
        return {a: int(self.rng.integers(env.n_acoes[a])) for a in env.agentes}


class MaxPressao:
    """Max-pressure: abre a fase com maior diferença entre veículos nas faixas de entrada e de saída."""

    nome = "max_pressao"
    controla = True

    def __call__(self, env, obs):
        return {a: int(np.argmax(env.pressoes(a))) for a in env.agentes}
