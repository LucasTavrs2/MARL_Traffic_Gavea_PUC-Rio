"""Coordenadas da rede do SUMO (metros) para latitude e longitude, sem pyproj.

O netconvert projeta o OSM em UTM e desloca tudo pelo netOffset: x_rede = x_utm + netOffset.
A volta é a inversa da projeção UTM (fórmulas de Snyder, erro bem abaixo de 1 m na zona).

Interpolar linearmente entre convBoundary e origBoundary não serve: o origBoundary cobre todos os
pontos lidos do OSM, inclusive os de fora do recorte, e o convBoundary só o que sobrou depois de
cortar. Com recortes grandes isso estica as coordenadas em centenas de metros.
"""
from __future__ import annotations

import math
import re

A = 6378137.0  # WGS84
F = 1 / 298.257223563
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)
K0 = 0.9996


def _meridiano(phi: float) -> float:
    """Comprimento do arco de meridiano do equador até a latitude phi (radianos)."""
    e4, e6 = E2 * E2, E2 * E2 * E2
    return A * ((1 - E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                - (3 * E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                - (35 * e6 / 3072) * math.sin(6 * phi))


def utm_para_lat_lon(leste: float, norte: float, zona: int, sul: bool) -> tuple[float, float]:
    x = leste - 500000.0
    y = norte - (10000000.0 if sul else 0.0)
    mu = y / K0 / (A * (1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = EP2 * c * c, t * t
    n1 = A / math.sqrt(1 - E2 * s * s)
    r1 = A * (1 - E2) / (1 - E2 * s * s) ** 1.5
    d = x / (n1 * K0)
    lat = phi1 - (n1 * t / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * EP2) * d ** 4 / 24
                                  + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * EP2 + 24 * t1 ** 2) * d ** 5 / 120) / c
    lon0 = math.radians((zona - 1) * 6 - 180 + 3)
    return math.degrees(lat), math.degrees(lon0 + lon)


def lat_lon_para_utm(lat: float, lon: float, zona: int) -> tuple[float, float]:
    """Projeção direta (para testes e conferência)."""
    phi, lam = math.radians(lat), math.radians(lon)
    lam0 = math.radians((zona - 1) * 6 - 180 + 3)
    s, c, t = math.sin(phi), math.cos(phi), math.tan(phi)
    n = A / math.sqrt(1 - E2 * s * s)
    tt, cc, aa = t * t, EP2 * c * c, c * (lam - lam0)
    leste = K0 * n * (aa + (1 - tt + cc) * aa ** 3 / 6 + (5 - 18 * tt + tt * tt + 72 * cc - 58 * EP2) * aa ** 5 / 120)
    norte = K0 * (_meridiano(phi) + n * t * (aa ** 2 / 2 + (5 - tt + 9 * cc + 4 * cc * cc) * aa ** 4 / 24
                                            + (61 - 58 * tt + tt * tt + 600 * cc - 330 * EP2) * aa ** 6 / 720))
    return leste + 500000.0, norte + (10000000.0 if lat < 0 else 0.0)


class Projecao:
    """Converte (x, y) da rede em (lat, lon), a partir do <location> do .net.xml."""

    def __init__(self, deslocamento=None, conv=None, orig=None, proj: str = ""):
        self.deslocamento, self.conv, self.orig = deslocamento, conv, orig
        zona = re.search(r"\+zone=(\d+)", proj or "")
        self.zona = int(zona.group(1)) if zona and "+proj=utm" in proj else None
        self.sul = "+south" in (proj or "")

    @classmethod
    def do_location(cls, el) -> "Projecao":
        def lista(nome):
            try:
                return [float(v) for v in el.get(nome).split(",")]
            except (AttributeError, ValueError):
                return None
        return cls(lista("netOffset"), lista("convBoundary"), lista("origBoundary"), el.get("projParameter", ""))

    def para_lat_lon(self, x: float, y: float) -> tuple[float, float] | None:
        if self.zona is not None and self.deslocamento:
            return utm_para_lat_lon(x - self.deslocamento[0], y - self.deslocamento[1], self.zona, self.sul)
        # sem projeção conhecida (redes de teste): aproximação linear entre os limites
        conv, orig = self.conv, self.orig
        if not conv or not orig or conv[2] == conv[0] or conv[3] == conv[1]:
            return None
        lon = orig[0] + (x - conv[0]) / (conv[2] - conv[0]) * (orig[2] - orig[0])
        lat = orig[1] + (y - conv[1]) / (conv[3] - conv[1]) * (orig[3] - orig[1])
        return lat, lon
