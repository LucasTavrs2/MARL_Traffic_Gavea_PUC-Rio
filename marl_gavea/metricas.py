"""Leitura das saídas do SUMO (tripinfo e statistic-output) e resumo das métricas de um episódio."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# O tipo sai do prefixo do id que o gerar_demanda.py dá a cada veículo (carro_12, moto_3, onibus_7);
# se o id não tiver um desses prefixos, tenta pela classe do vType.
PREFIXOS = {"carro_": "carros", "moto_": "motos", "onibus_": "onibus"}
CLASSES = {"passenger": "carros", "motorcycle": "motos", "bus": "onibus"}
TIPOS = {"carros": "carros", "motos": "motos", "onibus": "onibus"}


def _tipo(id_veiculo: str, vtype: str) -> str:
    for prefixo, nome in PREFIXOS.items():
        if id_veiculo.startswith(prefixo):
            return nome
    return CLASSES.get(vtype, "outros")


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
                    "tipo": _tipo(el.get("id", ""), el.get("vType", "")),
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
        "_soma_atraso": sum(v["perdido"] + v["atraso_insercao"] for v in viagens),
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
    viagens = raiz.find("vehicleTripStatistics")
    if viagens is not None:
        # Espera média dos veículos que ainda aguardavam para entrar na rede no fim (-1 se não havia nenhum).
        espera = _num(viagens.get("departDelayWaiting"), -1.0)
        if espera >= 0:
            resumo["_espera_nao_inseridos"] = espera
    return resumo


def resumo_saida(pasta: Path) -> dict:
    pasta = Path(pasta)
    resumo = {}
    if (pasta / "tripinfo.xml").exists():
        resumo.update(ler_tripinfo(pasta / "tripinfo.xml"))
    if (pasta / "estatisticas.xml").exists():
        resumo.update(ler_estatisticas(pasta / "estatisticas.xml"))
    _atraso_total(resumo)
    return resumo


def _atraso_total(resumo: dict) -> None:
    """Atraso total por veículo gerado: tempo perdido na rede mais a espera para entrar nela.

    O tempo perdido do SUMO (timeLoss) não conta a espera na entrada (departDelay), nem os veículos
    que não chegaram a entrar. Um controle que empurra a fila até a borda da rede parece melhor do
    que é. Aqui entram os dois: os inseridos com tempo perdido + atraso de inserção, e os que ficaram
    de fora com a espera que tinham no fim (departDelayWaiting do statistic-output; sem ela, conta 0,
    e o valor fica como limite inferior)."""
    soma = resumo.pop("_soma_atraso", None)
    espera_fora = resumo.pop("_espera_nao_inseridos", 0.0)
    if soma is None or not resumo.get("veiculos"):
        return
    fora = resumo.get("nao_inseridos", 0)
    resumo["atraso_total_medio_s"] = (soma + fora * espera_fora) / (resumo["veiculos"] + fora)
