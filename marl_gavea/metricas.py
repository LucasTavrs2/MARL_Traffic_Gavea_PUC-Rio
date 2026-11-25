"""Leitura das saídas do SUMO (tripinfo e statistic-output) e resumo das métricas de um episódio."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# vType criado pelo randomTrips --vehicle-class: o id é o nome da classe.
TIPOS = {"passenger": "carros", "motorcycle": "motos", "bus": "onibus"}


def _num(valor, padrao=0.0) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def _media(valores) -> float:
    valores = list(valores)
    return sum(valores) / len(valores) if valores else float("nan")


def ler_tripinfo(caminho: Path) -> dict:
    viagens = []
    for _, el in ET.iterparse(caminho, events=("end",)):
        if el.tag == "tripinfo":
            viagens.append(
                {
                    "tipo": el.get("vType", ""),
                    "chegada": _num(el.get("arrival"), -1.0),
                    "duracao": _num(el.get("duration")),
                    "perdido": _num(el.get("timeLoss")),
                    "espera": _num(el.get("waitingTime")),
                    "atraso_insercao": _num(el.get("departDelay")),
                }
            )
            el.clear()
    concluidas = [v for v in viagens if v["chegada"] >= 0]
    resumo = {
        "veiculos": len(viagens),
        "concluidos": len(concluidas),
        "pct_concluidos": 100.0 * len(concluidas) / len(viagens) if viagens else float("nan"),
        "tempo_perdido_medio_s": _media(v["perdido"] for v in viagens),
        "espera_media_s": _media(v["espera"] for v in viagens),
        "tempo_viagem_medio_s": _media(v["duracao"] for v in concluidas),
        "atraso_insercao_medio_s": _media(v["atraso_insercao"] for v in viagens),
    }
    for tipo, nome in TIPOS.items():
        resumo[f"tempo_perdido_{nome}_s"] = _media(v["perdido"] for v in viagens if v["tipo"] == tipo)
    return resumo


def ler_estatisticas(caminho: Path) -> dict:
    raiz = ET.parse(caminho).getroot()
    veiculos = raiz.find("vehicles")
    teleportes = raiz.find("teleports")
    resumo = {}
    if veiculos is not None:
        carregados = _num(veiculos.get("loaded"))
        inseridos = _num(veiculos.get("inserted"))
        resumo["nao_inseridos"] = int(carregados - inseridos)
    if teleportes is not None:
        resumo["teleportes"] = int(_num(teleportes.get("total")))
    return resumo


def resumo_saida(pasta: Path) -> dict:
    pasta = Path(pasta)
    resumo = {}
    if (pasta / "tripinfo.xml").exists():
        resumo.update(ler_tripinfo(pasta / "tripinfo.xml"))
    if (pasta / "estatisticas.xml").exists():
        resumo.update(ler_estatisticas(pasta / "estatisticas.xml"))
    return resumo
