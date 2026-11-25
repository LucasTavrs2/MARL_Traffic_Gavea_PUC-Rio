"""Ambiente multiagente sobre o SUMO: um agente por semáforo com duas ou mais fases verdes.

Regras de segurança aplicadas a qualquer controlador (aleatório, max-pressure, IDQN):
- toda troca de fase passa por amarelo;
- uma fase verde dura pelo menos `verde_min` segundos;
- uma fase verde dura no máximo `verde_max` segundos (depois disso a troca é forçada);
- nenhuma faixa com carros parados fica mais de `vermelho_max` segundos sem verde: passado esse
  tempo, a próxima troca vai obrigatoriamente para uma fase que a atende.
Nas trocas forçadas (verde máximo ou vermelho máximo), a fase escolhida é a que atende a faixa que
espera há mais tempo, e não simplesmente a seguinte; senão, fases que só atendem um movimento
pequeno podiam nunca ser alcançadas.

Semáforos com uma única fase verde (por exemplo, travessias de pedestre no meio da quadra)
não viram agentes: continuam rodando o programa original do SUMO em todos os métodos.

Fila, densidade e pressão são medidas numa zona de cerca de 100 m antes (e depois) de cada
semáforo, e não só na faixa que encosta nele, que muitas vezes tem poucos metros (veja zonas.py).

Observação de cada agente: fase atual (one-hot), se já pode trocar, e, por faixa, densidade e fila
na zona de entrada, densidade na zona de saída (para enxergar quando a via à frente está cheia) e
há quanto tempo cada faixa de entrada com carros parados está sem verde (em frações do vermelho máximo).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .comum import binario_sumo
from .metricas import resumo_saida
from .zonas import zonas_da_rede

METROS_POR_VEICULO = 7.5


@dataclass
class Semaforo:
    id: str
    fases: list  # estados verdes distintos, ex.: "GGrr"
    links: list  # por índice de link: (faixa_entrada, faixa_saida) ou None
    entradas: list
    saidas: list
    capacidade: dict  # faixa -> número de veículos que cabem nela
    fase: int = 0
    inicio_verde: float = 0.0
    fim_amarelo: float | None = None
    destino: int | None = None
    trocas: int = 0
    forcadas: int = 0  # trocas impostas pelo verde máximo
    por_espera: int = 0  # trocas impostas pelo vermelho máximo
    servidas: list = field(default_factory=list)  # por fase verde: faixas de entrada que ela libera
    ultimo_verde: dict = field(default_factory=dict)  # faixa de entrada -> último instante com verde ou sem fila
    zona_entrada: dict = field(default_factory=dict)  # faixa de entrada -> faixas da zona antes do semáforo
    zona_saida: dict = field(default_factory=dict)  # faixa de saída -> faixas da zona depois do semáforo
    cap_zona: dict = field(default_factory=dict)  # faixa de entrada -> veículos que cabem na zona
    cap_saida: dict = field(default_factory=dict)  # faixa de saída -> veículos que cabem na zona
    montante: list = field(default_factory=list)  # todas as faixas antes do semáforo, sem repetição

    @property
    def n_fases(self) -> int:
        return len(self.fases)

    def estado_amarelo(self, de: int, para: int) -> str:
        atual, proximo = self.fases[de], self.fases[para]
        return "".join(
            "y" if atual[i] in "Gg" and proximo[i] in "rs" else atual[i] for i in range(len(atual))
        )


def descobrir_semaforos(conn) -> list[Semaforo]:
    semaforos = []
    for tl in conn.trafficlight.getIDList():
        logicas = conn.trafficlight.getAllProgramLogics(tl)
        programa = conn.trafficlight.getProgram(tl)
        logica = next((l for l in logicas if l.programID == programa), logicas[0])
        fases = []
        for fase in logica.phases:
            estado = fase.state
            if "y" in estado or "Y" in estado or "u" in estado:
                continue
            if not any(c in "Gg" for c in estado):
                continue
            if estado not in fases:
                fases.append(estado)
        links = []
        for grupo in conn.trafficlight.getControlledLinks(tl):
            links.append((grupo[0][0], grupo[0][1]) if grupo else None)
        entradas = list(dict.fromkeys(l[0] for l in links if l))
        saidas = list(dict.fromkeys(l[1] for l in links if l))
        capacidade = {
            f: max(1.0, conn.lane.getLength(f) / METROS_POR_VEICULO) for f in entradas + saidas
        }
        semaforos.append(Semaforo(tl, fases, links, entradas, saidas, capacidade))
    return semaforos


class AmbienteGavea:
    def __init__(
        self,
        rede,
        rotas,
        *,
        duracao: int = 4200,
        delta: int = 5,
        amarelo: int = 3,
        verde_min: int = 10,
        verde_max: int = 90,
        vermelho_max: int = 120,
        recompensa: str = "fila",
        controlar: bool = True,
        gui: bool = False,
        semente: int = 0,
        saida=None,
        conectar=None,
        escala: float = 1.0,
        avisos: bool = False,
    ):
        if delta <= amarelo:
            raise ValueError("delta precisa ser maior que o tempo de amarelo")
        if recompensa not in ("fila", "espera"):
            raise ValueError("recompensa deve ser 'fila' ou 'espera'")
        self.rede = Path(rede)
        self.rotas = [str(r) for r in rotas]
        self.duracao = duracao
        self.delta = delta
        self.amarelo = amarelo
        self.verde_min = verde_min
        self.verde_max = verde_max
        self.vermelho_max = vermelho_max
        self.recompensa = recompensa
        self.controlar = controlar
        self.gui = gui
        self.semente = semente
        self.saida = Path(saida) if saida else None
        self.escala = escala  # multiplica a demanda do cenário (--scale do SUMO), para testes de estresse
        self.avisos = avisos  # grava os avisos do SUMO (teleportes) em saida/sumo.log
        self._conectar = conectar  # usado nos testes para trocar o SUMO por um simulador falso
        self.conn = None
        self.semaforos: dict[str, Semaforo] = {}
        self.fixos: list[str] = []
        self.agentes: list[str] = []
        self.n_acoes: dict[str, int] = {}
        self.dim_obs: dict[str, int] = {}

    # ------------------------------------------------------------------ ciclo de vida
    def _comando(self) -> list[str]:
        cmd = [binario_sumo(self.gui), "-n", str(self.rede)]
        if self.rotas:
            cmd += ["-r", ",".join(self.rotas)]
        cmd += [
            "--seed", str(self.semente),
            "--time-to-teleport", "300",
            "--waiting-time-memory", "1000",
            "--no-step-log", "true",
        ]
        if self.escala != 1.0:
            cmd += ["--scale", f"{self.escala:g}"]
        if self.avisos and self.saida:
            self.saida.mkdir(parents=True, exist_ok=True)
            cmd += ["--log", str(self.saida / "sumo.log")]
        else:
            cmd += ["--no-warnings", "true"]
        if self.saida:
            self.saida.mkdir(parents=True, exist_ok=True)
            cmd += [
                "--tripinfo-output", str(self.saida / "tripinfo.xml"),
                "--tripinfo-output.write-unfinished", "true",
                "--statistic-output", str(self.saida / "estatisticas.xml"),
            ]
        if self.gui:
            cmd += ["--start", "true", "--quit-on-end", "true", "--delay", "50"]
        return cmd

    def reset(self) -> dict:
        self.fechar()
        if self._conectar is not None:
            self.conn = self._conectar(self)
        else:
            import traci

            rotulo = f"gavea_{uuid.uuid4().hex[:8]}"
            if self.avisos and self.saida:
                # os avisos vão para saida/sumo.log; sem isso o SUMO também os despeja no terminal
                import subprocess

                from sumolib.miscutils import getFreeSocketPort

                porta = getFreeSocketPort()
                processo = subprocess.Popen(self._comando() + ["--remote-port", str(porta)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                traci.init(porta, label=rotulo, proc=processo)
            else:
                traci.start(self._comando(), label=rotulo)
            self.conn = traci.getConnection(rotulo)

        todos = descobrir_semaforos(self.conn)
        self.semaforos = {s.id: s for s in todos if s.n_fases >= 2}
        self.fixos = sorted(s.id for s in todos if s.n_fases < 2)
        self.agentes = sorted(self.semaforos)
        self.n_acoes = {a: self.semaforos[a].n_fases for a in self.agentes}
        self.dim_obs = {
            a: self.semaforos[a].n_fases + 1 + 3 * len(self.semaforos[a].entradas) + len(self.semaforos[a].saidas)
            for a in self.agentes
        }
        self._montar_zonas()

        t = self.conn.simulation.getTime()
        for s in self.semaforos.values():
            s.fase, s.inicio_verde, s.fim_amarelo, s.destino, s.trocas, s.forcadas = 0, t, None, None, 0, 0
            s.por_espera = 0
            s.ultimo_verde = {f: t for f in s.entradas}
            s.servidas = [
                {s.links[i][0] for i, c in enumerate(estado) if c in "Gg" and i < len(s.links) and s.links[i]}
                for estado in s.fases
            ]
            if self.controlar:
                self.conn.trafficlight.setRedYellowGreenState(s.id, s.fases[0])
        self._ler_faixas()
        self._atualizar_esperas(t)
        self._espera_anterior = {a: self._espera_total(a) for a in self.agentes}
        self._soma_fila = 0.0
        self._decisoes = 0
        return {a: self._observar(a) for a in self.agentes}

    def fechar(self) -> dict:
        if self.conn is None:
            return {}
        try:
            self.conn.close()
        finally:
            self.conn = None
        resumo = resumo_saida(self.saida) if self.saida else {}
        if self._decisoes:
            resumo["fila_media_veic"] = self._soma_fila / self._decisoes
        if self.semaforos:
            resumo["trocas_de_fase"] = sum(s.trocas for s in self.semaforos.values())
            resumo["trocas_forcadas"] = sum(s.forcadas for s in self.semaforos.values())
            resumo["trocas_por_espera"] = sum(s.por_espera for s in self.semaforos.values())
        return resumo

    # ------------------------------------------------------------------ passo
    def step(self, acoes: dict | None = None):
        t = self.conn.simulation.getTime()
        if self.controlar and acoes:
            for agente, acao in acoes.items():
                self._aplicar(self.semaforos[agente], int(acao), t)

        for _ in range(self.delta):
            self.conn.simulationStep()
            t = self.conn.simulation.getTime()
            if self.controlar:
                for s in self.semaforos.values():
                    if s.fim_amarelo is not None and t >= s.fim_amarelo:
                        s.fase, s.destino, s.fim_amarelo = s.destino, None, None
                        s.inicio_verde = t
                        s.trocas += 1
                        self.conn.trafficlight.setRedYellowGreenState(s.id, s.fases[s.fase])
            if t >= self.duracao:
                break

        self._ler_faixas()
        self._atualizar_esperas(t)
        obs = {a: self._observar(a) for a in self.agentes}
        recompensas = {a: self._recompensa(a) for a in self.agentes}
        fila = sum(self._fila(a) for a in self.agentes)
        self._soma_fila += fila
        self._decisoes += 1
        return obs, recompensas, t >= self.duracao, {"tempo": t, "fila_total": fila}

    def _aplicar(self, s: Semaforo, acao: int, t: float) -> None:
        if not 0 <= acao < s.n_fases:
            raise ValueError(f"Ação {acao} inválida para {s.id} ({s.n_fases} fases)")
        if s.fim_amarelo is not None:
            return  # já está trocando
        tempo_verde = t - s.inicio_verde
        if tempo_verde < self.verde_min:
            return
        famintas = self._famintas(s, t)
        if famintas:
            if not famintas & s.servidas[acao]:
                acao = self._fase_mais_esperada(s, t)
                s.por_espera += 1
        elif acao == s.fase and self.verde_max and tempo_verde >= self.verde_max:
            acao = self._fase_mais_esperada(s, t)
            s.forcadas += 1
        if acao != s.fase:
            self.conn.trafficlight.setRedYellowGreenState(s.id, s.estado_amarelo(s.fase, acao))
            s.fim_amarelo = t + self.amarelo
            s.destino = acao

    # ------------------------------------------------------------------ espera por verde
    def _parados(self, s: Semaforo, faixa: str) -> float:
        return self._soma(self._parado, s.zona_entrada[faixa])

    def _atualizar_esperas(self, t: float) -> None:
        """Marca as faixas que estão com verde agora ou sem ninguém parado."""
        if not self.controlar:
            return
        for s in self.semaforos.values():
            abertas = s.servidas[s.fase] if s.fim_amarelo is None else set()
            for f in s.entradas:
                if f in abertas or self._parados(s, f) == 0:
                    s.ultimo_verde[f] = t

    def _famintas(self, s: Semaforo, t: float) -> set:
        """Faixas com carros parados há `vermelho_max` segundos ou mais sem verde."""
        if not self.vermelho_max:
            return set()
        return {
            f for f in s.entradas
            if f not in s.servidas[s.fase] and t - s.ultimo_verde[f] >= self.vermelho_max and self._parados(s, f) > 0
        }

    def _fase_mais_esperada(self, s: Semaforo, t: float) -> int:
        """Entre as outras fases, a que atende a faixa com carros parados que espera há mais tempo
        (desempate: mais carros parados). Sem ninguém esperando, a fase seguinte."""
        melhor, chave_melhor = (s.fase + 1) % s.n_fases, (-1.0, 0.0)
        for p in range(s.n_fases):
            if p == s.fase:
                continue
            parados = {f: self._parados(s, f) for f in s.servidas[p]}
            esperas = [t - s.ultimo_verde[f] for f, n in parados.items() if n > 0]
            chave = (max(esperas) if esperas else -1.0, sum(parados.values()))
            if chave > chave_melhor:
                melhor, chave_melhor = p, chave
        return melhor

    # ------------------------------------------------------------------ zonas de detecção
    def _montar_zonas(self) -> None:
        zonas = zonas_da_rede(self.rede)
        for s in self.semaforos.values():
            s.zona_entrada = {f: zonas.montante(f) if zonas else [f] for f in s.entradas}
            s.zona_saida = {f: zonas.jusante(f) if zonas else [f] for f in s.saidas}
            s.cap_zona = {
                f: max(s.capacidade[f], (zonas.tamanho(z) if zonas else 0.0) / METROS_POR_VEICULO)
                for f, z in s.zona_entrada.items()
            }
            s.cap_saida = {
                f: max(s.capacidade[f], (zonas.tamanho(z) if zonas else 0.0) / METROS_POR_VEICULO)
                for f, z in s.zona_saida.items()
            }
            s.montante = sorted({f for z in s.zona_entrada.values() for f in z})
        self._faixas = sorted(
            {f for s in self.semaforos.values() for z in (*s.zona_entrada.values(), *s.zona_saida.values()) for f in z}
        )

    def _ler_faixas(self) -> None:
        """Lê uma vez por decisão o estado de todas as faixas usadas por observação, recompensa e pressão."""
        lane = self.conn.lane
        self._veic = {f: lane.getLastStepVehicleNumber(f) for f in self._faixas}
        self._parado = {f: lane.getLastStepHaltingNumber(f) for f in self._faixas}
        self._espera = {f: lane.getWaitingTime(f) for f in self._faixas} if self.recompensa == "espera" else {}

    @staticmethod
    def _soma(tabela: dict, faixas) -> float:
        return float(sum(tabela[f] for f in faixas))

    # ------------------------------------------------------------------ observação e recompensa
    def _observar(self, agente: str) -> np.ndarray:
        s = self.semaforos[agente]
        t = self.conn.simulation.getTime()
        fase = np.zeros(s.n_fases, dtype=np.float32)
        fase[s.fase] = 1.0
        pode_trocar = [1.0 if t - s.inicio_verde >= self.verde_min else 0.0]
        densidade = [min(1.0, self._soma(self._veic, s.zona_entrada[f]) / s.cap_zona[f]) for f in s.entradas]
        fila = [min(1.0, self._soma(self._parado, s.zona_entrada[f]) / s.cap_zona[f]) for f in s.entradas]
        espera = [
            min(1.0, (t - s.ultimo_verde[f]) / self.vermelho_max) if self.vermelho_max else 0.0 for f in s.entradas
        ]
        return np.concatenate([fase, pode_trocar, densidade, fila, self._densidade_saida(s), espera]).astype(np.float32)

    def _densidade_saida(self, s: Semaforo) -> list[float]:
        return [min(1.0, self._soma(self._veic, s.zona_saida[f]) / s.cap_saida[f]) for f in s.saidas]

    def _fila(self, agente: str) -> float:
        return self._soma(self._parado, self.semaforos[agente].montante)

    def _espera_total(self, agente: str) -> float:
        return self._soma(self._espera, self.semaforos[agente].montante) if self._espera else 0.0

    def _recompensa(self, agente: str) -> float:
        if self.recompensa == "fila":
            return -self._fila(agente) / 10.0
        atual = self._espera_total(agente)
        r = (self._espera_anterior[agente] - atual) / 100.0
        self._espera_anterior[agente] = atual
        return r

    # ------------------------------------------------------------------ usado pelo max-pressure
    def pressoes(self, agente: str) -> list[float]:
        """Pressão de cada fase verde, normalizada pela capacidade (como no PressLight): soma, nos
        movimentos que a fase libera, da ocupação da zona de entrada menos a da zona de saída.

        Com contagens brutas, um trecho curto entre dois semáforos parece vazio mesmo lotado
        (cabem 2 carros) e o controlador continua mandando carros para lá até travar o cruzamento.
        Em ocupação, trecho lotado vale 1 e segura a fase que alimenta ele.
        """
        s = self.semaforos[agente]
        ocup_entrada = {f: min(1.0, self._soma(self._veic, s.zona_entrada[f]) / s.cap_zona[f]) for f in s.entradas}
        ocup_saida = dict(zip(s.saidas, self._densidade_saida(s)))
        pressoes = []
        for estado in s.fases:
            p = 0.0
            for i, c in enumerate(estado):
                if c in "Gg" and i < len(s.links) and s.links[i]:
                    entrada, saida = s.links[i]
                    p += ocup_entrada[entrada] - ocup_saida[saida]
            pressoes.append(p)
        return pressoes


def rodar_episodio(env: AmbienteGavea, politica, aprender=None) -> dict:
    """Roda um episódio inteiro. `politica(env, obs) -> {agente: ação}`.
    `aprender(obs, acoes, recompensas, obs_seguinte)` é chamado a cada decisão, se for dado."""
    obs = env.reset()
    fim = False
    try:
        while not fim:
            acoes = politica(env, obs)
            obs_seguinte, recompensas, fim, _ = env.step(acoes)
            if aprender is not None:
                aprender(obs, acoes, recompensas, obs_seguinte)
            obs = obs_seguinte
    finally:
        resumo = env.fechar()
    return resumo
