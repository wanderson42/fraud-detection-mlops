# Dicionário de dados e features

Atualizado em 2026-10-08. Escopo: `silver_v1`, `gold_v1` e `temporal_v1`.
Definições conferidas no código e contratos enviados pelo autor. Este documento
explica os campos; os contratos executáveis continuam definindo schema e ordem.

## Preservação dos dados e escolhas de modelagem

**Nenhuma coluna foi retirada na aquisição Bronze.** O extrator preserva os bytes
dos arquivos diários `.pkl` da fonte fixada. As nove colunas são mantidas na Silver,
com tipos explícitos e sem remoção de linhas, zeros ou extremos aceitos pelo contrato.

A Gold prepara 19 preditores e seis metadados, totalizando 25 colunas físicas.
`TX_FRAUD_SCENARIO`, `TX_TIME_SECONDS` e `TX_TIME_DAYS` permanecem na Silver, mas
não são copiados para a Gold. Essa projeção não apaga os campos da origem.
`TX_DATETIME` é preservado e permite obter o calendário e conferir o relógio.

| Decisão | Significado neste projeto |
| --- | --- |
| Fixar a fonte | Adquirir o histórico transacional publicado, sem recriar todas as tabelas internas do simulador |
| Preservar Bronze/Silver | Manter os dados de origem e seus campos, com integridade e contrato |
| Definir candidatos Gold | Escolher previamente famílias de sinais e janelas de 1/7 dias, conforme disponibilidade temporal |
| Separar metadados e alvo | Usar IDs para vínculo/agrupamento e rótulos para avaliação ou histórico já conhecido |
| Fazer ablação | Retreinar variantes com grupos de features retirados e medir o efeito sob protocolo controlado; ainda não executado |

O Handbook também apresenta janelas de 30 dias e indicadores binários de noite
e fim de semana. A nossa primeira Gold usa 1/7 dias, hora e dia da semana, além
de razões de gasto e suporte explícito. São escolhas de desenho próprias deste
projeto, não evidência de que os candidatos ausentes sejam inferiores.
Não houve um experimento de ablação na Bronze ou na definição inicial da Gold.

## Campos de origem: Bronze e Silver

Uma linha representa uma transação. Os tipos abaixo são da **Silver**; a Bronze
mantém os tipos originais do pickle, inclusive colunas `object` observadas pelo autor.
Timestamps da fonte não têm timezone. O valor monetário é uma unidade simulada:
não o interpretamos como reais, euros ou dólares.

| Campo | Tipo Silver | Significado e unidade | Uso na Gold/modelo |
| --- | --- | --- | --- |
| `TRANSACTION_ID` | `int64` | Identificador não negativo, único no histórico | Metadado de vínculo e auditoria; não entra em `X` |
| `TX_DATETIME` | `datetime64[ns]` | Momento do evento, sem timezone | Metadado; origem das janelas e do calendário |
| `CUSTOMER_ID` | `int64` | Identificador do cliente; pode aparecer em várias transações | Metadado e chave dos históricos/alertas por cliente; não entra diretamente em `X` |
| `TERMINAL_ID` | `int64` | Identificador do terminal; pode atender vários clientes | Metadado e chave dos históricos do terminal; não entra diretamente em `X` |
| `TX_AMOUNT` | `float64` | Valor da transação, finito e não negativo | Preditor direto; também alimenta médias e razões |
| `TX_TIME_SECONDS` | `int64` | Segundos decorridos desde `2018-04-01 00:00:00` | Conferência do relógio na Silver; ausente da Gold |
| `TX_TIME_DAYS` | `int64` | Dias decorridos desde a mesma origem, parte inteira de segundos/86.400 | Conferência do relógio na Silver; ausente da Gold |
| `TX_FRAUD` | `int8` | Alvo: 0 = genuína; 1 = fraude | `y` e metadado; o rótulo atual nunca entra em `X` |
| `TX_FRAUD_SCENARIO` | `int8` | Cenário atribuído pelo gerador: 0 = genuína; 1 = regra artificial de valor; 2 = terminal comprometido; 3 = cliente comprometido | Auditoria na Silver; ausente da Gold e proibido como preditor |

