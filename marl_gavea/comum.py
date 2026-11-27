"""Caminhos do projeto e localização do SUMO.

O mapa vem da variável de ambiente MARL_MAPA (pasta em maps/; padrão: gavea_real, o MVP).
Cada mapa tem a própria demanda e os próprios resultados, para um não sobrescrever o outro:
  gavea_real      -> demanda/              resultados/
  outro_mapa      -> demanda_outro_mapa/   resultados/outro_mapa/
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MAPA_PADRAO = "gavea_real"
MAPA = os.environ.get("MARL_MAPA", "").strip() or MAPA_PADRAO
PASTA_MAPA = RAIZ / "maps" / MAPA
if not PASTA_MAPA.is_dir():
    disponiveis = sorted(p.name for p in (RAIZ / "maps").iterdir() if p.is_dir()) if (RAIZ / "maps").is_dir() else []
    raise SystemExit(f"MARL_MAPA={MAPA}, mas não existe a pasta {PASTA_MAPA}. Mapas: {', '.join(disponiveis)}")
REDE = PASTA_MAPA / "gavea.net.xml"
OSM = PASTA_MAPA / "gavea.osm.xml"
DEMANDA = RAIZ / "demanda" if MAPA == MAPA_PADRAO else RAIZ / f"demanda_{MAPA}"
RESULTADOS = RAIZ / "resultados" if MAPA == MAPA_PADRAO else RAIZ / "resultados" / MAPA
MANIFESTO = DEMANDA / "manifesto.json"
if MAPA != MAPA_PADRAO:
    print(f"[mapa: {MAPA}]", flush=True)


def preparar_sumo() -> Path:
    """Usa o SUMO instalado no ambiente Python ativo (pacote eclipse-sumo).

    Ele tem prioridade sobre a variável SUMO_HOME do sistema, que pode apontar para outra
    instalação (por exemplo, um ambiente conda antigo com outra versão do SUMO).
    """
    try:
        import sumo  # pacote eclipse-sumo

        home = str(sumo.SUMO_HOME)
        os.environ["SUMO_HOME"] = home
    except ImportError:
        home = os.environ.get("SUMO_HOME")
        if not home:
            raise RuntimeError(
                "SUMO não encontrado. Ative o .venv e rode: pip install -r requirements.txt"
            )
    ferramentas = Path(home) / "tools"
    if str(ferramentas) not in sys.path:
        sys.path.append(str(ferramentas))
    return Path(home)


def binario_sumo(gui: bool = False) -> str:
    preparar_sumo()
    import sumolib

    return sumolib.checkBinary("sumo-gui" if gui else "sumo")


def carregar_manifesto() -> dict:
    if not MANIFESTO.exists():
        raise SystemExit(
            "Demanda não encontrada. Rode antes: python scripts/gerar_demanda.py"
        )
    return json.loads(MANIFESTO.read_text(encoding="utf-8"))


def rotas_do_conjunto(manifesto: dict, nome: str) -> list[dict]:
    """Devolve a lista de cenários ({nome, rotas, escala}) de um conjunto: treino, validacao ou teste."""
    if nome not in manifesto:
        raise SystemExit(f"Conjunto '{nome}' não existe no manifesto. Opções: {list(manifesto)}")
    cenarios = []
    for c in manifesto[nome]:
        cenarios.append({**c, "rotas": [str(DEMANDA / r) for r in c["rotas"]]})
    return cenarios


class _Espelho:
    """Escreve no terminal e num arquivo ao mesmo tempo."""

    def __init__(self, original, arquivo):
        self.original, self.arquivo = original, arquivo

    def write(self, texto):
        self.original.write(texto)
        self.arquivo.write(texto)
        return len(texto)

    def flush(self):
        self.original.flush()
        self.arquivo.flush()

    def __getattr__(self, nome):
        return getattr(self.original, nome)


def espelhar_saida(caminho: Path) -> Path:
    """Daqui em diante, tudo o que o script imprimir também vai para `caminho`."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = _Espelho(sys.stdout, caminho.open("a", encoding="utf-8"))
    return caminho


def impedir_suspensao() -> bool:
    """No Windows, pede ao sistema para não suspender por inatividade enquanto este processo roda.

    A tela ainda pode apagar. Vale até o processo terminar. Não impede a suspensão ao fechar a tampa
    do notebook nem a pedida pelo menu Iniciar. Em outros sistemas não faz nada. Devolve True se
    o pedido foi aceito (confira com `powercfg /requests`: o python aparece em SYSTEM)."""
    if sys.platform != "win32":
        return False
    import ctypes

    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
