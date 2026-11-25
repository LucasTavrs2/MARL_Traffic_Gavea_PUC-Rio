"""Ambiente multiagente sobre o SUMO: um agente por semáforo com duas ou mais fases verdes.

Regras de segurança aplicadas a qualquer controlador (aleatório, max-pressure, IDQN):
- toda troca de fase passa por amarelo;
- uma fase verde dura pelo menos `verde_min` segundos;
- uma fase verde dura no máximo `verde_max` segundos (depois disso a troca é forçada).

Semáforos com uma única fase verde (por exemplo, travessias de pedestre no meio da quadra)
não viram agentes: continuam rodando o programa original do SUMO em todos os métodos.

Fila, densidade e pressão são medidas numa zona de cerca de 100 m antes (e depois) de cada
semáforo, e não só na faixa que encosta nele, que muitas vezes tem poucos metros (veja zonas.py).

Observação de cada agente: fase atual (one-hot), se já pode trocar, e, por faixa, densidade e fila
na zona de entrada e densidade na zona de saída (para enxergar quando a via à frente está cheia).
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
        recompensa: str = "fila",
        controlar: bool = True,
        gui: bool = False,
        semente: int = 0,
        saida=None,
        conectar=None,
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
        self.recompensa = recompensa
        self.controlar = controlar
        self.gui = gui
        self.semente = semente
        self.saida = Path(saida) if saida else None
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
            "--no-warnings", "true",
        ]
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
            traci.start(self._comando(), label=rotulo)
            self.conn = traci.getConnection(rotulo)

        todos = descobrir_semaforos(self.conn)
        self.semaforos = {s.id: s for s in todos if s.n_fases >= 2}
        self.fixos = sorted(s.id for s in todos if s.n_fases < 2)
        self.agentes = sorted(self.semaforos)
        self.n_acoes = {a: self.semaforos[a].n_fases for a in self.agentes}
        self.dim_obs = {
            a: self.semaforos[a].n_fases + 1 + 2 * len(self.semaforos[a].entradas) + len(self.semaforos[a].saidas)
            for a in self.agentes
        }
        self._montar_zonas()

        t = self.conn.simulation.getTime()
        for s in self.semaforos.values():
            s.fase, s.inicio_verde, s.fim_amarelo, s.destino, s.trocas = 0, t, None, None, 0
            if self.controlar:
                self.conn.trafficlight.setRedYellowGreenState(s.id, s.fases[0])
        self._ler_faixas()
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
        if acao == s.fase and self.verde_max and tempo_verde >= self.verde_max:
            acao = (s.fase + 1) % s.n_fases
        if acao != s.fase and tempo_verde >= self.verde_min:
            self.conn.trafficlight.setRedYellowGreenState(s.id, s.estado_amarelo(s.fase, acao))
            s.fim_amarelo = t + self.amarelo
            s.destino = acao

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
        return np.concatenate([fase, pode_trocar, densidade, fila, self._densidade_saida(s)]).astype(np.float32)

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
