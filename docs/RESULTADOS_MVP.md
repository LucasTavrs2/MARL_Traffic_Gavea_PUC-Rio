# Resultados do MVP (mapa gavea_real, IDQN v3.1)

Registro de referência para comparar com o mapa ampliado. Código no estado da tag `mvp-v3.1`
(27/11/2026). Avaliação final em `resultados\avaliacao\20261127_094014`.

## Configuração

**Mapa:** `maps/gavea_real`, recorte de 1,19 km × 0,44 km em torno da PUC-Rio, da Marquês de São Vicente
até a Av. Bartolomeu Mitre. São 10 controladores de semáforo, agrupados à mão no `tls_grupos.nod.xml`:

- **6 agentes:** msv_duque_estrada, rodrigo_otavio_norte, rodrigo_otavio_mario_ribeiro,
  bm_visconde_albuquerque, bm_mario_ribeiro e bm_tubira;
- **4 fixos**, com uma só fase verde e rodando o programa original: msv_rotatoria_oeste,
  msv_rotatoria_nordeste, msv_travessia_puc e rubens_berardo.

**Demanda:** sintética, feita com o gerador de viagens aleatórias do SUMO (randomTrips): cerca de 80% carros,
15% motos e 5% ônibus. O volume foi calibrado com escala global 0,45, o maior com 95% ou mais dos veículos
concluindo a viagem e até 5 teleportes. Os cenários:

| Conjunto | Cenários | Volume |
| --- | --- | --- |
| Treino | 20 | 0,6 a 1,6 vez o calibrado |
| Validação | 1 (s051) | 100% e 150% |
| Teste | 5 (s101 a s105) | 100%, 125% e 150%; os volumes acima de 100% saem do `--scale` |

Cada episódio simula 4200 s.

**Controle:**
- uma decisão a cada 5 s, com amarelo de 3 s;
- verde mínimo de 10 s e verde máximo de 90 s;
- vermelho máximo de 120 s: a faixa que espera há mais tempo ganha o verde;
- os mesmos limites valem para todos os métodos.

**Observação de cada agente:**
- a fase atual e se ele já pode trocar;
- por faixa de entrada: densidade, fila e tempo sem verde;
- por faixa de saída: densidade.

As faixas de entrada são lidas numa zona de até 100 m antes do semáforo, ou até o semáforo anterior;
as de saída, até o próximo semáforo.

**Recompensa:** menos a fração da zona antes do semáforo ocupada por carros parados, entre -1 e 0.

**Algoritmo:** IDQN (um DQN independente por agente), escrito em numpy:
- rede MLP 64×64, Double DQN e perda Huber;
- Adam com taxa 1e-4 e corte de gradiente em 10;
- gamma 0,95;
- rede-alvo copiada a cada 1000 atualizações;
- alvos de Q limitados a [-20, 0];
- memória de replay de 50 mil transições;
- epsilon caindo linearmente até 0,05 na primeira metade do treino.

**Métrica principal:** atraso total por veículo gerado, isto é, o tempo perdido na rede mais a espera
para entrar nela, contando também quem não entrou.

## Treino (3 sementes × 1500 episódios)

- **Estável nas três sementes:** a perda teve pico de 0,014 e terminou em ~0,0003; o Q médio ficou em
  ~-0,21. Na v2, sem os limites, a perda divergia até ~1e10.
- **Patamar a partir do episódio ~750**, quando o epsilon chega ao mínimo; os últimos 750 episódios não
  melhoraram mais. Numa próxima rodada, 1000 episódios bastam.
- **Os agentes aprenderam a não deixar faixa sem verde:** as trocas impostas pelo vermelho máximo caíram de
  ~60 por rodada no começo para ~7 com 100% do volume e ~19 com 150%. O verde máximo ainda força ~80
  trocas por rodada.

Validação, em atraso total (média de 100% e 150%):

| Semente | Melhor (episódio) | Média dos últimos 750 episódios |
| --- | --- | --- |
| s0 | 64,1 (800) | 77,5 ± 4,1 |
| s1 | 68,5 (1415) | 79,3 ± 4,3 |
| s2 | 69,1 (1455) | 76,9 ± 3,7 |

## Teste (5 cenários, média)

Atraso total (s), com a diferença em relação ao max-pressure entre parênteses:

| Método | 100% | 125% | 150% |
| --- | --- | --- | --- |
| Atuado (lógica padrão do SUMO) | 183,7 (+127%) | 286,8 (+134%) | 379,2 (+137%) |
| Max-pressure | 81,0 | 122,4 | 160,3 |
| IDQN s0 `melhor` | **57,7 (-29%)** | 96,9 (-21%) | **143,4 (-11%)** |
| IDQN s1 `melhor` | 60,5 (-25%) | 108,8 (-11%) | 158,9 (-1%) |
| IDQN s2 `melhor` | 61,7 (-24%) | 103,7 (-15%) | 161,5 (+1%) |
| IDQN, média das 3 sementes | 60,0 (-26%) | 103,1 (-16%) | 154,6 (-4%) |
| IDQN s0 `ultimo` | 84,0 (+4%) | 135,0 (+10%) | 175,6 (+10%) |
| IDQN s1 `ultimo` | 66,5 (-18%) | 119,4 (-3%) | 160,7 (+0%) |
| IDQN s2 `ultimo` | 64,7 (-20%) | 118,2 (-3%) | 171,2 (+7%) |
| IDQN v2 s2 `melhor` | 64,0 (-21%) | **91,1 (-26%)** | 149,0 (-7%) |

Contra o atuado, a média das 3 sementes reduz o atraso em 67%, 64% e 59%.

Tempo perdido (s) e teleportes por rodada:

| Método | 100% | 125% | 150% |
| --- | --- | --- | --- |
| Atuado | 163,3 / 2,0 | 261,3 / 8,6 | 327,7 / 14,8 |
| Max-pressure | 73,8 / 0,0 | 103,3 / 1,0 | 139,1 / 2,6 |
| IDQN s0 `melhor` | 55,4 / 0,2 | 81,9 / 0,0 | 122,5 / 1,8 |
| IDQN s1 `melhor` | 56,6 / 0,0 | 91,5 / 0,4 | 134,3 / 2,4 |
| IDQN s2 `melhor` | 57,2 / 0,0 | 85,4 / 0,4 | 136,7 / 1,8 |

Tempo perdido dos ônibus com 100%: max-pressure 79,4 s; IDQN 64,2 s (s0), 65,5 s (s1) e 67,2 s (s2).

## Conclusões

- **No volume calibrado, o IDQN ganha do max-pressure com folga:** as três sementes ganham nos 5
  cenários de teste (15 de 15 no tempo perdido), sem teleporte e com todos os veículos concluindo.
- **O ganho não vem de segurar carro fora da rede.** A espera para entrar fica em 2 a 5 s no IDQN, contra
  7 s no max-pressure, com 100%. Com 150%, fica em ~21 s nos dois, o que indica que é efeito da demanda.
- **Com mais trânsito, a vantagem encolhe:** com 150%, só a s0 se destaca. A hipótese, ainda a medir, é
  que o agente fica "cego" quando a zona antes do semáforo enche: uma fila maior não muda a observação nem
  a recompensa. O `investigar.py` mede isso (GUIA, seção 9.3).
- **Escolher pelo `melhor.npz` funciona:** a ordem da validação se repetiu no teste, e os `ultimo` saem
  piores. A política oscila entre episódios, então escolher o checkpoint faz parte do método.
- **A v3 trouxe consistência entre sementes, não um pico maior:** a melhor rede da v2 continua
  competitiva e é a melhor de todas com 125%.

## Ressalvas

- O "atuado" é a lógica padrão do SUMO, não o plano semafórico real da CET-Rio. A comparação com o
  controle atual da cidade depende desses planos.
- A demanda é sintética, calibrada só pelo volume total. As contagens reais da CET-Rio devem substituí-la.
- São 5 cenários de teste. A diferença com 100% é consistente cenário a cenário; com 150%, as sementes
  s1 e s2 empatam com o max-pressure.

## Como comparar com o mapa ampliado

Os segundos absolutos não se comparam de um mapa para outro: a rede muda, as viagens ficam mais longas
e a demanda é calibrada de novo (o gerador usa a mesma quantidade de veículos por hora em qualquer rede,
e a escala global sai do `diagnosticar.py` de cada mapa). O que se compara é a diferença relativa no
mesmo mapa e nos mesmos cenários:

- contra o max-pressure: -26% com 100%, -16% com 125% e -4% com 150% (média das 3 sementes);
- contra o atuado: -67%, -64% e -59%;
- a dispersão entre sementes e a diferença entre `melhor` e `ultimo`.