No contrato, `TX_FRAUD = 1` se e somente se `TX_FRAUD_SCENARIO > 0`.
O cenário revela o alvo; oferecê-lo ao modelo produziria vazamento. Ele descreve
a atribuição final do gerador, cujas regras podem se sobrepor; não é uma taxonomia
validada de fraude real nem prova do mecanismo causal de um evento bancário.

IDs armazenados como inteiros continuam sendo identificadores, sem significado
ordinal de risco. Usá-los como chaves para calcular históricos não equivale a
oferecer seu número bruto ao classificador.

## Relógio comum às features históricas

Seja `t = TX_DATETIME` da transação avaliada e `n ∈ {1, 7}` dias:

- `Hc(n,t)`: transações do mesmo cliente em `[t − n dias, t)`.
- `Ht(n,t)`: transações do mesmo terminal no mesmo intervalo.
- `Kt(n,t)`: transações do mesmo terminal em `[t − (7+n) dias, t − 7 dias)`,
  com rótulos já disponíveis segundo o atraso assumido de sete dias.

O limite esquerdo é inclusivo e o direito é exclusivo. A transação atual e seus
pares com timestamp idêntico ficam fora do histórico de comportamento. Para os
rótulos, exigimos `LABEL_AVAILABLE_AT < t`, também excluindo disponibilidade
exatamente no instante avaliado. Um dia significa 24 horas, não uma mudança de
data no calendário. Os limites do SQL preservam nanossegundos.

| Sufixo | Histórico do cliente/terminal | Histórico com rótulos conhecidos do terminal |
| --- | --- | --- |
| `1D` | `[t − 1 dia, t)` | `[t − 8 dias, t − 7 dias)` |
| `7D` | `[t − 7 dias, t)` | `[t − 14 dias, t − 7 dias)` |

Portanto, `KNOWN_FRAUD_RATE_7D` **não é a taxa de fraude das transações dos últimos
sete dias corridos até `t`**. É a taxa no intervalo anterior cujo feedback já pode
ser usado. Numerador e denominador têm a mesma janela de eventos elegíveis.
O histórico de transações recentes pode ser usado para volume, mas seus rótulos
ainda indisponíveis não podem alimentar risco conhecido.

## Os 19 preditores da Gold

A lista abaixo segue a ordem fixa de `FEATURE_COLUMNS`. Contagens são números
inteiros de eventos; médias têm a unidade de `TX_AMOUNT`; razões e taxas são
adimensionais. Todas as features publicadas devem ser finitas e sem nulos.

### Transação atual — três preditores

| Feature | Tipo | Definição | Leitura e limite |
| --- | --- | --- | --- |
| `TX_AMOUNT` | `float64` | Valor do evento atual | Valor zero é permitido; não é um limiar universal de fraude |
| `TX_HOUR` | `int8` | Hora extraída de `TX_DATETIME`, de 0 a 23 | Calendário da fonte, sem conversão de timezone |
| `TX_WEEKDAY` | `int8` | Dia da semana ISO, segunda = 1 até domingo = 7 | Não usa a convenção Python segunda = 0 |

Hora e dia são representações inteiras, sem codificação cíclica ou escala na Gold.
Essas escolhas não pressupõem relação linear com risco. Transformações aprendidas
pertencem ao pipeline de modelagem; outra representação exigiria comparação própria.

### Cliente — oito preditores

Para `Hc(n,t)`, `Nc` é sua contagem e `Mc` é a média dos valores, ou zero quando
não há eventos. A média considera todas as transações anteriores elegíveis,
incluindo fraudes e zeros; não depende do conhecimento dos rótulos.

| Feature | Tipo | Cálculo | Leitura |
| --- | --- | --- | --- |
| `CUSTOMER_TX_COUNT_1D` | `int64` | `Nc` em `Hc(1,t)` | Frequência recente do cliente |
| `CUSTOMER_AVG_AMOUNT_1D` | `float64` | `Mc` em `Hc(1,t)` | Gasto médio anterior, janela de 24 horas |
| `CUSTOMER_AMOUNT_RATIO_1D` | `float64` | `TX_AMOUNT / Mc` se `Mc > 0`; senão 0 | Valor atual relativo à média anterior de um dia |
| `CUSTOMER_AMOUNT_RATIO_VALID_1D` | `int8` | 1 se `Mc > 0`; senão 0 | Informa se a divisão anterior é válida |
| `CUSTOMER_TX_COUNT_7D` | `int64` | `Nc` em `Hc(7,t)` | Frequência observada em sete dias |
| `CUSTOMER_AVG_AMOUNT_7D` | `float64` | `Mc` em `Hc(7,t)` | Gasto médio anterior, janela de sete dias |
| `CUSTOMER_AMOUNT_RATIO_7D` | `float64` | `TX_AMOUNT / Mc` se `Mc > 0`; senão 0 | Valor atual relativo à média anterior de sete dias |
| `CUSTOMER_AMOUNT_RATIO_VALID_7D` | `int8` | 1 se `Mc > 0`; senão 0 | Informa se a divisão anterior é válida |

