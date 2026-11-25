"""Zonas de detecção: o trecho de via que cada semáforo "enxerga".

O OSM corta as vias em pedaços nas faixas de pedestre, então a faixa que chega ao semáforo muitas
vezes tem 5 a 20 m e só cabe 1 ou 2 carros. Olhar só para ela esconde a fila de verdade, que fica
nos trechos de trás. Aqui cada faixa de entrada ganha uma zona que sobe a via até cerca de
`alcance` metros, parando antes de outro semáforo; cada faixa de saída ganha uma zona que desce a
via até o próximo semáforo ou até o alcance. É o equivalente a um laço detector longo.

A rede é lida direto do XML (conexões entre faixas), sem TraCI.
"""
from __future__ import annotations

import collections
import xml.etree.ElementTree as ET
from pathlib import Path

ALCANCE_PADRAO = 100.0


class Zonas:
    def __init__(self, rede: str | Path):
        self.comprimento: dict[str, float] = {}
        self.anteriores: dict[str, set[str]] = collections.defaultdict(set)
        self.seguintes: dict[str, set[str]] = collections.defaultdict(set)
        self.controladas: set[str] = set()  # faixas que terminam num semáforo
        for _, el in ET.iterparse(str(rede), events=("end",)):
            if el.tag == "edge":
                if el.get("function") != "internal":
                    for faixa in el.findall("lane"):
                        self.comprimento[faixa.get("id")] = float(faixa.get("length", "0"))
                el.clear()
            elif el.tag == "connection":
                de, para = el.get("from", ""), el.get("to", "")
                if not de.startswith(":") and not para.startswith(":"):
                    a, b = f"{de}_{el.get('fromLane')}", f"{para}_{el.get('toLane')}"
                    self.seguintes[a].add(b)
                    self.anteriores[b].add(a)
                    if el.get("tl"):
                        self.controladas.add(a)
            elif el.tag in ("junction", "tlLogic"):
                el.clear()

    def _percorrer(self, inicio: str, vizinhos: dict, alcance: float, incluir, expandir) -> list[str]:
        zona = [inicio]
        vistos = {inicio}
        pendentes = collections.deque([(inicio, self.comprimento.get(inicio, 0.0))])
        while pendentes:
            faixa, distancia = pendentes.popleft()
            if distancia >= alcance or (faixa != inicio and not expandir(faixa)):
                continue
            for v in sorted(vizinhos.get(faixa, ())):
                if v in vistos or v not in self.comprimento or not incluir(v):
                    continue
                vistos.add(v)
                zona.append(v)
                pendentes.append((v, distancia + self.comprimento[v]))
        return zona

    def montante(self, faixa: str, alcance: float = ALCANCE_PADRAO) -> list[str]:
        """Faixa de entrada e os trechos antes dela, sem passar por outro semáforo."""
        return self._percorrer(faixa, self.anteriores, alcance,
                               incluir=lambda f: f not in self.controladas,
                               expandir=lambda f: True)

    def jusante(self, faixa: str, alcance: float = ALCANCE_PADRAO) -> list[str]:
        """Faixa de saída e os trechos depois dela, até a chegada do próximo semáforo."""
        if faixa in self.controladas:
            return [faixa]
        return self._percorrer(faixa, self.seguintes, alcance,
                               incluir=lambda f: True,
                               expandir=lambda f: f not in self.controladas)

    def tamanho(self, zona: list[str]) -> float:
        return sum(self.comprimento.get(f, 0.0) for f in zona)


_CACHE: dict[str, Zonas] = {}


def zonas_da_rede(rede: str | Path) -> Zonas | None:
    """Lê a rede uma vez por processo. Devolve None se o arquivo não existir (testes sem SUMO)."""
    chave = str(Path(rede).resolve()) if Path(rede).exists() else None
    if chave is None:
        return None
    if chave not in _CACHE:
        _CACHE[chave] = Zonas(chave)
    return _CACHE[chave]
