"""Gera os cenários de demanda sintética (carros, motos e ônibus) com o randomTrips do SUMO.

Conjuntos:
- treino: cada cenário tem uma semente e uma escala de volume sorteada entre --treino-min e
  --treino-max (padrão 0,6 a 1,6), para o agente aprender também com trânsito leve e pesado;
- validacao: usado durante o treino para guardar o melhor modelo;
- teste: só para a comparação final, o agente nunca treina nele.

Todos os volumes são multiplicados por --escala-global, calibrada com o diagnosticar.py para a
rede escoar. Por padrão, origens e destinos favorecem as vias com mais faixas e mais rápidas.

A proporção de veículos é provisória (cerca de 80% carros, 15% motos, 5% ônibus) até chegarem
os dados de contagem.

Uso:  python scripts/gerar_demanda.py
      python scripts/gerar_demanda.py --escala-global 0.55
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from marl_gavea.comum import DEMANDA, MANIFESTO, REDE, preparar_sumo  # noqa: E402

# classe do veículo, prefixo dos ids, intervalo médio em segundos entre veículos na escala 1,0
TIPOS = [
    ("passenger", "carro", 2.5),
    ("motorcycle", "moto", 12.0),
    ("bus", "onibus", 40.0),
]


def gerar_cenario(nome: str, semente: int, escala: float, duracao: int, sumo_home: Path, ponderar: bool) -> dict:
    pasta = DEMANDA / nome
    pasta.mkdir(parents=True, exist_ok=True)
    random_trips = sumo_home / "tools" / "randomTrips.py"
    ambiente = {**os.environ, "SUMO_HOME": str(sumo_home)}
    rotas = []
    for k, (classe, prefixo, periodo) in enumerate(TIPOS):
        saida = pasta / f"{prefixo}.rou.xml"
        cmd = [
            sys.executable, str(random_trips),
            "-n", str(REDE),
            "-r", str(saida),
            "-o", str(pasta / f"{prefixo}.trips.xml"),
            "-b", "0",
            "-e", str(duracao),
            "-p", f"{periodo / escala:.3f}",
            "--fringe-factor", "10",
            "--min-distance", "300",
            "--seed", str(semente * 10 + k),
            "--prefix", f"{prefixo}_",
            "--edge-permission", classe,
            "--vehicle-class", classe,
            "--trip-attributes", 'departLane="best" departSpeed="max"',
            "--validate",
        ]
        if ponderar:
            # favorece origens e destinos em vias com mais faixas e mais rápidas (as avenidas),
            # em vez de espalhar viagens igualmente por ruas residenciais
            cmd += ["-L", "--speed-exponent", "2"]
        r = subprocess.run(cmd, env=ambiente, capture_output=True, text=True)
        if r.returncode != 0 and "edge-permission" in (r.stderr or ""):
            # versões antigas do randomTrips só conhecem o nome antigo da opção
            cmd[cmd.index("--edge-permission")] = "--vclass"
            r = subprocess.run(cmd, env=ambiente, capture_output=True, text=True)
        if r.returncode != 0 or not saida.exists():
            print(r.stdout[-2000:])
            print(r.stderr[-2000:])
            raise SystemExit(f"randomTrips falhou em {nome}/{prefixo}")
        rotas.append(f"{nome}/{saida.name}")
    return {"nome": nome, "semente": semente, "escala": round(escala, 3), "rotas": rotas}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--treino", type=int, default=20, help="número de cenários de treino")
    p.add_argument("--treino-min", type=float, default=0.6, help="menor volume de treino (fração do calibrado)")
    p.add_argument("--treino-max", type=float, default=1.6, help="maior volume de treino (fração do calibrado)")
    p.add_argument("--validacao", type=int, default=1)
    p.add_argument("--teste", type=int, default=5)
    p.add_argument("--duracao", type=int, default=3600, help="segundos com chegada de veículos")
    p.add_argument("--semente", type=int, default=0)
    p.add_argument("--escala-global", type=float, default=None,
                   help="multiplica o volume de todos os cenários; use o valor sugerido pelo diagnosticar.py "
                        "(se omitido, repete o da demanda atual, ou 1,0 se ainda não houver)")
    p.add_argument("--sem-ponderar", action="store_true",
                   help="sorteia origens e destinos sem favorecer as avenidas (comportamento antigo)")
    args = p.parse_args()

    if not REDE.exists():
        raise SystemExit(f"Rede não encontrada: {REDE}")
    if args.escala_global is None:
        anterior = json.loads(MANIFESTO.read_text(encoding="utf-8")) if MANIFESTO.exists() else {}
        args.escala_global = float(anterior.get("escala_global", 1.0))
        print(f"Escala global {args.escala_global:g} (a mesma da demanda atual; mude com --escala-global)")
    sumo_home = preparar_sumo()
    rng = np.random.default_rng(args.semente)
    ponderar = not args.sem_ponderar
    manifesto = {"duracao_demanda": args.duracao, "escala_global": args.escala_global,
                 "ponderar_vias": ponderar, "volume_treino": [args.treino_min, args.treino_max],
                 "treino": [], "validacao": [], "teste": []}

    g = args.escala_global
    planos = (
        [("treino", f"treino_s{i:03d}", i, g * float(rng.uniform(args.treino_min, args.treino_max)))
         for i in range(1, args.treino + 1)]
        + [("validacao", f"validacao_s{50 + i:03d}", 50 + i, g) for i in range(1, args.validacao + 1)]
        + [("teste", f"teste_s{100 + i:03d}", 100 + i, g) for i in range(1, args.teste + 1)]
    )
    for conjunto, nome, semente, escala in planos:
        print(f"Gerando {nome} (escala {escala:.2f})...", flush=True)
        manifesto[conjunto].append(gerar_cenario(nome, semente, escala, args.duracao, sumo_home, ponderar))

    MANIFESTO.write_text(json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPronto: {len(planos)} cenários em {DEMANDA}")


if __name__ == "__main__":
    main()
