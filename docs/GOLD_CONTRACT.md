# Contrato da Gold

Versão: `gold_v1`. Protocolo: `temporal_v1`. Estado: construção e verificação
no dataset real informadas pelo autor em 2026-10-07; 104 testes controlados na
preparação. Checks locais do autor, commit e CI da Gold ainda não informados.
Contrato executável: [gold_contract_v1.json](../references/gold_contract_v1.json).
A Gold prepara tabelas de modelagem; esta entrega não ajusta nem avalia um modelo.

## Dados e preservação das janelas

A origem é a Silver `silver_v1`, verificada por inteiro antes do build. O protocolo
mantém treino de 1 a 28 de abril, validação de 6 a 12 de maio e teste de 20 a 26 de
maio de 2018. Há sete dias de feedback entre as janelas. O histórico de 27 de maio
em diante permanece fora desta Gold inicial. Fonte: commit
`6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a` do Handbook.

Para gerar features, o builder lê o contexto contínuo de **1 de abril a 26 de maio**,
incluindo os intervalos de feedback. Publica somente as 42 partições diárias das
três janelas. Transações dos intervalos atualizam histórico quando temporalmente
elegíveis, mas não viram exemplos de treino/validação/teste. As features são calculadas
antes de selecionar os splits, evitando apagar o histórico entre as janelas.

Todas as transações dessas janelas são preservadas, incluindo valores zero e
extremos. IDs, timestamps, cliente, terminal, valor e alvo são reconciliados com a
origem. A Silver não é alterada. O fato de materializar features e alvos do teste
não autoriza sua exploração ou seleção de modelo; sua avaliação fica para depois
do congelamento das escolhas, conforme o [protocolo](EVALUATION_PROTOCOL.md).

## Relógio e causalidade

`t` é o timestamp da transação avaliada. Cada dia equivale a 24 horas; a fonte é
naive, sem timezone. O código trabalha com nanossegundos inteiros para preservar
os limites de `datetime64[ns]`.

| Janela | Histórico de transações | Histórico com rótulos conhecidos |
| --- | --- | --- |
| 1 dia | `[t − 1 dia, t)` | `[t − 8 dias, t − 7 dias)` |
| 7 dias | `[t − 7 dias, t)` | `[t − 14 dias, t − 7 dias)` |

O limite esquerdo é inclusivo e o direito exclusivo. **A transação atual e todas
as transações com o mesmo timestamp ficam fora do histórico de comportamento.**
O ID ordena a escrita, mas não cria uma ordem de chegada entre eventos simultâneos.

`LABEL_AVAILABLE_AT = TX_DATETIME + 7 dias`. Um rótulo entra no histórico somente
quando `LABEL_AVAILABLE_AT < t`. Na janela conhecida de sete dias, numerador e
denominador usam as mesmas transações do intervalo `[t − 14 dias, t − 7 dias)`.
Não dividimos fraudes antigas por transações recentes ainda sem rótulo conhecido.

Exemplo: um rótulo de transação ocorrida em 1 de abril às 10h fica disponível em
8 de abril às 10h. Pela política conservadora deste contrato, ele começa a alimentar
features em timestamps estritamente posteriores a esse instante. O mesmo limite
é aplicado aos rótulos de fraude e de transações genuínas.

O snapshot começa em 1 de abril. O histórico anterior a essa data não existe nesta
base: as primeiras janelas são parciais e os contadores refletem apenas o histórico
observado. Não descartamos uma fase inicial de aquecimento. O histórico conhecido
de sete dias só pode cobrir seus 14 dias anteriores após o começo dessa cobertura.
Essa escolha é uma limitação documentada do primeiro protocolo.

## Preditores e metadados

São **19 preditores numéricos**, em ordem fixa:

