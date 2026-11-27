"""Testes dos scripts de apoio (diagnóstico e geração de demanda), sem precisar do SUMO."""
from __future__ import annotations

import io
import math
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
# os testes rodam sempre no mapa padrão, qualquer que seja o mapa escolhido no terminal ou no .mapa
os.environ.pop("MARL_MAPA", None)
os.environ["MARL_ARQ_MAPA"] = str(RAIZ / "tests" / "_sem_mapa")  # não existe: vale o padrão
sys.path.insert(0, str(RAIZ / "scripts"))

import diagnosticar  # noqa: E402
import gerar_demanda  # noqa: E402

REDE_MINI = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.20">
    <location netOffset="0,0" convBoundary="0.00,0.00,100.00,200.00" origBoundary="-43.2300,-22.9800,-43.2200,-22.9700" projParameter="!"/>
    <edge id=":J1_0" function="internal">
        <lane id=":J1_0_0" index="0" speed="10" length="5.00" shape="50.00,0.00 50.00,5.00"/>
    </edge>
    <edge id="111#2" from="J0" to="J1">
        <lane id="111#2_0" index="0" speed="13.9" length="50.00" shape="0.00,0.00 50.00,0.00"/>
    </edge>
    <edge id="222-AddedOnRampEdge" from="J2" to="J1">
        <lane id="222-AddedOnRampEdge_0" index="0" speed="13.9" length="200.00" shape="50.00,200.00 50.00,0.00"/>
    </edge>
    <junction id="J0" type="dead_end" x="0.00" y="0.00"/>
    <junction id="J1" type="traffic_light" x="50.00" y="0.00"/>
    <junction id="J2" type="dead_end" x="50.00" y="200.00"/>
</net>
"""

OSM_MINI = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
    <node id="1" lat="-22.98" lon="-43.23"/>
    <way id="111"><nd ref="1"/><tag k="name" v="Rua Um"/></way>
    <way id="222"><nd ref="1"/><tag k="name" v="Avenida Dois"/></way>
</osm>
"""