Quando válida, razão 1 significa valor igual à média; acima de 1, maior; abaixo
de 1, menor. Isso descreve um desvio relativo, não uma classificação de fraude.
Razão zero com flag 1 pode significar valor atual zero; razão zero com flag 0
é o fallback de uma divisão sem denominador positivo.

### Terminal — oito preditores

Para cada janela, `Nt` conta eventos em `Ht(n,t)`; `L` conta eventos com rótulo
conhecido em `Kt(n,t)`; `F` soma seus rótulos `TX_FRAUD`. `L` inclui genuínas e
fraudes. O atraso é aplicado aos dois tipos de rótulo.

| Feature | Tipo | Cálculo | Leitura |
| --- | --- | --- | --- |
| `TERMINAL_TX_COUNT_1D` | `int64` | `Nt` em `Ht(1,t)` | Volume recente do terminal, sem exigir rótulos |
| `TERMINAL_KNOWN_LABEL_COUNT_1D` | `int64` | `L` em `Kt(1,t)` | Suporte da taxa conhecida de um dia |
| `TERMINAL_KNOWN_FRAUD_COUNT_1D` | `int64` | `F` em `Kt(1,t)` | Quantidade de fraudes já conhecidas na janela |
| `TERMINAL_KNOWN_FRAUD_RATE_1D` | `float64` | `F / L` se `L > 0`; senão 0 | Proporção de fraudes entre os rótulos conhecidos |
| `TERMINAL_TX_COUNT_7D` | `int64` | `Nt` em `Ht(7,t)` | Volume recente em sete dias, sem exigir rótulos |
| `TERMINAL_KNOWN_LABEL_COUNT_7D` | `int64` | `L` em `Kt(7,t)` | Suporte da taxa conhecida de sete dias |
| `TERMINAL_KNOWN_FRAUD_COUNT_7D` | `int64` | `F` em `Kt(7,t)` | Quantidade de fraudes já conhecidas na janela |
| `TERMINAL_KNOWN_FRAUD_RATE_7D` | `float64` | `F / L` se `L > 0`; senão 0 | Proporção de fraudes entre os rótulos conhecidos |

Taxas ficam entre 0 e 1. Taxa zero com `L = 0` significa ausência de suporte;
com `L > 0`, significa nenhuma fraude conhecida naquele conjunto de eventos.
Os contadores distinguem essas situações. Uma taxa zero não certifica segurança.

## Seis metadados da Gold

| Campo | Tipo Gold | Papel |
| --- | --- | --- |
| `TRANSACTION_ID` | `int64` | Vínculo com a Silver, unicidade e reconciliação |
| `TX_DATETIME` | `datetime64[ns]` | Relógio do evento, data e agrupamentos de avaliação |
| `CUSTOMER_ID` | `int64` | Vínculo de cliente e ranking por cliente/dia |
| `TERMINAL_ID` | `int64` | Vínculo de terminal e auditoria de seus históricos |
| `LABEL_AVAILABLE_AT` | `datetime64[ns]` | `TX_DATETIME + 7 dias`; hipótese de disponibilidade do rótulo |
| `TX_FRAUD` | `int8` | Alvo preservado para avaliação e auditoria |

O loader retorna `(X, y, metadata)`: `X` contém somente os 19 preditores, `y`
contém `TX_FRAUD`, e `metadata` contém os seis campos acima, inclusive o alvo.
Não concatenar `metadata` a `X`. Colunas Hive `split` e `tx_date` vêm do caminho
das partições e não pertencem às 25 colunas físicas nem à lista de preditores.

O rótulo **da transação atual** permanece fora das entradas. Rótulos **de eventos
anteriores**, depois de disponíveis, podem alimentar contadores e taxas. Essa
distinção é o fundamento das features `KNOWN_*`; o atraso de sete dias é uma
hipótese do laboratório, não uma propriedade medida de uma operação bancária.