| Grupo | Colunas, para `n ∈ {1, 7}` quando indicado | Quantidade |
| --- | --- | --- |
| Transação atual | `TX_AMOUNT`, `TX_HOUR`, `TX_WEEKDAY` | 3 |
| Cliente | `CUSTOMER_TX_COUNT_nD`, `CUSTOMER_AVG_AMOUNT_nD`, `CUSTOMER_AMOUNT_RATIO_nD`, `CUSTOMER_AMOUNT_RATIO_VALID_nD` | 8 |
| Terminal | `TERMINAL_TX_COUNT_nD`, `TERMINAL_KNOWN_LABEL_COUNT_nD`, `TERMINAL_KNOWN_FRAUD_COUNT_nD`, `TERMINAL_KNOWN_FRAUD_RATE_nD` | 8 |

`TX_HOUR` varia de 0 a 23; `TX_WEEKDAY` usa ISO (segunda = 1, domingo = 7).
São candidatos de calendário disponíveis no evento; sua inclusão não representa
um achado da EDA horária que ainda não foi compartilhada. Contadores são `int64`,
flags e calendário são `int8`; valores, médias, razões e taxas são `float64`.

Os **seis metadados** são `TRANSACTION_ID`, `TX_DATETIME`, `CUSTOMER_ID`, `TERMINAL_ID`,
`LABEL_AVAILABLE_AT` e `TX_FRAUD`. Timestamps são `datetime64[ns]`, IDs são `int64` e
alvo é `int8`. O Parquet tem 25 colunas físicas. `TX_FRAUD_SCENARIO` não é copiado
para a Gold. IDs brutos, timestamps, disponibilidade e alvo não são preditores.

O [loader](../fraud_detection_mlops/gold.py) retorna `(X, y, metadata)` usando a lista
explícita `FEATURE_COLUMNS`. `X` contém apenas os 19 preditores; `y` é `TX_FRAUD`.
Os metadados também preservam o alvo para auditoria e nunca devem ser passados ao
modelo como features. Chamar o loader para `test` fica reservado à avaliação final.

## Histórico vazio e médias zero

A política usa regras constantes, sem estimar parâmetros a partir dos holdouts:

- Contagem sem eventos: zero. Média sem eventos: zero; a contagem revela suporte vazio.
- Razão de valor: `TX_AMOUNT / média anterior` quando a média é positiva; caso
  contrário, razão zero e `CUSTOMER_AMOUNT_RATIO_VALID_nD = 0`.
- Risco conhecido: fraudes conhecidas / rótulos conhecidos; quando o denominador
  é zero, taxa zero e contagem de rótulos conhecidos zero.

Uma média de zero também pode refletir transações anteriores de valor zero;
a contagem diferencia essa situação de histórico vazio. A flag da razão indica
se a divisão é válida. Nenhum zero é removido e não produzimos NaN ou infinito.
Fallback zero não afirma que o terminal seja seguro; precisa ser interpretado
com seu suporte. Escala, encoders, seleção, resampling e outros parâmetros aprendidos
continuam para o pipeline de modelagem, ajustados exclusivamente no treino.

## Implementação e armazenamento

[features.py](../fraud_detection_mlops/features.py) implementa janelas SQL `RANGE`
com limites em nanossegundos. Materializa as features numa tabela temporária
DuckDB sobre o contexto; não concatena toda a Silver num DataFrame pandas. O pandas
é usado em uma partição de saída por vez, para normalizar tipos, escrever Parquet
e conferir a leitura de volta. O loader de modelagem carrega um split inteiro em
memória quando chamado; não é um loader de treinamento em streaming.

O DuckDB usa quatro threads no build e tem diretório de spill dentro do staging.
As janelas precisam de ordenação e memória; esta versão não estabelece um orçamento
fixo nem promete processamento independente do volume. A execução no tamanho real
será medida no ambiente do autor. Não há novas dependências nesta entrega.

Destino:

```text
data/processed/handbook/<source_commit>/gold_v1/
```

Cada dia fica em
`transactions/split=<train|validation|test>/tx_date=YYYY-MM-DD/part-00000.parquet`,
com Zstd, schema fixo e sem índice pandas. O manifesto registra os 42 Parquets,
tamanhos e SHA-256, protocolo, contagens por split, inputs do contexto, hashes de
contratos, fonte, módulos e inventário. A saída não inclui estatísticas exploratórias
de fraude de validação/teste. Colunas Hive `split` e `tx_date` são derivadas dos paths,
e não pertencem ao schema físico nem a `X`.