class TestDiagnostico(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        base = Path(self.pasta.name)
        (base / "rede.net.xml").write_text(REDE_MINI, encoding="utf-8")
        (base / "mapa.osm.xml").write_text(OSM_MINI, encoding="utf-8")
        self.loc = diagnosticar.Localizador(base / "rede.net.xml", base / "mapa.osm.xml")

    def tearDown(self):
        self.pasta.cleanup()

    def test_nome_comprimento_e_coordenadas_da_faixa(self):
        r = self.loc.descrever("111#2_0")
        self.assertEqual(r["local"], "Rua Um")
        self.assertEqual(r["comprimento_m"], "50.0")
        # meio da faixa: x=25 (1/4 da largura), y=0 (borda sul)
        self.assertEqual(r["coordenadas"], "-22.980000,-43.227500")

    def test_rampa_inventada_pelo_netconvert_tem_o_nome_da_rua(self):
        r = self.loc.descrever("222-AddedOnRampEdge_0")
        self.assertEqual(r["local"], "Avenida Dois")
        self.assertEqual(r["coordenadas"], "-22.975000,-43.225000")

    def test_faixa_interna_mostra_o_cruzamento(self):
        r = self.loc.descrever(":J1_0_0")
        self.assertEqual(r["local"], "dentro do cruzamento: Avenida Dois / Rua Um")
        self.assertEqual(r["coordenadas"], "-22.980000,-43.225000")

    def test_faixa_desconhecida_nao_quebra(self):
        r = self.loc.descrever("999_0")
        self.assertIn("999", r["local"])
        self.assertEqual(r["coordenadas"], "")

    def test_sugere_maior_volume_que_escoa(self):
        # números do diagnóstico real (cenário teste_s101, programa atuado)
        resumos = {
            0.25: {"pct_concluidos": 100.0, "teleportes": 0},
            0.5: {"pct_concluidos": 96.4, "teleportes": 22},
            1.0: {"pct_concluidos": 48.3, "teleportes": 461},
        }
        self.assertEqual(diagnosticar.sugerir_escala(resumos), 0.25)
        resumos[0.4] = {"pct_concluidos": 98.0, "teleportes": 3}
        self.assertEqual(diagnosticar.sugerir_escala(resumos), 0.4)
        self.assertIsNone(diagnosticar.sugerir_escala({1.0: resumos[1.0]}))


class TestGerarDemanda(unittest.TestCase):
    def _comandos(self, ponderar):
        comandos = []

        def falso(cmd, **kw):
            comandos.append(cmd)
            Path(cmd[cmd.index("-r") + 1]).write_text("<routes/>", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(gerar_demanda, "DEMANDA", Path(tmp)), \
                mock.patch.object(gerar_demanda.subprocess, "run", falso):
            cenario = gerar_demanda.gerar_cenario("teste_s101", 101, 0.5, 3600, Path(tmp), ponderar)
        return cenario, comandos

    def test_escala_divide_o_intervalo_entre_veiculos(self):
        cenario, comandos = self._comandos(ponderar=True)
        periodos = [float(c[c.index("-p") + 1]) for c in comandos]
        self.assertEqual(periodos, [5.0, 24.0, 80.0])  # carro 2,5 s, moto 12 s, ônibus 40 s, com metade do volume
        self.assertEqual(cenario["escala"], 0.5)

    def test_ponderacao_pelas_avenidas(self):
        _, comandos = self._comandos(ponderar=True)
        for c in comandos:
            self.assertIn("-L", c)
            self.assertEqual(c[c.index("--speed-exponent") + 1], "2")
        _, comandos = self._comandos(ponderar=False)
        for c in comandos:
            self.assertNotIn("-L", c)
            self.assertNotIn("--speed-exponent", c)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------- zonas de detecção
from marl_gavea.ambiente import AmbienteGavea  # noqa: E402
from marl_gavea.controladores import MaxPressao  # noqa: E402
from marl_gavea.zonas import Zonas  # noqa: E402

sys.path.insert(0, str(RAIZ / "tests"))
from sumo_falso import SumoFalso  # noqa: E402

# a0 -> a1 -> [semáforo S1] -> b0 -> b1 -> [semáforo S2] -> c0
# a0 e a1 somam 30 m; b0 e b1 ficam entre os dois semáforos; c0 depois de S2
REDE_LINHA = """<net>
    <edge id="a0" from="n0" to="n1"><lane id="a0_0" index="0" length="20" shape="0,0 20,0"/></edge>
    <edge id="a1" from="n1" to="S1"><lane id="a1_0" index="0" length="10" shape="20,0 30,0"/></edge>
    <edge id="b0" from="S1" to="n2"><lane id="b0_0" index="0" length="40" shape="30,0 70,0"/></edge>
    <edge id="b1" from="n2" to="S2"><lane id="b1_0" index="0" length="80" shape="70,0 150,0"/></edge>
    <edge id="c0" from="S2" to="n3"><lane id="c0_0" index="0" length="200" shape="150,0 350,0"/></edge>
    <connection from="a0" to="a1" fromLane="0" toLane="0"/>
    <connection from="a1" to="b0" fromLane="0" toLane="0" tl="S1"/>
    <connection from="b0" to="b1" fromLane="0" toLane="0"/>
    <connection from="b1" to="c0" fromLane="0" toLane="0" tl="S2"/>
</net>"""

# mesma interface do simulador falso: as faixas do cruzamento 'cruz' se chamam norte_in, leste_in...
# Aqui a entrada norte tem só 10 m e um trecho de 60 m antes dela, como acontece na rede real.
REDE_FALSA = """<net>
    <edge id="antes" from="x" to="y"><lane id="antes_0" index="0" length="60" shape="0,0 0,60"/></edge>
    <edge id="norte" from="y" to="cruz"><lane id="norte_in" index="0" length="10" shape="0,60 0,70"/></edge>
    <edge id="leste" from="z" to="cruz"><lane id="leste_in" index="0" length="75" shape="0,0 75,0"/></edge>
    <connection from="antes" to="norte" fromLane="0" toLane="in"/>
    <connection from="norte" to="norte" fromLane="in" toLane="out" tl="cruz"/>
    <connection from="leste" to="leste" fromLane="in" toLane="out" tl="cruz"/>
</net>"""


class TestZonas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        caminho = Path(self.tmp.name) / "linha.net.xml"
        caminho.write_text(REDE_LINHA, encoding="utf-8")
        self.z = Zonas(caminho)

    def tearDown(self):
        self.tmp.cleanup()

    def test_montante_sobe_ate_o_alcance(self):
        self.assertEqual(self.z.montante("a1_0"), ["a1_0", "a0_0"])
        self.assertEqual(self.z.montante("a1_0", alcance=5), ["a1_0"])

    def test_montante_para_antes_de_outro_semaforo(self):
        # b1 chega ao S2; antes dele vem b0, mas antes de b0 está o S1
        self.assertEqual(self.z.montante("b1_0"), ["b1_0", "b0_0"])

    def test_jusante_vai_ate_a_chegada_do_proximo_semaforo(self):
        self.assertEqual(self.z.jusante("b0_0"), ["b0_0", "b1_0"])
        self.assertEqual(self.z.jusante("c0_0"), ["c0_0"])
        self.assertEqual(self.z.tamanho(["b0_0", "b1_0"]), 120)


class TestAmbienteComZonas(unittest.TestCase):
    def test_fila_escondida_antes_da_faixa_curta_conta(self):
        with tempfile.TemporaryDirectory() as tmp:
            rede = Path(tmp) / "falsa.net.xml"
            rede.write_text(REDE_FALSA, encoding="utf-8")
            sims = []

            def conectar(env):
                sim = SumoFalso()
                sim.n["antes_0"] = 0
                sim.espera["antes_0"] = 0.0
                sims.append(sim)
                return sim

            env = AmbienteGavea(rede, [], conectar=conectar)
            env.reset()
            sim = sims[-1]
            # só 1 carro encosta no semáforo do norte, mas há 6 parados no trecho de trás;
            # no leste há 4 carros
            sim.n["norte_in"], sim.n["antes_0"], sim.n["leste_in"] = 1, 6, 4
            env._ler_faixas()
            self.assertEqual(env.semaforos["cruz"].zona_entrada["norte_in"], ["norte_in", "antes_0"])
            self.assertEqual(MaxPressao()(env, None), {"cruz": 0})
            self.assertEqual(env._fila("cruz"), 11.0)
            obs = env._observar("cruz")
            # [fase0, fase1, pode_trocar, dens_norte, dens_leste, fila_norte, fila_leste, saida_norte, saida_leste,
            #  espera_norte, espera_leste]
            self.assertAlmostEqual(float(obs[3]), 0.7, places=5)  # 7 veículos / 10 de capacidade
            env.fechar()


class TestPressaoComSaidaCheia(unittest.TestCase):
    def test_nao_manda_carro_para_saida_lotada(self):
        sims = []

        def conectar(env):
            sims.append(SumoFalso())
            return sims[-1]

        env = AmbienteGavea("rede.net.xml", [], conectar=conectar)
        env.reset()
        sim = sims[-1]
        # norte tem mais carros esperando, mas a saída dele está lotada (10 de 10)
        sim.n["norte_in"], sim.n["norte_out"], sim.n["leste_in"] = 6, 10, 3
        env._ler_faixas()
        self.assertEqual(MaxPressao()(env, None), {"cruz": 1})
        sim.n["norte_out"] = 0
        env._ler_faixas()
        self.assertEqual(MaxPressao()(env, None), {"cruz": 0})
        self.assertAlmostEqual(float(env._observar("cruz")[7]), 0.0)  # ocupação da saída norte
        env.fechar()


# ---------------------------------------------------------------------- investigação de colapsos
import investigar  # noqa: E402


class SempreFase0:
    """Política teimosa: só quer a fase 0. O verde máximo é que obriga a abrir a outra."""

    controla = True

    def __call__(self, env, obs):
        return {a: 0 for a in env.agentes}


class TestInvestigar(unittest.TestCase):
    def test_aponta_faixa_sem_verde_e_trocas_forcadas(self):
        cenario = {"nome": "falso", "semente": 0, "rotas": []}
        original = investigar.AmbienteGavea

        def ambiente_falso(rede, rotas, **kw):
            return original("rede.net.xml", rotas, conectar=lambda env: SumoFalso(chegadas=(0.3, 0.3)), **kw)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(investigar, "AmbienteGavea", ambiente_falso):
            r = investigar.rodar(SempreFase0(), cenario, 1.0, 600, Path(tmp) / "rodada")
            self.assertGreater(r["semaforos"]["cruz"]["forcadas"], 0)
            espera, _, parados = r["rastreador"].pior_espera[("cruz", "leste_in")]
            self.assertGreaterEqual(espera, 85)  # leste espera o verde máximo inteiro da fase 0
            self.assertGreater(parados, 0)
            self.assertNotIn(("cruz", "norte_in"), {k for k, v in r["rastreador"].pior_espera.items() if v[0] > 30})
            self.assertEqual(len(r["rastreador"].registros), 600 // 5)
            # o leste fica até 90 s no vermelho: a zona dele enche e o agente fica "cego" em parte do tempo
            with mock.patch("sys.stdout", io.StringIO()):
                tabela = investigar.relatorio_saturacao([r])
            self.assertGreater(tabela[("cruz", 1.0)][0], 0.0)
            self.assertLessEqual(tabela[("cruz", 1.0)][1], tabela[("cruz", 1.0)][0])
            self.assertIn("zona_cheia", r["rastreador"].registros[0])

    def test_le_pasta_de_validacao(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "rede.net.xml").write_text(REDE_MINI, encoding="utf-8")
            (base / "mapa.osm.xml").write_text(OSM_MINI, encoding="utf-8")
            pasta = base / "ep1495"
            pasta.mkdir()
            (pasta / "tripinfo.xml").write_text("""<tripinfos>
                <tripinfo id="carro_1" departLane="111#2_0" arrival="300" timeLoss="20"/>
                <tripinfo id="carro_2" departLane="222-AddedOnRampEdge_0" arrival="-1" timeLoss="900"/>
                <tripinfo id="carro_3" departLane="222-AddedOnRampEdge_0" arrival="800" timeLoss="500"/>
            </tripinfos>""", encoding="utf-8")
            loc = diagnosticar.Localizador(base / "rede.net.xml", base / "mapa.osm.xml")
            saida = io.StringIO()
            with mock.patch("sys.stdout", saida):
                investigar.ler_pasta(pasta, loc, 5)
            texto = saida.getvalue()
            self.assertIn("1 não terminaram", texto)
            primeira = [l for l in texto.splitlines() if "Avenida Dois" in l or "Rua Um" in l][0]
            self.assertIn("Avenida Dois", primeira)  # a rua com mais atraso aparece primeiro


class TestVermelhoMaximo(unittest.TestCase):
    """Uma política que só quer a fase 0 não pode deixar o leste esperando para sempre."""

    def _rodar(self, **kw):
        sims = []

        def conectar(env):
            sims.append(SumoFalso(chegadas=(0.3, 0.1)))
            return sims[-1]

        env = AmbienteGavea("rede.net.xml", [], conectar=conectar, duracao=900, **kw)
        rastreador = investigar.Rastreador(SempreFase0())
        obs = env.reset()
        rastreador.preparar(env)
        fim, maior_espera_obs = False, 0.0
        while not fim:
            obs, _, fim, _ = env.step(rastreador(env, obs))
            maior_espera_obs = max(maior_espera_obs, float(obs["cruz"][-1]))
        s = env.semaforos["cruz"]
        resumo = env.fechar()
        verdes_leste = [t for t, tl, estado in sims[-1].historico if tl == "cruz" and estado == "rG"]
        pior = rastreador.pior_espera.get(("cruz", "leste_in"), (0.0, 0.0, 0.0))[0]
        return s, resumo, verdes_leste, maior_espera_obs, pior

    def test_sem_limites_o_leste_nunca_abre(self):
        _, _, verdes_leste, _, pior = self._rodar(verde_max=0, vermelho_max=0)
        self.assertEqual(verdes_leste, [])
        self.assertGreater(pior, 800)

    def test_vermelho_maximo_obriga_a_abrir(self):
        s, resumo, verdes_leste, maior_espera_obs, pior = self._rodar(verde_max=0, vermelho_max=60)
        self.assertGreater(s.por_espera, 0)
        self.assertEqual(resumo["trocas_por_espera"], s.por_espera)
        self.assertTrue(verdes_leste)
        # quem espera aparece na observação, chegando a 1 quando bate o vermelho máximo
        self.assertAlmostEqual(maior_espera_obs, 1.0)
        # carro parado no leste espera no máximo o vermelho máximo + uma decisão + o amarelo (+ folga da medição)
        self.assertLessEqual(pior, 60 + 5 + 3 + 5)


# ---------------------------------------------------------------------- estabilidade do treino (v3)
import numpy as np  # noqa: E402

from marl_gavea.idqn import AgenteDQN  # noqa: E402


class TestEstabilidade(unittest.TestCase):
    def test_recompensa_fila_fica_entre_menos_um_e_zero(self):
        sims = []

        def conectar(env):
            sims.append(SumoFalso())
            return sims[-1]

        env = AmbienteGavea("rede.net.xml", [], conectar=conectar, recompensa="fila")
        env.reset()
        s = env.semaforos["cruz"]
        sim = sims[-1]
        sim.n["norte_in"], sim.n["leste_in"] = 3, 2
        env._ler_faixas()
        self.assertAlmostEqual(env._recompensa("cruz"), -5 / s.cap_montante)
        sim.n["norte_in"], sim.n["leste_in"] = 500, 500  # absurdo: fica em -1
        env._ler_faixas()
        self.assertEqual(env._recompensa("cruz"), -1.0)
        env.fechar()

    def _treinar_com_recompensa(self, recompensa, limites):
        ag = AgenteDQN(2, 2, semente=0, aquecimento=64, alvo_cada=50, lr=1e-3, gamma=0.9, limites_q=limites)
        rng = np.random.default_rng(0)
        for _ in range(3000):
            s = rng.random(2).astype(np.float32)
            ag.guardar(s, int(rng.integers(2)), recompensa, rng.random(2).astype(np.float32))
            ag.treinar()
        q, _ = ag.q.forward(rng.random((200, 2)))
        return q

    def test_limites_seguram_o_valor_q(self):
        # recompensa -5 por passo com gamma 0.9 levaria Q a -50; com os limites da recompensa
        # normalizada (Q entre -10 e 0), a rede não passa muito de -10
        q_sem = self._treinar_com_recompensa(-5.0, None)
        q_com = self._treinar_com_recompensa(-5.0, (-10.0, 0.0))
        self.assertLess(q_sem.mean(), -15)
        self.assertGreater(q_com.min(), -12)

    def test_checkpoint_guarda_os_limites(self):
        ag = AgenteDQN(3, 2, limites_q=(-20.0, 0.0))
        arrays, meta = ag.para_checkpoint("x_")
        de_novo = AgenteDQN.de_checkpoint(arrays, "x_", meta)
        self.assertEqual(de_novo.limites_q, (-20.0, 0.0))
        self.assertEqual((de_novo.gamma, de_novo.lr, de_novo.alvo_cada), (0.95, 1e-4, 1000))


import avaliar  # noqa: E402
import treinar_idqn  # noqa: E402


class TestCriterios(unittest.TestCase):
    def test_validacao_usa_atraso_total(self):
        r = {"atraso_total_medio_s": 80.0, "tempo_perdido_medio_s": 60.0, "fila_media_veic": 5.0}
        self.assertEqual(treinar_idqn.criterio(r), ("atraso_total_medio_s", 80.0))
        self.assertEqual(treinar_idqn.criterio({"tempo_perdido_medio_s": 60.0}), ("tempo_perdido_medio_s", 60.0))

    def test_variacao_sem_referencia_nao_quebra(self):
        self.assertAlmostEqual(avaliar.variacao_pct(90.0, 100.0), -10.0)
        self.assertTrue(np.isnan(avaliar.variacao_pct(90.0, float("nan"))))


from marl_gavea import comum  # noqa: E402


class TestTempoESuspensao(unittest.TestCase):
    def test_estimativa_ignora_suspensao(self):
        normal = [16.0] * 19
        self.assertAlmostEqual(treinar_idqn.estimar_restante(normal + [16.0], 100), 1600.0)
        # uma rodada de 5,5 h (PC suspenso) conta como 5x a mediana, não como 5,5 h
        com_pausa = treinar_idqn.estimar_restante(normal + [19965.0], 100)
        self.assertLess(com_pausa, 2000.0)

    def test_impedir_suspensao_fora_do_windows_nao_faz_nada(self):
        with mock.patch.object(comum.sys, "platform", "linux"):
            self.assertFalse(comum.impedir_suspensao())

    def test_impedir_suspensao_no_windows_chama_a_api(self):
        chamada = mock.Mock(return_value=1)
        falso = mock.Mock()
        falso.windll.kernel32.SetThreadExecutionState = chamada
        with mock.patch.object(comum.sys, "platform", "win32"), mock.patch.dict(sys.modules, {"ctypes": falso}):
            self.assertTrue(comum.impedir_suspensao())
        chamada.assert_called_once_with(0x80000001)


class TestVariosModelos(unittest.TestCase):
    def test_rotulos(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d) / "idqn_v3.1_s0"
            pasta.mkdir()
            (pasta / "melhor.npz").write_bytes(b"x")
            (pasta / "ultimo.npz").write_bytes(b"x")
            m = avaliar.ler_modelos([f"s0={pasta / 'melhor.npz'}", str(pasta / "ultimo.npz")])
            self.assertEqual(list(m), ["s0", "idqn_v3.1_s0_ultimo"])
            # caminho no formato do Windows também vira rótulo pasta_arquivo
            with mock.patch.object(avaliar.Path, "exists", return_value=True):
                m = avaliar.ler_modelos([r"resultados\idqn_s2\melhor.npz"])
            self.assertEqual(list(m), ["idqn_s2_melhor"])

    def test_rotulo_invalido_repetido_ou_arquivo_ausente(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "m.npz"
            arq.write_bytes(b"x")
            for itens in ([f"a b={arq}"], [f"x={arq}", f"x={arq}"], [f"atuado={arq}"], [f"y={Path(d) / 'nao.npz'}"]):
                with self.assertRaises(SystemExit):
                    avaliar.ler_modelos(itens)

    def test_rodada_com_dois_modelos_e_baseline(self):
        import csv as _csv

        original = avaliar.AmbienteGavea

        def ambiente_falso(rede, rotas, **kw):
            return original("rede.net.xml", rotas, conectar=lambda env: SumoFalso(), **kw)

        class PoliticaFalsa(SempreFase0):
            def __init__(self, caminho):
                self.caminho = caminho

        cenarios = [{"nome": "teste_falso", "semente": 0, "rotas": []}]
        with tempfile.TemporaryDirectory() as tmp:
            for nome in ("a.npz", "b.npz"):
                (Path(tmp) / nome).write_bytes(b"x")
            argv = ["avaliar.py", "--metodos", "max_pressao", "--duracao", "300", "--saida", tmp,
                    "--modelos", f"s0={Path(tmp) / 'a.npz'}", f"s1_ultimo={Path(tmp) / 'b.npz'}"]
            with mock.patch.object(avaliar, "AmbienteGavea", ambiente_falso), \
                    mock.patch.object(avaliar, "carregar_manifesto", return_value={}), \
                    mock.patch.object(avaliar, "rotas_do_conjunto", return_value=cenarios), \
                    mock.patch("marl_gavea.idqn.PoliticaIDQN", PoliticaFalsa), \
                    mock.patch.object(sys, "argv", argv), mock.patch("sys.stdout", io.StringIO()) as tela:
                avaliar.main()
                texto = tela.getvalue()
            resumo = next(Path(tmp).glob("*/resumo.csv"))
            linhas = list(_csv.DictReader(resumo.read_text(encoding="utf-8").splitlines()))
        self.assertEqual([l["metodo"] for l in linhas], ["max_pressao", "s0", "s1_ultimo"])
        self.assertIn("atraso_total_vs_max_pressao_pct", linhas[0])
        self.assertIn("s1_ultimo", texto)


# ---------------------------------------------------------------------- mapa ampliado
import subprocess as _sp  # noqa: E402

import listar_semaforos  # noqa: E402

REDE_DOIS_SINAIS = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.20">
    <location netOffset="0,0" convBoundary="0.00,0.00,100.00,200.00" origBoundary="-43.2300,-22.9800,-43.2200,-22.9700" projParameter="!"/>
    <edge id="111#1" from="A0" to="J1" name="Rua Um"><lane id="111#1_0" index="0" speed="13.9" length="50.00" shape="0,0 50,0"/></edge>
    <edge id="222" from="A1" to="J1"><lane id="222_0" index="0" speed="13.9" length="50.00" shape="50,-50 50,0"/></edge>
    <edge id="333" from="J1" to="J2" name="Rua Tres"><lane id="333_0" index="0" speed="13.9" length="30.00" shape="50,0 50,30"/></edge>
    <edge id="444" from="A2" to="J2" name="Rua Quatro"><lane id="444_0" index="0" speed="13.9" length="50.00" shape="0,30 50,30"/></edge>
    <tlLogic id="J1" type="actuated" programID="0" offset="0">
        <phase duration="30" state="Gr"/><phase duration="3" state="yr"/><phase duration="30" state="rG"/><phase duration="3" state="ry"/>
    </tlLogic>
    <tlLogic id="J2" type="actuated" programID="0" offset="0">
        <phase duration="30" state="Gr"/><phase duration="3" state="yr"/><phase duration="30" state="Gr"/>
    </tlLogic>
    <junction id="A0" type="dead_end" x="0.00" y="0.00"/>
    <junction id="A1" type="dead_end" x="50.00" y="-50.00"/>
    <junction id="A2" type="dead_end" x="0.00" y="30.00"/>
    <junction id="J1" type="traffic_light" x="50.00" y="0.00"/>
    <junction id="J2" type="traffic_light" x="50.00" y="30.00"/>
    <connection from="111#1" to="333" fromLane="0" toLane="0" tl="J1" linkIndex="0"/>
    <connection from="222" to="333" fromLane="0" toLane="0" tl="J1" linkIndex="1"/>
    <connection from="444" to="333" fromLane="0" toLane="0" tl="J2" linkIndex="0"/>
    <connection from="333" to="999" fromLane="0" toLane="0" tl="J2" linkIndex="1"/>
</net>
"""

OSM_DOIS = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6"><way id="222"><tag k="name" v="Avenida Dois"/></way></osm>
"""


class TestListarSemaforos(unittest.TestCase):
    def test_agente_fixo_nomes_e_vizinho_perto(self):
        with tempfile.TemporaryDirectory() as d:
            rede, osm = Path(d) / "gavea.net.xml", Path(d) / "gavea.osm.xml"
            rede.write_text(REDE_DOIS_SINAIS, encoding="utf-8")
            osm.write_text(OSM_DOIS, encoding="utf-8")
            lista = listar_semaforos.ler_rede(rede, osm)
            argv = ["listar_semaforos.py", "--rede", str(rede)]
            with mock.patch.object(sys, "argv", argv), mock.patch("sys.stdout", io.StringIO()) as tela:
                listar_semaforos.main()
            texto = tela.getvalue()
            csv_texto = (Path(d) / "semaforos.csv").read_text(encoding="utf-8-sig")
        por_id = {s["id"]: s for s in lista}
        self.assertEqual([s["id"] for s in lista], ["J2", "J1"])  # do norte para o sul
        self.assertEqual(por_id["J1"]["tipo"], "agente")
        self.assertEqual(por_id["J1"]["fases_verdes"], 2)
        self.assertEqual(por_id["J1"]["ruas"], "Avenida Dois x Rua Um")  # nome da rede e, sem ele, do OSM
        self.assertEqual(por_id["J2"]["tipo"], "fixo")  # "Gr" repetido é uma fase verde só
        self.assertAlmostEqual(por_id["J1"]["coord"][0], -22.98)
        self.assertAlmostEqual(por_id["J1"]["coord"][1], -43.225)
        self.assertAlmostEqual(por_id["J2"]["distancia_m"], 30.0)
        self.assertIn("mesmo cruzamento?", texto)
        self.assertIn("1 viram agentes", texto)
        self.assertIn("Rua Quatro x Rua Tres", csv_texto)


class TestEscolhaDoMapa(unittest.TestCase):
    RAIZ_PROJ = Path(__file__).resolve().parents[1]

    def _rodar(self, codigo, variavel="", arquivo=None, *args):
        env = {**os.environ, "MARL_MAPA": variavel,
               "MARL_ARQ_MAPA": str(arquivo or self.RAIZ_PROJ / "tests" / "_sem_mapa")}
        return _sp.run([sys.executable, *codigo, *args], cwd=self.RAIZ_PROJ, env=env, capture_output=True, text=True)

    def _comum(self, variavel="", arquivo=None):
        codigo = ["-c", "import sys; sys.path.insert(0, '.'); import marl_gavea.comum as c; "
                  "print(c.MAPA, c.REDE.parent.name, c.DEMANDA.name, c.RESULTADOS.relative_to(c.RAIZ).as_posix())"]
        return self._rodar(codigo, variavel, arquivo)

    def test_mapa_padrao_e_ampliado_nao_se_misturam(self):
        r = self._comum()
        self.assertEqual(r.stdout.split()[-4:], ["gavea_real", "gavea_real", "demanda", "resultados"])
        self.assertIn("[mapa: gavea_real (MVP) | padrão]", r.stdout)
        r = self._comum("gavea_ampliada")
        self.assertEqual(r.stdout.split()[-4:], ["gavea_ampliada", "gavea_ampliada", "demanda_gavea_ampliada",
                                                 "resultados/gavea_ampliada"])
        self.assertIn("[mapa: gavea_ampliada | variável MARL_MAPA deste terminal]", r.stdout)

    def test_mapa_inexistente_avisa(self):
        r = self._comum("nao_existe")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gavea_ampliada", r.stderr)  # lista os mapas que existem

    def test_escolha_pelo_arquivo_vale_sem_a_variavel_e_perde_para_ela(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / ".mapa"
            mapa_py = [str(self.RAIZ_PROJ / "scripts" / "mapa.py")]
            r = self._rodar(mapa_py, "", arq, "gavea_ampliada")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(arq.read_text(encoding="utf-8").strip(), "gavea_ampliada")
            self.assertIn("[mapa: gavea_ampliada | arquivo .mapa]", self._comum("", arq).stdout)
            self.assertIn("gavea_real", self._comum("gavea_real", arq).stdout.split()[-4:])  # a variável manda
            r = self._rodar(mapa_py, "gavea_real", arq)  # só mostra, e avisa da variável
            self.assertIn("Remove-Item Env:MARL_MAPA", r.stdout)
            self.assertEqual(self._rodar(mapa_py, "", arq, "nao_existe").returncode, 1)
            self._rodar(mapa_py, "", arq, "gavea_real")  # volta ao MVP: apaga o arquivo
            self.assertFalse(arq.exists())
            self.assertIn("[mapa: gavea_real (MVP) | padrão]", self._comum("", arq).stdout)


# ---------------------------------------------------------------------- coordenadas
from marl_gavea import geo  # noqa: E402


class TestCoordenadas(unittest.TestCase):
    PROJ = "+proj=utm +zone=23 +south +ellps=WGS84 +datum=WGS84 +units=m +no_defs"

    def test_meridiano_central_confere_com_integracao_numerica(self):
        # arco de meridiano integrado na mão, independente das fórmulas da projeção
        phi, n = math.radians(-22.98), 20000
        h = phi / n
        arco = sum(geo.A * (1 - geo.E2) / (1 - geo.E2 * math.sin((i + 0.5) * h) ** 2) ** 1.5 * h for i in range(n))
        lat, lon = geo.utm_para_lat_lon(500000.0, 10000000.0 + geo.K0 * arco, 23, True)
        self.assertAlmostEqual(lat, -22.98, places=7)
        self.assertAlmostEqual(lon, -45.0, places=9)

    def test_ida_e_volta_na_gavea(self):
        for lat, lon in [(-22.9790, -43.2330), (-22.9835, -43.2213)]:
            e, n = geo.lat_lon_para_utm(lat, lon, 23)
            la, lo = geo.utm_para_lat_lon(e, n, 23, True)
            self.assertAlmostEqual(la, lat, places=8)
            self.assertAlmostEqual(lo, lon, places=8)

    def test_netoffset_e_recorte_cortado_nao_esticam(self):
        e, n = geo.lat_lon_para_utm(-22.9768, -43.2251, 23)
        # origBoundary bem maior que o recorte (pontos de fora lidos do OSM): não pode influir
        el = ET.fromstring(f'<location netOffset="-681000.00,-7457000.00" convBoundary="0,0,1700,900" '
                           f'origBoundary="-43.30,-23.05,-43.15,-22.90" projParameter="{self.PROJ}"/>')
        lat, lon = geo.Projecao.do_location(el).para_lat_lon(e - 681000.0, n - 7457000.0)
        self.assertAlmostEqual(lat, -22.9768, places=7)
        self.assertAlmostEqual(lon, -43.2251, places=7)


class TestModeloDeOutroMapa(unittest.TestCase):
    def test_modelo_guarda_o_mapa_e_recusa_outro(self):
        from marl_gavea import idqn

        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "m.npz"
            idqn.salvar_modelo(arq, {"cruz": AgenteDQN(4, 2, semente=0)}, {"episodio": 1})
            _, extra = idqn.carregar_modelo(arq)
            self.assertEqual(extra["mapa"], idqn.MAPA)
            self.assertEqual(extra["episodio"], 1)
            idqn.PoliticaIDQN(arq)  # mesmo mapa: carrega
            with mock.patch.object(idqn, "MAPA", "gavea_ampliada"):
                with self.assertRaises(SystemExit) as erro:
                    idqn.PoliticaIDQN(arq)
            self.assertIn(f"python scripts/mapa.py {idqn.MAPA}", str(erro.exception))
