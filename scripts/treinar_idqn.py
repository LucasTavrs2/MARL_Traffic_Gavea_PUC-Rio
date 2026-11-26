"""Treina o IDQN (um Double DQN por semáforo) nos cenários de treino.

Três modos:
  python scripts/treinar_idqn.py --episodios 100                       treino novo
  python scripts/treinar_idqn.py --episodios 200 --continuar           continua do último episódio até o 200
  python scripts/treinar_idqn.py --episodios 200 --continuar melhor    continua a partir do melhor da validação
  python scripts/treinar_idqn.py --episodios 100 --recomecar           guarda o treino antigo e começa do zero

Salva na pasta de --saida (padrão resultados/idqn):
- treino.csv, validacao.csv        métricas por episódio
- ultimo.npz, melhor.npz           só as redes, para o avaliar.py
- checkpoint.npz                   tudo para continuar (redes, otimizador, memória de replay, contadores)
- checkpoint_melhor.npz            o mesmo, no episódio que teve o melhor resultado na validação
- colapsos/epNNNN.npz              redes de cada validação, depois do fim da exploração, que ficou acima do dobro do melhor resultado,
                                   para investigar com: python scripts/investigar.py --modelo <arquivo>

O checkpoint é gravado ao fim de cada episódio. Ctrl+C perde só o episódio em andamento.
"""
from __future__ import annotations

import argparse
import csv
import math
import shutil
import sys
import time
from collections import deque
from datetime import datetime
from statistics import median
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from marl_gavea.ambiente import AmbienteGavea, rodar_episodio  # noqa: E402
from marl_gavea.comum import (  # noqa: E402
    REDE, RESULTADOS, carregar_manifesto, impedir_suspensao, rotas_do_conjunto,
)
from marl_gavea.idqn import AgenteDQN, carregar_checkpoint, salvar_checkpoint, salvar_modelo  # noqa: E402

COLUNAS = [
    "episodio", "cenario", "escala", "epsilon", "recompensa", "perda_media", "q_medio", "segundos",
    "tempo_perdido_medio_s", "espera_media_s", "tempo_viagem_medio_s", "pct_concluidos",
    "veiculos", "concluidos", "nao_inseridos", "teleportes", "fila_media_veic", "trocas_de_fase",
    "trocas_forcadas", "trocas_por_espera",
    "tempo_perdido_carros_s", "tempo_perdido_motos_s", "tempo_perdido_onibus_s",
    "atraso_insercao_medio_s", "atraso_total_medio_s",
]
DELTA = 5


def criterio(resumo: dict) -> tuple[str, float]:
    """Métrica usada para escolher o melhor modelo: atraso total médio (tempo perdido + espera para
    entrar na rede); se faltar, tempo perdido médio; se faltar, fila média."""
    for chave in ("atraso_total_medio_s", "tempo_perdido_medio_s", "fila_media_veic"):
        valor = resumo.get(chave)
        if isinstance(valor, (int, float)) and math.isfinite(valor):
            return chave, float(valor)
    return "nenhuma", float("inf")


def estimar_restante(duracoes, faltam: int) -> float:
    """Segundos até o fim, pela média das últimas rodadas (episódio + validação, quando houve).

    Rodadas muito acima do normal (o computador suspendeu, por exemplo) contam como 5x a mediana,
    para não inflar a estimativa pelo resto do treino."""
    if not duracoes:
        return float("nan")
    tipico = median(duracoes)
    limpas = [min(d, 5 * tipico) for d in duracoes]
    return sum(limpas) / len(limpas) * faltam


def anexar_csv(caminho: Path, linha: dict) -> None:
    novo = not caminho.exists()
    with caminho.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS, extrasaction="ignore", restval="")
        if novo:
            w.writeheader()
        w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in linha.items()})


