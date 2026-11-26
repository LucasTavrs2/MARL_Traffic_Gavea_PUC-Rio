# Guia: do clone ao primeiro treino

Passo a passo para rodar no Windows (PowerShell), do repositório vazio até a comparação do IDQN com os baselines.

O treino usa **demanda sintética**. O resultado mostra se o ambiente e o algoritmo funcionam, não o ganho real na Gávea: isso só vem com os dados de contagem (Fase 3 do roadmap).

## O que vem no pacote

| Pasta ou arquivo | Para que serve |
| --- | --- |
| `marl_gavea/` | Ambiente sobre o SUMO, baselines, IDQN e leitura de métricas |
| `scripts/checar_ambiente.py` | Confere a instalação e lista os semáforos que viram agentes |
| `scripts/gerar_demanda.py` | Gera os cenários de carros, motos e ônibus |
| `scripts/avaliar.py` | Compara os métodos nos mesmos cenários |
| `scripts/treinar_idqn.py` | Treina o IDQN |
| `tests/` | Testes que rodam sem o SUMO |
| `requirements.txt`, `.gitignore` | Versões atualizadas (substituem as antigas) |

## 1. Clonar o repositório

```powershell
cd $env:USERPROFILE\Desktop
git clone https://github.com/LucasTavrs2/MARL_Traffic_Gavea_PUC-Rio.git
cd MARL_Traffic_Gavea_PUC-Rio
git config user.name "Lucas Tavares"
git config user.email "lucastavrs2@gmail.com"
dir maps\gavea_real
```

A pasta `maps\gavea_real` precisa ter `gavea.net.xml`, `tls_grupos.nod.xml`, `gavea.osm.xml` e `build.ps1`. Se estiver vazia, o push do PC do trabalho não foi feito.

## 2. Criar o ambiente Python

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Se o PowerShell recusar o `Activate.ps1`, libere scripts só para o seu usuário e tente de novo:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. Colocar o pacote e instalar

Com o `.venv` ativo e na raiz do repositório:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\pacote_treino_gavea.zip" -DestinationPath . -Force
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Os 12 testes devem terminar com `OK`. Eles usam um cruzamento falso, então não dependem do SUMO.

Os scripts acham o SUMO sozinhos. Só precisa do `$env:SUMO_HOME` se for chamar ferramentas do SUMO direto no terminal.

## 4. Conferir o ambiente

Se a rede mudou (atualização do `build.ps1` ou do `tls_grupos.nod.xml`), reconstrua antes, de qualquer pasta:

```powershell
.\maps\gavea_real\build.ps1
```

Ele deve terminar com "10 controladores de semáforo". Depois disso, a demanda tem de ser gerada de novo (passo 5).


```powershell
python scripts/checar_ambiente.py
```

Mostra a tabela de semáforos, quantas fases verdes cada um tem e quais viram agentes. Semáforos com uma fase verde só, como travessias no meio da quadra, ficam no programa original em todos os métodos, para a comparação ser justa.

## 5. Gerar a demanda e calibrar o volume

```powershell
python scripts/gerar_demanda.py
python scripts/diagnosticar.py
```

