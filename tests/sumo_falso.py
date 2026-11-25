"""Simulador falso com a mesma interface do TraCI usada pelo ambiente.

Um cruzamento 'cruz' com duas entradas (norte e leste) e fases "Gr"/"rG", e uma travessia 'fixo'
com uma só fase verde (não deve virar agente). Cada entrada recebe veículos com probabilidade
fixa por segundo e descarrega um veículo por segundo quando o sinal está verde.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np


class SumoFalso:
    def __init__(self, chegadas=(0.30, 0.10), semente=0):
        self.rng = np.random.default_rng(semente)
        self.t = 0.0
        self.chegadas = {"norte_in": chegadas[0], "leste_in": chegadas[1]}
        self.n = {"norte_in": 0, "leste_in": 0, "norte_out": 0, "leste_out": 0, "faixa_x": 0, "faixa_y": 0}
        self.espera = {k: 0.0 for k in self.n}
        self.estados = {"cruz": "Gr", "fixo": "G"}
        self.historico = []  # (tempo, tl, estado) a cada mudança feita pelo controlador
        self.fechado = False
        self.trafficlight = _TL(self)
        self.lane = _Lane(self)
        self.simulation = SimpleNamespace(getTime=lambda: self.t)

    def simulationStep(self):
        self.t += 1
        for faixa, p in self.chegadas.items():
            if self.rng.random() < p:
                self.n[faixa] += 1
        for i, (entrada, saida) in enumerate([("norte_in", "norte_out"), ("leste_in", "leste_out")]):
            if self.estados["cruz"][i] in "Gg" and self.n[entrada] > 0:
                self.n[entrada] -= 1
                self.n[saida] += 1
        for saida in ("norte_out", "leste_out"):
            if self.n[saida] > 0 and self.rng.random() < 0.8:
                self.n[saida] -= 1
        for faixa in ("norte_in", "leste_in"):
            self.espera[faixa] += self.n[faixa]

    def close(self):
        self.fechado = True


class _TL:
    LOGICAS = {
        "cruz": ["Gr", "yr", "rG", "ry"],
        "fixo": ["G", "y", "r"],
    }
    LINKS = {
        "cruz": [[("norte_in", "norte_out", "via0")], [("leste_in", "leste_out", "via1")]],
        "fixo": [[("faixa_x", "faixa_y", "via2")]],
    }

    def __init__(self, sim):
        self.sim = sim

    def getIDList(self):
        return list(self.LOGICAS)

    def getProgram(self, tl):
        return "0"

    def getAllProgramLogics(self, tl):
        fases = [SimpleNamespace(state=s, duration=30) for s in self.LOGICAS[tl]]
        return [SimpleNamespace(programID="0", phases=fases)]

    def getControlledLinks(self, tl):
        return self.LINKS[tl]

    def setRedYellowGreenState(self, tl, estado):
        self.sim.estados[tl] = estado
        self.sim.historico.append((self.sim.t, tl, estado))

    def getRedYellowGreenState(self, tl):
        return self.sim.estados[tl]


class _Lane:
    def __init__(self, sim):
        self.sim = sim

    def getLength(self, faixa):
        return 75.0  # cabem 10 veículos

    def getLastStepVehicleNumber(self, faixa):
        return self.sim.n[faixa]

    def getLastStepHaltingNumber(self, faixa):
        return self.sim.n[faixa]

    def getWaitingTime(self, faixa):
        return self.sim.espera[faixa]