A publicação ocorre após verificar todo o staging. O lock `.gold_v1.lock` evita
builders concorrentes. Repetir o build confere a Silver e a Gold existentes,
verifica compatibilidade dos inputs e do código de features e retorna `reused`
sem regravar os Parquets. Uma saída incompatível ou corrompida causa falha.
Auditorias ficam em `data/processed/handbook/<source_commit>/runs/gold_<run_id>.json`.

## Verificação e limites de evidência

`gold verify` confere integridade, schema, cobertura exata, datas, contagens, IDs
únicos, finitude e relações entre features. Isso inclui disponibilidade dos rótulos,
razões, flags e taxas compatíveis com contadores. Ele não reconstrói todas as features
nem reverifica a Silver original; o build faz a verificação da origem. Os testes de
causalidade e o oracle independente cobrem a lógica das janelas.

Os testes controlados conferem limites em nanossegundos, exclusão de peers,
invariância ao futuro, independência de IDs na ordem de chegada, comparação com
cálculo independente, histórico dos gaps, alvos preservados, allowlist de features,
reutilização, corrupção, falhas de escrita, lock e alterações de inputs.
O [recibo de preparação](../references/evidence/gold_preparation_2026-10-07.json)
registra seu alcance. Não é evidência de uma Gold real construída pelo autor.

Este processamento é causal por **tempo de evento**, assumindo disponibilidade das
transações anteriores. A fonte não informa chegada ao sistema, revisões ou atrasos
de ingestão. Replay com eventos fora de ordem e paridade entre features offline e
online terão contratos próprios. Ainda não há feature store, modelo, serving ou
métrica de detecção validados nesta etapa.

Referências primárias:

- [Handbook: transformação de features](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/BaselineFeatureTransformation.html).
- [DuckDB: janelas SQL](https://duckdb.org/docs/current/sql/functions/window_functions).
- [DuckDB: timestamps](https://duckdb.org/docs/current/sql/functions/timestamp).

As famílias de features seguem hipóteses do projeto e princípios do Handbook;
limites estritos, janelas de 1/7 dias e protocolo são próprios deste portfólio.
Não replicamos o benchmark publicado no livro.

## Construção real informada pelo autor em 2026-10-07

O autor forneceu as saídas de build e verify: 42 partições, 19 preditores e
402.877 linhas, com contagens idênticas nos dois comandos.

| Split | Linhas |
| --- | ---: |
| Treino | 268.668 |
| Validação | 67.255 |
| Teste | 66.954 |
| Total | 402.877 |

O treino coincide com as 268.668 linhas da EDA anterior. Run de auditoria:
`8b1564df49fc497080963986b05aa007`.
O [recibo](../references/evidence/gold_build_2026-10-07.json) registra os valores
transcritos e o caminho relativo da auditoria; os artefatos nativos permanecem
locais e não foram inspecionados pelo assistente. O commit executado, os checks
locais do autor e a CI da Gold aguardam associação. Não há métrica de modelo.

## Escolha dos candidatos e futura seleção por desempenho

Os 19 candidatos representam uma escolha prévia de engenharia baseada em hipóteses
e no contrato de disponibilidade. A allowlist já exclui IDs, timestamps brutos,
alvo e cenário dos preditores. A Gold também seleciona as janelas do protocolo;
não preserva os seis meses inteiros como exemplos de modelagem.

Dentro das janelas, todas as transações aceitas pela Silver são preservadas.
Não removemos extremos ou zeros por correlação, separação entre classes ou
expectativa de melhorar uma métrica. Validar o contrato de qualidade e limitar o
histórico temporal são controles já aplicados; nenhuma transformação foi ajustada
com base no desempenho de um modelo.

O primeiro baseline estabelecerá uma referência com esses candidatos. Seleção
aprendida, imputação, escala ou resampling, quando necessários, serão ajustados no
treino dentro do pipeline. A validação comparará escolhas e ablações; o teste será
usado após congelar as escolhas. Não é obrigatório adiar um controle de qualidade
necessário até o baseline: o que exige evidência própria é uma intervenção para
melhorar desempenho ou mudar a população avaliada.
