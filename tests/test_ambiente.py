"""Testes que não precisam do SUMO. Rode na raiz do repositório: python -m unittest discover -s tests -v"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from marl_gavea.ambiente import AmbienteGavea, rodar_episodio  # noqa: E402
from marl_gavea.controladores import Aleatorio, Atuado, MaxPressao  # noqa: E402
from marl_gavea.idqn import (  # noqa: E402
    MLP, AgenteDQN, carregar_checkpoint, carregar_modelo, salvar_checkpoint, salvar_modelo,
)
from marl_gavea.metricas import resumo_saida  # noqa: E402
from sumo_falso import SumoFalso  # noqa: E402


def criar_env(chegadas=(0.30, 0.10), semente=0, **kw):
    sims = []

    def conectar(env):
        sims.append(SumoFalso(chegadas=chegadas, semente=semente))
        return sims[-1]

    return AmbienteGavea("rede.net.xml", [], conectar=conectar, **kw), sims


class TestAmbiente(unittest.TestCase):
    def test_descobre_agentes_e_fixos(self):
        env, _ = criar_env()
        obs = env.reset()
        self.assertEqual(env.agentes, ["cruz"])
        self.assertEqual(env.fixos, ["fixo"])
        self.assertEqual(env.n_acoes["cruz"], 2)
        # fases, pode trocar, por entrada (densidade, fila, espera) e por saída (densidade)
        self.assertEqual(env.dim_obs["cruz"], 2 + 1 + 3 * 2 + 2)
        self.assertEqual(obs["cruz"].shape, (11,))

    def test_respeita_verde_minimo_e_amarelo(self):
        env, sims = criar_env(delta=5, amarelo=3, verde_min=10)
        env.reset()
        sim = sims[-1]
        env.step({"cruz": 1})  # t=0, verde há 0 s: não troca
        self.assertEqual(sim.estados["cruz"], "Gr")
        env.step({"cruz": 1})  # t=5: ainda abaixo de 10 s
        self.assertEqual(sim.estados["cruz"], "Gr")
        env.step({"cruz": 1})  # t=10: troca com amarelo, verde novo em t=13
        mudancas = [(t, e) for t, tl, e in sim.historico if tl == "cruz"]
        self.assertIn((10.0, "yr"), mudancas)
        self.assertIn((13.0, "rG"), mudancas)
        self.assertEqual(env.semaforos["cruz"].fase, 1)
        self.assertEqual(env.semaforos["cruz"].inicio_verde, 13.0)

    def test_verde_maximo_forca_troca(self):
        env, sims = criar_env(delta=5, amarelo=3, verde_min=10, verde_max=30)
        env.reset()
        for _ in range(7):  # pede sempre a fase 0
            env.step({"cruz": 0})
        self.assertEqual(env.semaforos["cruz"].fase, 1)

    def test_atuado_nao_mexe_no_semaforo(self):
        env, sims = criar_env(controlar=False, duracao=60)
        rodar_episodio(env, Atuado())
        self.assertEqual(sims[-1].historico, [])
        self.assertTrue(sims[-1].fechado)

    def test_max_pressao_escolhe_fila_maior(self):
        env, sims = criar_env()
        env.reset()
        sim = sims[-1]
        sim.n["norte_in"], sim.n["leste_in"] = 1, 8
        env._ler_faixas()  # o ambiente lê as faixas uma vez por decisão
        self.assertEqual(MaxPressao()(env, None), {"cruz": 1})
        sim.n["norte_in"], sim.n["leste_in"] = 9, 2
        env._ler_faixas()
        self.assertEqual(MaxPressao()(env, None), {"cruz": 0})

    def test_max_pressao_mantem_fase_no_empate(self):
        env, sims = criar_env()
        env.reset()
        env.semaforos["cruz"].fase = 1
        sims[-1].n["norte_in"], sims[-1].n["leste_in"] = 4, 4
        env._ler_faixas()
        self.assertEqual(MaxPressao()(env, None), {"cruz": 1})

    def test_episodio_termina_na_duracao(self):
        env, sims = criar_env(duracao=100)
        resumo = rodar_episodio(env, Aleatorio(semente=1))
        self.assertEqual(sims[-1].t, 100.0)
        self.assertIn("fila_media_veic", resumo)
        self.assertTrue(sims[-1].fechado)

    def test_recompensa_espera(self):
        env, sims = criar_env(recompensa="espera")
        env.reset()
        _, r, _, _ = env.step({"cruz": 0})
        self.assertLessEqual(r["cruz"], 0.0)


class TestRede(unittest.TestCase):
    def test_gradiente_confere_com_diferencas_finitas(self):
        rng = np.random.default_rng(0)
        rede = MLP([5, 7, 6, 3], rng)
        x = rng.standard_normal((4, 5))
        alvo = rng.standard_normal((4, 3))

        def perda():
            y, _ = rede.forward(x)
            return 0.5 * float(((y - alvo) ** 2).sum())

        y, cache = rede.forward(x)
        grads = rede.backward(y - alvo, cache)
        for p, g in zip(rede.params, grads):
            idx = tuple(rng.integers(0, s) for s in p.shape)
            original = p[idx]
            p[idx] = original + 1e-6
            mais = perda()
            p[idx] = original - 1e-6
            menos = perda()
            p[idx] = original
            self.assertAlmostEqual((mais - menos) / 2e-6, g[idx], places=4)

    def test_salvar_e_carregar(self):
        ag = AgenteDQN(7, 2, semente=3, memoria=10)
        x = np.random.default_rng(1).random(7).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            caminho = Path(d) / "m.npz"
            salvar_modelo(caminho, {"cruz": ag}, {"episodio": 4})
            agentes, extra = carregar_modelo(caminho)
        self.assertEqual(extra["episodio"], 4)
        q1, _ = ag.q.forward(x[None])
        q2, _ = agentes["cruz"].q.forward(x[None])
        np.testing.assert_allclose(q1, q2)

    def test_checkpoint_continua_igual_a_nao_parar(self):
        """Salvar, carregar e seguir treinando tem que dar exatamente o mesmo que treinar sem parar."""
        dados = np.random.default_rng(7)
        transicoes = [(dados.random(6), int(dados.integers(3)), float(dados.standard_normal()), dados.random(6))
                      for _ in range(400)]

        def novo():
            return AgenteDQN(6, 3, semente=11, memoria=300, lote=16, aquecimento=50, alvo_cada=30, eps_passos=200)

        def rodar(ag, trechos):
            acoes = []
            for s, a, r, s2 in trechos:
                acoes.append(ag.agir(s))
                ag.guardar(s, a, r, s2)
                ag.treinar()
            return acoes

        direto = novo()
        acoes_direto = rodar(direto, transicoes)

        interrompido = novo()
        acoes_parte1 = rodar(interrompido, transicoes[:250])  # buffer de 300 ainda não encheu
        with tempfile.TemporaryDirectory() as d:
            salvar_checkpoint(Path(d) / "ck.npz", {"x": interrompido}, {"episodio": 1})
            agentes, info = carregar_checkpoint(Path(d) / "ck.npz")
        acoes_parte2 = rodar(agentes["x"], transicoes[250:])  # aqui o buffer dá a volta

        self.assertEqual(info["episodio"], 1)
        self.assertEqual(acoes_direto, acoes_parte1 + acoes_parte2)
        for p1, p2 in zip(direto.q.params + direto.alvo.params, agentes["x"].q.params + agentes["x"].alvo.params):
            np.testing.assert_allclose(p1, p2)
        self.assertEqual(direto.passos, agentes["x"].passos)
        self.assertAlmostEqual(direto.eps, agentes["x"].eps)

    def test_idqn_aprende_a_atender_a_fila_maior(self):
        """No cruzamento falso o norte recebe 3x mais carros. O IDQN treinado deve gerar menos fila que o aleatório."""
        agentes = {}

        def politica(env, obs):
            if not agentes:
                agentes["cruz"] = AgenteDQN(env.dim_obs["cruz"], 2, semente=0, aquecimento=200,
                                            alvo_cada=200, eps_passos=4000, lr=1e-3, gamma=0.9)
            return {"cruz": agentes["cruz"].agir(obs["cruz"])}

        def aprender(obs, acoes, rec, obs2):
            agentes["cruz"].guardar(obs["cruz"], acoes["cruz"], rec["cruz"], obs2["cruz"])
            agentes["cruz"].treinar()

        for ep in range(12):
            env, _ = criar_env(semente=ep, duracao=3000, verde_min=5, verde_max=60)
            rodar_episodio(env, politica, aprender)

        def gulosa(env, obs):
            return {"cruz": agentes["cruz"].agir(obs["cruz"], explorar=False)}

        filas_idqn, filas_aleatorio = [], []
        for s in range(100, 105):
            env, _ = criar_env(semente=s, duracao=3000, verde_min=5, verde_max=60)
            filas_idqn.append(rodar_episodio(env, gulosa)["fila_media_veic"])
            env, _ = criar_env(semente=s, duracao=3000, verde_min=5, verde_max=60)
            filas_aleatorio.append(rodar_episodio(env, Aleatorio(semente=s))["fila_media_veic"])
        self.assertLess(np.mean(filas_idqn), np.mean(filas_aleatorio))


class TestMetricas(unittest.TestCase):
    def test_le_tripinfo_e_estatisticas(self):
        trip = """<tripinfos>
  <tripinfo id="carro_0" depart="1.00" departDelay="0.50" arrival="101.00" duration="100.00"
            waitingTime="20.00" timeLoss="40.00" vType="passenger"/>
  <tripinfo id="onibus_0" depart="5.00" departDelay="1.50" arrival="-1.00" duration="-1.00"
            waitingTime="60.00" timeLoss="80.00" vType="DEFAULT_VEHTYPE"/>
</tripinfos>"""
        stats = """<statistics>
  <vehicles loaded="3" inserted="2" running="1" waiting="1"/>
  <teleports total="4" jam="4" yield="0" wrongLane="0"/>
</statistics>"""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tripinfo.xml").write_text(trip, encoding="utf-8")
            (Path(d) / "estatisticas.xml").write_text(stats, encoding="utf-8")
            r = resumo_saida(Path(d))
        self.assertEqual(r["veiculos"], 2)
        self.assertEqual(r["concluidos"], 1)
        self.assertAlmostEqual(r["tempo_perdido_medio_s"], 60.0)
        self.assertAlmostEqual(r["tempo_viagem_medio_s"], 100.0)
        self.assertAlmostEqual(r["tempo_perdido_onibus_s"], 80.0)
        self.assertEqual(r["nao_inseridos"], 1)
        self.assertEqual(r["teleportes"], 4)


if __name__ == "__main__":
    unittest.main()
