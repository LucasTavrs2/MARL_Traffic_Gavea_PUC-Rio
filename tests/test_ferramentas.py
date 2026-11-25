"""Testes dos scripts de apoio (diagnóstico e geração de demanda), sem precisar do SUMO."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
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
            # [fase0, fase1, pode_trocar, dens_norte, dens_leste, fila_norte, fila_leste, saida_norte, saida_leste]
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
        self.assertAlmostEqual(float(env._observar("cruz")[-2]), 0.0)
        env.fechar()
