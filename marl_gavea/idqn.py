"""IDQN: um Double DQN independente por semáforo, implementado em numpy.

As redes são pequenas (duas camadas ocultas de 64), então numpy basta e evita instalar o PyTorch.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from marl_gavea.comum import MAPA


class MLP:
    def __init__(self, tamanhos, rng):
        self.tamanhos = list(tamanhos)
        self.params = []
        for entrada, saida in zip(self.tamanhos[:-1], self.tamanhos[1:]):
            self.params.append((rng.standard_normal((entrada, saida)) * np.sqrt(2.0 / entrada)).astype(np.float64))
            self.params.append(np.zeros(saida, dtype=np.float64))

    @property
    def n_camadas(self) -> int:
        return len(self.params) // 2

    def forward(self, x):
        cache = []
        h = np.asarray(x, dtype=np.float64)
        for l in range(self.n_camadas):
            z = h @ self.params[2 * l] + self.params[2 * l + 1]
            cache.append((h, z))
            h = np.maximum(z, 0.0) if l < self.n_camadas - 1 else z
        return h, cache

    def backward(self, d_saida, cache):
        grads = [None] * len(self.params)
        d = d_saida
        for l in reversed(range(self.n_camadas)):
            h, z = cache[l]
            if l < self.n_camadas - 1:
                d = d * (z > 0)
            grads[2 * l] = h.T @ d
            grads[2 * l + 1] = d.sum(axis=0)
            d = d @ self.params[2 * l].T
        return grads

    def copiar_de(self, outra: "MLP") -> None:
        self.params = [p.copy() for p in outra.params]


class Adam:
    def __init__(self, params, lr=5e-4, b1=0.9, b2=0.999, eps=1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, b1, b2, eps
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]
        self.t = 0

    def passo(self, params, grads) -> None:
        self.t += 1
        for i, (p, g) in enumerate(zip(params, grads)):
            self.m[i] = self.b1 * self.m[i] + (1 - self.b1) * g
            self.v[i] = self.b2 * self.v[i] + (1 - self.b2) * g * g
            m_hat = self.m[i] / (1 - self.b1 ** self.t)
            v_hat = self.v[i] / (1 - self.b2 ** self.t)
            p -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


class AgenteDQN:
    def __init__(
        self,
        dim: int,
        n_acoes: int,
        *,
        semente: int = 0,
        oculto=(64, 64),
        lr: float = 1e-4,
        gamma: float = 0.95,
        memoria: int = 50_000,
        lote: int = 64,
        aquecimento: int = 1_000,
        alvo_cada: int = 1_000,
        eps_inicial: float = 1.0,
        eps_final: float = 0.05,
        eps_passos: int = 50_000,
        clip_grad: float = 10.0,
        limites_q=None,
    ):
        """limites_q: faixa em que o valor Q verdadeiro tem de estar (com recompensa entre -1 e 0 e
        desconto gamma, Q fica entre -1/(1-gamma) e 0). Os alvos do aprendizado são cortados nessa
        faixa, o que impede a rede de divergir para valores absurdos."""
        self.limites_q = tuple(limites_q) if limites_q is not None else None
        self.q_medio = float("nan")
        self.dim, self.n_acoes, self.oculto = dim, n_acoes, tuple(oculto)
        self.lr = lr
        self.gamma, self.lote, self.aquecimento, self.alvo_cada = gamma, lote, aquecimento, alvo_cada
        self.eps_inicial, self.eps_final, self.eps_passos = eps_inicial, eps_final, max(1, eps_passos)
        self.clip_grad = clip_grad
        self.rng = np.random.default_rng(semente)
        self.q = MLP([dim, *self.oculto, n_acoes], self.rng)
        self.alvo = MLP([dim, *self.oculto, n_acoes], self.rng)
        self.alvo.copiar_de(self.q)
        self.otimizador = Adam(self.q.params, lr=lr)
        self.S = np.zeros((memoria, dim), dtype=np.float32)
        self.A = np.zeros(memoria, dtype=np.int64)
        self.R = np.zeros(memoria, dtype=np.float32)
        self.S2 = np.zeros((memoria, dim), dtype=np.float32)
        self.ponteiro = 0
        self.tamanho = 0
        self.passos = 0
        self.atualizacoes = 0

    @property
    def eps(self) -> float:
        fracao = min(1.0, self.passos / self.eps_passos)
        return self.eps_inicial + fracao * (self.eps_final - self.eps_inicial)

    def agir(self, obs, explorar: bool = True) -> int:
        if explorar and self.rng.random() < self.eps:
            return int(self.rng.integers(self.n_acoes))
        q, _ = self.q.forward(np.asarray(obs)[None, :])
        return int(np.argmax(q[0]))

    def guardar(self, s, a, r, s2) -> None:
        i = self.ponteiro
        self.S[i], self.A[i], self.R[i], self.S2[i] = s, a, r, s2
        self.ponteiro = (i + 1) % len(self.A)
        self.tamanho = min(self.tamanho + 1, len(self.A))
        self.passos += 1

    def treinar(self):
        if self.tamanho < max(self.lote, self.aquecimento):
            return None
        idx = self.rng.integers(0, self.tamanho, self.lote)
        s, a, r, s2 = self.S[idx], self.A[idx], self.R[idx], self.S2[idx]
        linhas = np.arange(self.lote)

        q_s2, _ = self.q.forward(s2)
        melhor = np.argmax(q_s2, axis=1)  # Double DQN: a rede online escolhe, a alvo avalia
        q_alvo, _ = self.alvo.forward(s2)
        y = r + self.gamma * q_alvo[linhas, melhor]
        if self.limites_q is not None:
            y = np.clip(y, *self.limites_q)

        q, cache = self.q.forward(s)
        erro = q[linhas, a] - y
        self.q_medio = float(np.mean(q[linhas, a]))
        d_q = np.zeros_like(q)
        d_q[linhas, a] = np.clip(erro, -1.0, 1.0) / self.lote  # derivada da perda de Huber
        grads = self.q.backward(d_q, cache)
        norma = np.sqrt(sum(float((g * g).sum()) for g in grads))
        if norma > self.clip_grad:
            grads = [g * (self.clip_grad / norma) for g in grads]
        self.otimizador.passo(self.q.params, grads)

        self.atualizacoes += 1
        if self.atualizacoes % self.alvo_cada == 0:
            self.alvo.copiar_de(self.q)
        abs_erro = np.abs(erro)
        return float(np.mean(np.where(abs_erro < 1, 0.5 * erro ** 2, abs_erro - 0.5)))

    # ------------------------------------------------------------------ checkpoint completo
    def para_checkpoint(self, prefixo: str) -> tuple[dict, dict]:
        """Tudo o que é preciso para continuar o treino exatamente de onde parou."""
        arrays = {}
        for j, p in enumerate(self.q.params):
            arrays[f"{prefixo}q{j}"] = p
        for j, p in enumerate(self.alvo.params):
            arrays[f"{prefixo}alvo{j}"] = p
        for j, (m, v) in enumerate(zip(self.otimizador.m, self.otimizador.v)):
            arrays[f"{prefixo}adam_m{j}"] = m
            arrays[f"{prefixo}adam_v{j}"] = v
        n = self.tamanho
        arrays[f"{prefixo}S"], arrays[f"{prefixo}A"] = self.S[:n], self.A[:n]
        arrays[f"{prefixo}R"], arrays[f"{prefixo}S2"] = self.R[:n], self.S2[:n]
        meta = {
            "dim": self.dim, "n_acoes": self.n_acoes, "oculto": list(self.oculto), "lr": self.lr,
            "gamma": self.gamma, "memoria": len(self.A), "lote": self.lote, "aquecimento": self.aquecimento,
            "alvo_cada": self.alvo_cada, "eps_inicial": self.eps_inicial, "eps_final": self.eps_final,
            "eps_passos": self.eps_passos, "clip_grad": self.clip_grad,
            "limites_q": list(self.limites_q) if self.limites_q is not None else None,
            "ponteiro": self.ponteiro, "tamanho": self.tamanho, "passos": self.passos,
            "atualizacoes": self.atualizacoes, "adam_t": self.otimizador.t,
            "rng": self.rng.bit_generator.state,
        }
        return arrays, meta

    @classmethod
    def de_checkpoint(cls, dados, prefixo: str, meta: dict) -> "AgenteDQN":
        ag = cls(
            meta["dim"], meta["n_acoes"], oculto=meta["oculto"], lr=meta["lr"], gamma=meta["gamma"],
            memoria=meta["memoria"], lote=meta["lote"], aquecimento=meta["aquecimento"],
            alvo_cada=meta["alvo_cada"], eps_inicial=meta["eps_inicial"], eps_final=meta["eps_final"],
            eps_passos=meta["eps_passos"], clip_grad=meta["clip_grad"], limites_q=meta.get("limites_q"),
        )
        ag.q.params = [dados[f"{prefixo}q{j}"].copy() for j in range(len(ag.q.params))]
        ag.alvo.params = [dados[f"{prefixo}alvo{j}"].copy() for j in range(len(ag.alvo.params))]
        ag.otimizador.m = [dados[f"{prefixo}adam_m{j}"].copy() for j in range(len(ag.q.params))]
        ag.otimizador.v = [dados[f"{prefixo}adam_v{j}"].copy() for j in range(len(ag.q.params))]
        ag.otimizador.t = meta["adam_t"]
        n = meta["tamanho"]
        ag.S[:n], ag.A[:n] = dados[f"{prefixo}S"], dados[f"{prefixo}A"]
        ag.R[:n], ag.S2[:n] = dados[f"{prefixo}R"], dados[f"{prefixo}S2"]
        ag.ponteiro, ag.tamanho = meta["ponteiro"], n
        ag.passos, ag.atualizacoes = meta["passos"], meta["atualizacoes"]
        ag.rng.bit_generator.state = meta["rng"]
        return ag


def salvar_checkpoint(caminho, agentes: dict, info: dict) -> None:
    """Salva redes, otimizador, memória de replay e contadores de todos os agentes num arquivo só."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arrays = {}
    meta = {"versao": 1, "info": info, "agentes": []}
    for i, (nome, ag) in enumerate(agentes.items()):
        a, m = ag.para_checkpoint(f"a{i}_")
        arrays.update(a)
        meta["agentes"].append({"id": nome, **m})
    arrays["meta"] = np.array(json.dumps(meta, ensure_ascii=False))
    temporario = caminho.with_name(caminho.stem + "_tmp.npz")
    np.savez(temporario, **arrays)  # sem compressão: o buffer é grande e comprimir deixaria lento
    temporario.replace(caminho)