## Histórico vazio, incompleto e exemplos de leitura

O snapshot começa em 2018-04-01; não inventamos eventos anteriores. Janelas iniciais
podem estar incompletas. Não descartamos o aquecimento. O código pressupõe que os
eventos anteriores já estejam disponíveis e não modela atraso de ingestão.

| Situação | Valores publicados | Interpretação |
| --- | --- | --- |
| Cliente sem eventos na janela | Contagem 0, média 0, razão 0, flag 0 | Histórico vazio; não é perfil de gasto zero confirmado |
| Cliente com eventos anteriores todos de valor zero | Contagem positiva, média 0, razão 0, flag 0 | Há suporte, mas a razão não tem denominador positivo |
| Terminal sem rótulos conhecidos na janela | `L = 0`, `F = 0`, taxa 0 | Risco desconhecido nesse recorte |
| Terminal com 20 rótulos, sendo 2 fraudes | `L = 20`, `F = 2`, taxa 0,10 | Taxa empírica conhecida; não é probabilidade calibrada do próximo evento |
| Valor atual 19,15 e média anterior 27,128 | Razão ≈ 0,706; flag 1 | Valor atual abaixo da média; ainda pode ser fraude |

Suporte não é certeza: dois rótulos e duzentos rótulos podem produzir a mesma taxa,
com evidências diferentes. Históricos sobrepostos também criam dependência entre
as linhas de features.

## Dados que não foram adquiridos versus features ainda não criadas

O simulador usa perfis internos, incluindo coordenadas e parâmetros de gasto.
Eles não são colunas dos arquivos transacionais brutos usados pelo projeto.
Não foram apagados pelo extrator. Usá-los exigiria outra fonte e análise de sua
disponibilidade real; parâmetros ocultos do gerador não são automaticamente sinais
que uma operação observaria.

Dispositivo/IP, autenticação, canal, categoria de comerciante e grafo de contas
também não constam dessa tabela. Já janelas de 30 dias, recência e outras agregações
podem ser candidatos derivados de campos existentes: sua ausência é uma escolha
de escopo inicial, não perda de dados brutos ou ablação comprovada.

Os intervalos entre os splits atualizam o histórico quando elegíveis, mas não viram
exemplos de modelagem. A Gold publica apenas as janelas do protocolo; o restante
do histórico completo continua na Bronze/Silver. Isso define a população temporal
avaliada e deve ser separado de seleção de features ou descarte por desempenho.

## Unidade de observação e dependência

Uma linha é uma transação, não um cliente independente. Um cliente pode sofrer
várias tentativas; um terminal pode reunir eventos de diferentes clientes; e janelas
consecutivas reutilizam histórico. A dependência também existe em outros domínios
com medidas repetidas; independência é uma hipótese a verificar, não uma garantia
por se trabalhar com dados tabulares.

A métrica de alertas agrega clientes dentro de cada dia, mas a mesma pessoa pode
aparecer em dias distintos. Comparações estatísticas precisarão tratar tempo e
agrupamentos, sem considerar cada transação ou cliente/dia como réplica independente
por padrão. A escolha de cliente, terminal ou bloco temporal como unidade de
inferência dependerá da hipótese. Sete dias não demonstram estabilidade extensa.

## Fontes de verdade e manutenção

- [Contrato Gold executável](../references/gold_contract_v1.json): nomes, ordem, tipos e exclusões.
- [features.py](../fraud_detection_mlops/features.py): fórmulas e limites SQL.
- [Contrato da Gold](GOLD_CONTRACT.md): causalidade, publicação, loaders e limites.
- [Contrato da Silver](SILVER_CONTRACT.md): nove campos de origem e aceitação.
- [Protocolo temporal](EVALUATION_PROTOCOL.md): população e fronteiras da avaliação.
- [Handbook: simulador](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html): origem dos campos e cenários.
- [Handbook: transformação](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/BaselineFeatureTransformation.html): famílias conceituais; não substitui o código deste projeto.

Uma mudança de feature deve atualizar seu contrato, implementação, este dicionário
e a evidência do experimento correspondente. Esta entrega é documental: não altera
Gold, modelos ou entradas do experimento histórico.
