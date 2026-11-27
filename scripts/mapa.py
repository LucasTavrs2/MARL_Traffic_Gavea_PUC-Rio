"""Mostra ou troca o mapa usado por todos os scripts.

A escolha fica no arquivo .mapa, na raiz do projeto, e vale para todos os terminais até ser
trocada. Só a variável MARL_MAPA, se estiver definida num terminal, passa na frente dela.

Uso:
  python scripts/mapa.py                  # mostra o mapa atual e os disponíveis
  python scripts/mapa.py gavea_ampliada   # passa a usar o mapa ampliado
  python scripts/mapa.py gavea_real       # volta ao MVP
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PADRAO = "gavea_real"  # o mesmo de marl_gavea.comum (não importa de lá: ela recusa mapa inválido)
ARQ = Path(os.environ.get("MARL_ARQ_MAPA") or RAIZ / ".mapa")


def disponiveis() -> list[str]:
    pasta = RAIZ / "maps"
    return sorted(p.name for p in pasta.iterdir() if p.is_dir()) if pasta.is_dir() else []


def atual() -> str:
    try:
        return ARQ.read_text(encoding="utf-8").strip() or PADRAO
    except OSError:
        return PADRAO


def main(argv: list[str]) -> int:
    variavel = os.environ.get("MARL_MAPA", "").strip()
    if argv:
        nome = argv[0].strip().strip("/\\")
        if nome not in disponiveis():
            print(f"Não existe maps/{nome}. Mapas: {', '.join(disponiveis())}")
            return 1
        if nome == PADRAO:
            ARQ.unlink(missing_ok=True)
        else:
            ARQ.write_text(nome + "\n", encoding="utf-8")
        print(f"Mapa escolhido: {nome}{' (MVP)' if nome == PADRAO else ''}. Vale para todos os terminais.")
    else:
        print(f"Mapa escolhido: {atual()}. Disponíveis: {', '.join(disponiveis())}")
    if variavel:
        print(f"Atenção: neste terminal a variável MARL_MAPA={variavel} tem prioridade. "
              "Para usar a escolha acima, rode: Remove-Item Env:MARL_MAPA")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