def carregar_checkpoint(caminho) -> tuple[dict, dict]:
    with np.load(caminho, allow_pickle=False) as dados:
        meta = json.loads(str(dados["meta"]))
        agentes = {
            m["id"]: AgenteDQN.de_checkpoint(dados, f"a{i}_", m) for i, m in enumerate(meta["agentes"])
        }
    return agentes, meta["info"]


def salvar_modelo(caminho, agentes: dict, extra: dict | None = None) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arrays = {}
    meta = {"agentes": [], "extra": {"mapa": MAPA, **(extra or {})}}
    for i, (nome, ag) in enumerate(agentes.items()):
        meta["agentes"].append({"id": nome, "dim": ag.dim, "n_acoes": ag.n_acoes, "oculto": list(ag.oculto)})
        for j, p in enumerate(ag.q.params):
            arrays[f"a{i}_p{j}"] = p
    arrays["meta"] = np.array(json.dumps(meta, ensure_ascii=False))
    temporario = caminho.with_name(caminho.stem + "_tmp.npz")
    np.savez_compressed(temporario, **arrays)
    temporario.replace(caminho)


def carregar_modelo(caminho) -> tuple[dict, dict]:
    with np.load(caminho, allow_pickle=False) as dados:
        meta = json.loads(str(dados["meta"]))
        agentes = {}
        for i, info in enumerate(meta["agentes"]):
            ag = AgenteDQN(info["dim"], info["n_acoes"], oculto=info["oculto"], memoria=1)
            ag.q.params = [dados[f"a{i}_p{j}"].copy() for j in range(len(ag.q.params))]
            ag.alvo.copiar_de(ag.q)
            agentes[info["id"]] = ag
    return agentes, meta["extra"]