O `gerar_demanda.py` cria 26 cenários em `demanda\`: 20 de treino (volume sorteado entre 60% e 160% do calibrado, para o agente aprender também com trânsito leve e pesado), 1 de validação e 5 de teste. Depois de calibrada, a escala global fica guardada: rodar o script de novo sem `--escala-global` repete a mesma. As origens e destinos favorecem as vias com mais faixas e mais rápidas (as avenidas), em vez de espalhar viagens igualmente pelas ruas residenciais (`--sem-ponderar` volta ao sorteio uniforme).

O volume sintético ainda não vem de contagem real, então ele precisa ser calibrado para a rede escoar. O `diagnosticar.py` roda o atuado com 30% a 100% do volume, mostra onde os veículos ficam presos e termina com uma linha assim:

```
Regenere a demanda com:  python scripts/gerar_demanda.py --escala-global 0.55
```

Rode essa linha e confirme que o volume novo escoa:

```powershell
python scripts/gerar_demanda.py --escala-global 0.55
python scripts/diagnosticar.py --escalas 0.8 1.0 1.2
python scripts/checar_ambiente.py
```

Com 100% a meta é pelo menos 95% das viagens concluídas e quase nenhum teleporte. Com mais volume a rede começa a saturar, e é de propósito: o treino vai até 160% para o agente aprender a lidar com isso.

Para ver um cenário no sumo-gui:

```powershell
sumo-gui -n maps\gavea_real\gavea.net.xml -r demanda\teste_s101\carro.rou.xml,demanda\teste_s101\moto.rou.xml,demanda\teste_s101\onibus.rou.xml
```

## 6. Rodar os baselines

```powershell
python scripts/avaliar.py
```

Roda atuado, aleatório e max-pressure nos 5 cenários de teste (15 simulações) e imprime o resumo. Os arquivos vão para `resultados\avaliacao\<data_hora>\`.

## 6.1 Como ler o diagnóstico

Teleporte é o SUMO removendo um veículo parado há 300 s. Se até o atuado tiver dezenas ou centenas por cenário, a rede está travando e o treino não vale a pena ainda. O diagnóstico lista os pontos onde os veículos ficam presos, com nome da rua e coordenadas (cole no Google Maps). A tabela completa fica em `resultados\diagnostico\<data_hora>\teleportes.csv`.

- **Trava já com 30% do volume:** defeito na rede (trechos curtos demais, faixa sem conexão). Corrige-se no `build.ps1` ou no netedit.
- **Só trava com o volume cheio:** a demanda sintética está pesada demais para a rede; a escala global resolve.
- **Motivo `wrong lane`:** o veículo não consegue chegar à faixa de que precisa, quase sempre por defeito na rede.
- **Motivo `yield`:** quem vem da rua lateral não acha brecha para entrar na avenida.
- **Motivo `jam`:** fila parada, normalmente por causa de um bloqueio mais à frente.

Se mudar a rede (`build.ps1` ou netedit), gere a demanda de novo: as rotas antigas usam trechos que podem ter mudado.

## 7. Treino de teste

```powershell
python scripts/treinar_idqn.py --episodios 3 --avaliar-cada 1 --saida resultados\idqn_teste
```

Anote os segundos por episódio que aparecem na tela. Eles dão a estimativa do treino longo: segundos por episódio × 120 (100 episódios mais as 20 validações).

## 8. Treino longo

Antes, deixe o PC sem suspender:

```powershell
powercfg /change standby-timeout-ac 0
python scripts/treinar_idqn.py --episodios 100
```

Depois do treino, volte a suspensão para o valor que usava (por exemplo, `powercfg /change standby-timeout-ac 30`).

O `Ctrl+C` interrompe com segurança: perde só o episódio em andamento. Ficam salvos em `resultados\idqn\`:

| Arquivo | Conteúdo |
| --- | --- |
| `treino.csv` | Uma linha por episódio de treino |
| `validacao.csv` | Política sem exploração no cenário de validação, com 100% e 150% do volume, a cada 5 episódios |
| `melhor.npz` | Redes do episódio com menor atraso total médio na validação (média dos dois volumes; para o `avaliar.py`) |
| `ultimo.npz` | Redes do último episódio completo (para o `avaliar.py`) |
| `checkpoint.npz` | Tudo para continuar: redes, otimizador, memória de replay, epsilon e sorteador |
| `checkpoint_melhor.npz` | O mesmo, no episódio do melhor resultado na validação |
| `colapsos\epNNNN.npz` | Redes de cada validação que passou do dobro do melhor resultado (para o `investigar.py`) |

Os dois checkpoints crescem até a memória de replay encher (50 mil transições por semáforo) e podem somar algumas centenas de MB. Eles ficam fora do Git.

A cada episódio o terminal mostra a **perda** (o erro médio da rede). Com a recompensa entre -1 e 0 ela deve ficar abaixo de 1. Se passar de 10, aparece um aviso: os valores Q estão divergindo e a política vai piorar. Foi o que aconteceu na v2, em que a perda chegou a bilhões. O `treino.csv` também guarda o `q_medio`, que deve ficar entre -20 e 0.

## 8.1 Continuar, voltar ao melhor ou recomeçar

O `--episodios` é sempre o **total**, contando os já feitos.

| Situação | Comando |
| --- | --- |
| Parou no meio (Ctrl+C, queda de luz) | `python scripts/treinar_idqn.py --episodios 100 --continuar` |
| Terminou os 100 e a validação ainda estava melhorando | `python scripts/treinar_idqn.py --episodios 150 --continuar` |
| A validação piorou depois de um pico | `python scripts/treinar_idqn.py --episodios 150 --continuar melhor` |
| Não ficou bom e quer começar do zero | `python scripts/treinar_idqn.py --episodios 100 --recomecar` |
| Quer manter um treino e testar outro em paralelo | `python scripts/treinar_idqn.py --episodios 100 --saida resultados\idqn_espera --recompensa espera` |

- **Continuar** segue com tudo como estava: a memória de replay, a taxa de exploração e os contadores. O resultado é idêntico ao de um treino que nunca parou (isso está coberto por teste).
- **Continuar do melhor** volta ao checkpoint do melhor episódio. As linhas posteriores saem do `treino.csv` e do `validacao.csv`, e os arquivos originais ficam salvos com o sufixo `_antes_de_<data>`.
- **Recomeçar** não apaga nada: renomeia a pasta antiga para `idqn_antigo_<data>`.
- Ao estender um treino, o epsilon já está no mínimo (0,05). Os episódios extras servem para refinar a política, não para explorar de novo.
- A duração do episódio e a recompensa ficam as do treino original, mesmo que você passe outros valores.

## 8.2 Configuração recomendada

1. **Episódio de 4.200 s simulados** (o padrão): uma hora de chegadas, que cobre um pico inteiro, mais 10 minutos para a rede esvaziar. Episódio mais longo deixa cada um mais lento sem trazer variedade: o que ajuda é ter mais episódios em cenários diferentes.
2. **1.000 episódios.** No primeiro treino longo a validação estabilizou por volta do episódio 1.000; 1.500 não trouxe ganho.
3. **Para o resultado ser confiável, treine com 3 sementes** e avalie cada uma nos 5 cenários de teste e no teste de estresse. Podem rodar ao mesmo tempo, cada uma num terminal:

```powershell
python scripts/treinar_idqn.py --episodios 1000 --semente 0 --saida resultados\idqn_s0
python scripts/treinar_idqn.py --episodios 1000 --semente 1 --saida resultados\idqn_s1
python scripts/treinar_idqn.py --episodios 1000 --semente 2 --saida resultados\idqn_s2
```

Se as três sementes baterem o atuado e o max-pressure, inclusive com volume maior, o resultado não foi sorte de uma rodada.

## 9. Comparação final

```powershell
python scripts/avaliar.py --metodos atuado aleatorio max_pressao idqn --modelo resultados\idqn\melhor.npz
```

Para assistir à política treinada no sumo-gui:

```powershell
python scripts/avaliar.py --metodos idqn --modelo resultados\idqn\melhor.npz --gui
```

## 9.1 Teste de estresse

Avalia os métodos com mais volume do que o usado no treino, sem treinar de novo. O volume extra vem
do `--scale` do SUMO, que duplica parte dos veículos dos cenários de teste.

```powershell
python scripts/avaliar.py --metodos atuado max_pressao idqn --modelo resultados\idqn\melhor.npz --escalas 1.0 1.25 1.5
```

Sai uma tabela por volume. O que importa é se o IDQN continua à frente do max-pressure quando o
trânsito pesa, e se ele trava (tempo perdido e teleportes disparando) antes dos outros.

## 9.2 Investigar um colapso

Roda um método em todos os cenários (treino, validação e teste), marca as rodadas que travaram e
detalha a pior: fila de cada semáforo ao longo do tempo, a rua que ficou mais tempo sem verde com
carros esperando, trocas forçadas pelo verde máximo e os pontos de teleporte.

```powershell
python scripts/investigar.py --modelo resultados\idqn\melhor.npz --escalas 1.0 1.25 1.5
python scripts/investigar.py --metodo max_pressao --escalas 1.5
```

Para olhar uma validação que já colapsou durante o treino (as pastas ficam em `resultados\idqn\validacao`):

```powershell
python scripts/investigar.py --pasta resultados\idqn\validacao\ep1495
```

Daqui em diante, quando a validação passar do dobro do melhor resultado, o treino guarda as redes daquele momento em
`colapsos\epNNNN.npz`, e dá para reproduzir o colapso com `--modelo resultados\idqn\colapsos\epNNNN.npz --conjuntos validacao`.

## 10. Subir o código

```powershell
git add .
git status
git commit -m "Ambiente de treino: baselines e IDQN"
git push
```

No `git status`, confira que não aparecem `.venv`, `demanda` nem `resultados`.

## Como ler os resultados

- **Atraso total médio** é a métrica principal: o tempo perdido na rede mais a espera para entrar nela, dividido por todos os veículos gerados, inclusive os que não chegaram a entrar.
- **Tempo perdido médio**: segundos que cada veículo perdeu em relação a andar livre, contando só quem entrou na rede. Sozinho, engana: um controle que deixa a fila chegar à borda da rede segura carros do lado de fora, e essa espera não entra no tempo perdido.
- **vs atuado / vs max_pressao**: variação do atraso total em relação a esses métodos, quando eles estão na mesma rodada do `avaliar.py`.
- **Teleportes** acima de zero indicam travamento: o SUMO remove um veículo parado há 300 s. Se até o atuado tiver muitos, a demanda está pesada demais para a rede.
- **Concluídos %** é a fração de veículos que chegou ao destino dentro do episódio.
- **Sucesso**: o IDQN abaixo do atuado e do max-pressure no atraso total, sem mais teleportes. Bater o aleatório não basta.
- **Curva de aprendizado**: abra `treino.csv` no Excel e veja o `atraso_total_medio_s` cair conforme o `epsilon` desce.

## Parâmetros usados

| Parâmetro | Valor |
| --- | --- |
| Intervalo entre decisões | 5 s |
| Amarelo | 3 s |
| Verde mínimo e máximo | 10 s e 90 s |
| Vermelho máximo | 120 s: faixa com carros parados há 120 s sem verde é atendida na troca seguinte (vale para aleatório, max-pressure e IDQN) |
| Troca forçada | Vai para a fase que atende a faixa que espera há mais tempo, não para a fase seguinte |
| Zona de detecção | Até 100 m antes de cada semáforo (parando no semáforo anterior) e até o próximo semáforo depois dele; o OSM corta as vias em trechos de poucos metros, então olhar só a faixa que encosta no semáforo esconderia a fila |
| Observação | Fase atual, se já pode trocar, densidade, fila e tempo sem verde na zona de cada faixa de entrada, densidade na zona de cada faixa de saída |
| Volume no treino | 20 cenários entre 60% e 160% do volume calibrado; validação com 100% e 150% |
| Max-pressure | Pressão normalizada pela capacidade (ocupação da entrada menos ocupação da saída), mantém a fase atual em empate |
| Recompensa | Fração das zonas antes do semáforo ocupada por veículos parados, entre -1 e 0 (`--recompensa espera` usa o tempo de espera) |
| Episódio | 4.200 s: 3.600 s de chegadas mais 600 s para esvaziar |
| Mistura de veículos | Cerca de 80% carros, 15% motos e 5% ônibus (provisória) |
| IDQN | Double DQN por semáforo, rede 64×64, replay de 50 mil, epsilon de 1,0 a 0,05 na primeira metade do treino, gamma 0,95, taxa de aprendizado 0,0001, rede-alvo copiada a cada 1.000 atualizações, alvos cortados na faixa possível de Q (-20 a 0) |

## Problemas comuns

| Mensagem | O que fazer |
| --- | --- |
| `SUMO não encontrado` | Ative o `.venv` e rode `pip install -r requirements.txt` |
| `randomTrips falhou` | Mande a saída inteira para análise |
| `O modelo não bate com a rede` | A rede ou o `tls_grupos.nod.xml` mudou depois do treino: treine de novo |
| `A pasta ... já tem um treino` | Escolha `--continuar`, `--continuar melhor`, `--recomecar` ou outra `--saida` |
| `O checkpoint não bate com a rede atual` | A rede mudou depois do treino: use `--recomecar` |
| Erro de conexão com o TraCI | Feche janelas abertas do sumo-gui e rode de novo |

## O que mandar depois

1. A saída do `checar_ambiente.py` com a demanda já gerada.
2. O resumo do `avaliar.py` com os baselines.
3. Os segundos por episódio do treino de teste.