def cortar_csv(caminho: Path, ate_episodio: int, carimbo: str) -> None:
    """Ao continuar de um episódio anterior, tira do CSV as linhas posteriores (guardando uma cópia)."""
    if not caminho.exists():
        return
    with caminho.open(encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    manter = [l for l in linhas if int(l["episodio"]) <= ate_episodio]
    if len(manter) == len(linhas):
        return
    caminho.rename(caminho.with_name(f"{caminho.stem}_antes_de_{carimbo}.csv"))
    with caminho.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(manter)


def preparar_pasta(args, saida: Path, carimbo: str):
    """Decide entre treino novo, continuação ou recomeço. Devolve (agentes, episódio inicial, melhor)."""
    tem_treino = saida.exists() and any(saida.iterdir())
    if args.continuar and args.recomecar:
        raise SystemExit("Use --continuar ou --recomecar, não os dois.")

    if args.recomecar and tem_treino:
        arquivo = saida.with_name(f"{saida.name}_antigo_{carimbo}")
        saida.rename(arquivo)
        print(f"Treino anterior guardado em {arquivo}")
        tem_treino = False

    if args.continuar:
        nome = "checkpoint.npz" if args.continuar == "ultimo" else "checkpoint_melhor.npz"
        caminho = saida / nome
        if not caminho.exists():
            raise SystemExit(f"Não achei {caminho}. Não há treino para continuar nessa pasta.")
        agentes, info = carregar_checkpoint(caminho)
        feitos = int(info["episodio"])
        if feitos >= args.episodios:
            raise SystemExit(
                f"Esse checkpoint já tem {feitos} episódios. Para treinar mais, use --episodios maior, "
                f"ex.: --episodios {feitos + 50} --continuar {args.continuar}"
            )
        for campo in ("duracao", "recompensa"):
            if getattr(args, campo) != info[campo]:
                print(f"Aviso: usando {campo}={info[campo]} do treino original (você passou {getattr(args, campo)}).")
                setattr(args, campo, info[campo])
        for csv_nome in ("treino.csv", "validacao.csv"):
            cortar_csv(saida / csv_nome, feitos, carimbo)
        ep = next(iter(agentes.values()))
        print(f"Continuando do episódio {feitos} ({args.continuar}) | epsilon {ep.eps:.2f} | "
              f"memória {ep.tamanho} transições por agente")
        return agentes, feitos, float(info.get("melhor", float("inf")))

    if tem_treino:
        raise SystemExit(
            f"A pasta {saida} já tem um treino. Escolha:\n"
            f"  --continuar          segue do último episódio\n"
            f"  --continuar melhor   segue do melhor episódio da validação\n"
            f"  --recomecar          guarda esse treino com outro nome e começa do zero\n"
            f"  --saida outra_pasta  treino novo em outra pasta"
        )
    saida.mkdir(parents=True, exist_ok=True)
    return {}, 0, float("inf")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--episodios", type=int, default=100, help="total de episódios (contando os já feitos)")
    p.add_argument("--continuar", nargs="?", const="ultimo", choices=["ultimo", "melhor"],
                   help="continua um treino salvo (padrão: do último episódio)")
    p.add_argument("--recomecar", action="store_true", help="guarda o treino existente e começa do zero")
    p.add_argument("--duracao", type=int, default=4200, help="segundos simulados por episódio")
    p.add_argument("--recompensa", choices=["fila", "espera"], default="fila")
    p.add_argument("--avaliar-cada", type=int, default=5, help="avalia na validação a cada N episódios")
    p.add_argument("--validacao-escalas", nargs="+", type=float, default=[1.0, 1.5],
                   help="volumes da validação; o melhor modelo é o de menor média entre eles (padrão: 1.0 e 1.5)")
    p.add_argument("--exploracao", type=float, default=0.5,
                   help="fração do treino em que o epsilon cai de 1,0 até 0,05 (só vale em treino novo)")
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--gamma", type=float, default=0.95)
    p.add_argument("--alvo-cada", type=int, default=1000,
                   help="atualizações entre cópias da rede online para a rede-alvo")
    p.add_argument("--semente", type=int, default=0)
    p.add_argument("--saida", default=str(RESULTADOS / "idqn"))
    p.add_argument("--manter-xml", action="store_true", help="guarda os XML do SUMO de cada episódio de treino")
    args = p.parse_args()

    manifesto = carregar_manifesto()
    treino = rotas_do_conjunto(manifesto, "treino")
    validacao = rotas_do_conjunto(manifesto, "validacao")[0]
    saida = Path(args.saida)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    agentes, primeiro_ep, melhor = preparar_pasta(args, saida, carimbo)
    eps_passos = int(args.exploracao * args.episodios * (args.duracao // DELTA))
    estado = {"recompensa": 0.0, "perdas": [], "q": []}
    # recompensa "fila" fica entre -1 e 0, então Q fica entre -1/(1-gamma) e 0
    limites_q = (-1.0 / (1.0 - args.gamma), 0.0) if args.recompensa == "fila" else None

    def politica(env, obs):
        if not agentes:
            for i, a in enumerate(env.agentes):
                agentes[a] = AgenteDQN(
                    env.dim_obs[a], env.n_acoes[a], semente=args.semente * 1000 + i,
                    lr=args.lr, gamma=args.gamma, eps_passos=eps_passos,
                    alvo_cada=args.alvo_cada, limites_q=limites_q,
                )
            print(f"Agentes: {len(agentes)} | fixos no programa original: {env.fixos or 'nenhum'}")
        if not estado.get("conferido"):
            for a in env.agentes:
                ag = agentes.get(a)
                if ag is None or ag.dim != env.dim_obs[a] or ag.n_acoes != env.n_acoes[a]:
                    raise SystemExit(
                        f"O checkpoint não bate com a rede atual no semáforo '{a}'. "
                        "A rede ou o agrupamento de semáforos mudou: use --recomecar."
                    )
            estado["conferido"] = True
        return {a: agentes[a].agir(obs[a]) for a in env.agentes}

    def aprender(obs, acoes, recompensas, obs_seguinte):
        for a, acao in acoes.items():
            ag = agentes[a]
            ag.guardar(obs[a], acao, recompensas[a], obs_seguinte[a])
            perda = ag.treinar()
            if perda is not None:
                estado["perdas"].append(perda)
                estado["q"].append(ag.q_medio)
            estado["recompensa"] += recompensas[a]

    def gulosa(env, obs):
        return {a: agentes[a].agir(obs[a], explorar=False) for a in env.agentes}

    def info_checkpoint(ep_feitos):
        return {"episodio": ep_feitos, "melhor": melhor, "duracao": args.duracao,
                "recompensa": args.recompensa, "semente": args.semente}

    if impedir_suspensao():
        print("Suspensão automática do Windows bloqueada enquanto o treino roda (a tela ainda pode apagar).")
    duracoes = deque(maxlen=20)
    try:
        for ep in range(primeiro_ep, args.episodios):
            t_rodada = time.time()
            cenario = treino[ep % len(treino)]
            pasta_ep = saida / "episodios" / f"ep{ep + 1:03d}"
            env = AmbienteGavea(REDE, cenario["rotas"], duracao=args.duracao, delta=DELTA,
                                recompensa=args.recompensa, semente=ep, saida=pasta_ep)
            estado["recompensa"], estado["perdas"], estado["q"] = 0.0, [], []
            t0 = time.time()
            resumo = rodar_episodio(env, politica, aprender)
            segundos = time.time() - t0
            if not args.manter_xml:
                shutil.rmtree(pasta_ep, ignore_errors=True)

            epsilon = next(iter(agentes.values())).eps
            anexar_csv(saida / "treino.csv", {
                "episodio": ep + 1, "cenario": cenario["nome"], "epsilon": epsilon,
                "recompensa": estado["recompensa"],
                "perda_media": float(np.mean(estado["perdas"])) if estado["perdas"] else float("nan"),
                "q_medio": float(np.mean(estado["q"])) if estado["q"] else float("nan"),
                "segundos": segundos, **resumo,
            })
            perda_ep = float(np.mean(estado["perdas"])) if estado["perdas"] else float("nan")
            print(
                f"ep {ep + 1:3d}/{args.episodios} | {cenario['nome']} | eps {epsilon:.2f} | "
                f"perdido {resumo.get('tempo_perdido_medio_s', float('nan')):6.1f} s | "
                f"teleportes {resumo.get('teleportes', '?')} | perda {perda_ep:.3f} | {segundos:5.0f} s",
                flush=True,
            )
            if perda_ep > 10:
                print("   atenção: a perda passou de 10, sinal de que os valores Q estão divergindo", flush=True)

            melhorou = False
            if (ep + 1) % args.avaliar_cada == 0 or ep + 1 == args.episodios:
                valores = []
                for escala in args.validacao_escalas:
                    pasta_val = saida / "validacao" / f"ep{ep + 1:03d}"
                    if len(args.validacao_escalas) > 1:
                        pasta_val = pasta_val / f"volume_{escala:g}"
                    env_val = AmbienteGavea(REDE, validacao["rotas"], duracao=args.duracao, delta=DELTA,
                                            recompensa=args.recompensa, semente=validacao["semente"],
                                            saida=pasta_val, escala=escala)
                    r_val = rodar_episodio(env_val, gulosa)
                    anexar_csv(saida / "validacao.csv",
                               {"episodio": ep + 1, "cenario": validacao["nome"], "escala": escala, **r_val})
                    chave, v = criterio(r_val)
                    valores.append(v)
                valor = float(np.mean(valores))
                # só conta como colapso depois do fim da exploração: antes disso a oscilação é esperada
                ag0 = next(iter(agentes.values()))
                fim_exploracao = ag0.eps <= ag0.eps_final + 1e-9
                colapsou = fim_exploracao and math.isfinite(melhor) and valor > 2 * melhor
                melhorou = valor < melhor or not (saida / "melhor.npz").exists()
                if melhorou:
                    melhor = valor
                detalhe = ""
                if len(valores) > 1:
                    detalhe = " (" + ", ".join(f"{e * 100:.0f}%: {v:.1f}" for e, v in zip(args.validacao_escalas, valores)) + ")"
                print(f"   validação: {chave} = {valor:.1f}{detalhe}{'  <- melhor até agora' if melhorou else ''}",
                      flush=True)
                if colapsou:
                    arquivo = saida / "colapsos" / f"ep{ep + 1:04d}.npz"
                    salvar_modelo(arquivo, agentes, {"episodio": ep + 1, "validacao": valor})
                    print(f"   colapso na validação: redes guardadas em {arquivo}", flush=True)

            salvar_modelo(saida / "ultimo.npz", agentes, {"episodio": ep + 1})
            salvar_checkpoint(saida / "checkpoint.npz", agentes, info_checkpoint(ep + 1))
            if melhorou:
                salvar_modelo(saida / "melhor.npz", agentes, {"episodio": ep + 1, "melhor": melhor})
                shutil.copyfile(saida / "checkpoint.npz", saida / "checkpoint_melhor.npz")

            duracao_rodada = time.time() - t_rodada
            if len(duracoes) >= 5 and duracao_rodada > 10 * median(duracoes):
                print(f"   essa rodada levou {duracao_rodada / 60:.0f} min: o computador provavelmente suspendeu",
                      flush=True)
            duracoes.append(duracao_rodada)
            print(f"   faltam ~{estimar_restante(duracoes, args.episodios - ep - 1) / 60:.0f} min", flush=True)
    except KeyboardInterrupt:
        print(f"\nInterrompido. Para seguir de onde parou: "
              f"python scripts/treinar_idqn.py --episodios {args.episodios} --continuar --saida {saida}")
        return

    print(f"\nFim. Melhor resultado na validação: {melhor:.1f}. Arquivos em {saida}")


if __name__ == "__main__":
    main()