class PoliticaIDQN:
    """Política gulosa de um modelo salvo, para avaliação."""

    nome = "idqn"
    controla = True

    def __init__(self, caminho):
        self.agentes, self.extra = carregar_modelo(caminho)
        mapa = self.extra.get("mapa")
        if mapa and mapa != MAPA:
            raise SystemExit(
                f"O modelo {caminho} foi treinado no mapa '{mapa}', e o mapa atual é '{MAPA}'. "
                + ("Rode Remove-Item Env:MARL_MAPA para voltar ao MVP." if mapa == "gavea_real"
                   else f"Defina $env:MARL_MAPA=\"{mapa}\".")
            )

    def __call__(self, env, obs):
        acoes = {}
        for a in env.agentes:
            ag = self.agentes.get(a)
            if ag is None or ag.dim != env.dim_obs[a] or ag.n_acoes != env.n_acoes[a]:
                raise SystemExit(
                    f"O modelo não bate com a rede atual (mapa '{MAPA}') no semáforo '{a}'. "
                    "Se o modelo é de outro mapa, ajuste a variável MARL_MAPA (sem ela, vale o gavea_real: "
                    "Remove-Item Env:MARL_MAPA). Se é deste mapa, a rede ou o agrupamento de semáforos mudou "
                    "depois do treino: treine de novo."
                )
            acoes[a] = ag.agir(obs[a], explorar=False)
        return acoes
