# Contrato da Silver

Contrato: `silver_v1`. Diagnóstico completo informado pelo autor em 2026-10-06.
Conversão implementada, testada e executada sobre os 183 arquivos reais no ambiente
do autor, com `build` e `verify` aprovados em 2026-10-06. O contrato executável está em
[silver_contract_v1.json](../references/silver_contract_v1.json).
O [recibo da implementação preparada](../references/evidence/silver_implementation_validation_2026-10-07.json)
registra 70 testes aprovados e um ensaio com a primeira partição real, separado da
execução completa e da CI.
O [recibo da execução completa](../references/evidence/silver_build_2026-10-06.json)
registra a validação posterior informada pelo autor: 1.754.155 linhas e IDs
distintos, 14.681 fraudes, 1.739.474 genuínas e 42 zeros preservados.

## Evidência e decisões

A auditoria informou 1.754.155 linhas e o mesmo número de IDs distintos, 14.681
fraudes e 1.739.474 transações genuínas. Não foram encontrados nulos, duplicatas,
valores negativos, inconsistências de datas/contadores ou rótulos inválidos nas
verificações implementadas. Foram observados 42 valores monetários zero.
O [recibo](../references/evidence/silver_profile_2026-10-06.json) transcreve o resumo
do terminal; não substitui o `profile.json` nativo nem uma validação da Silver.

Decisão: aceitar `TX_AMOUNT` finito e maior ou igual a zero, preservando zeros e
extremos. O [gerador do Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html)
substitui sorteios negativos por uma distribuição uniforme a partir de zero e
arredonda para duas casas decimais. Portanto, zero é compatível com um valor pequeno
arredondado. Essa inferência não comprova a origem individual de cada um dos 42 casos.
Não fazemos exclusão, imputação ou tratamento de outliers nesta conversão.

## Schema físico

| Coluna | Tipo pandas / Arrow | Regra |
| --- | --- | --- |
| `TRANSACTION_ID` | `int64` | Não negativo; único no histórico completo |
| `TX_DATETIME` | `datetime64[ns]` / `timestamp[ns]` | Sem timezone; data corresponde à partição |
| `CUSTOMER_ID` | `int64` | Não negativo; identificador |
| `TERMINAL_ID` | `int64` | Não negativo; identificador |
| `TX_AMOUNT` | `float64` / `double` | Finito e não negativo; zeros preservados |
| `TX_TIME_SECONDS` | `int64` | Consistente com segundos desde 2018-04-01 |
| `TX_TIME_DAYS` | `int64` | Consistente com dias desde a mesma origem |
| `TX_FRAUD` | `int8` | Um dos rótulos 0 e 1; alvo supervisionado |
| `TX_FRAUD_SCENARIO` | `int8` | Um dos cenários 0, 1, 2 e 3; metadado de auditoria |

As nove colunas são obrigatórias, sem nomes repetidos nem nulos. Fraude deve ser 1
se e somente se o cenário for maior que zero. Inteiros devem ser representáveis
sem truncamento ou overflow. A Bronze permanece com os bytes e tipos originais.
O índice pandas é descartado na gravação; `TRANSACTION_ID` continua preservado.

Timestamps de eventos mantêm a convenção sem timezone da fonte. Horários UTC de
auditoria são metadados da execução e não mudam essa convenção. IDs não passam a
ser variáveis contínuas do modelo por serem armazenados como inteiros.
`TX_FRAUD` é o alvo e `TX_FRAUD_SCENARIO` acompanha a geração do rótulo: ambos são
excluídos das entradas preditivas. A seleção das outras features será definida na Gold.

## Aceitação e publicação

O builder reutiliza o profiler para verificar a Bronze completa e recalcular o
diagnóstico a cada execução. Não utiliza um relatório antigo como autorização de
conversão. Todas as violações semânticas verificadas bloqueiam a publicação, exceto
`zero_amounts` e `timestamp_order_decreases`, que são contadores informativos.
A ordem das linhas é preservada; consultas SQL exigem `ORDER BY` quando necessário.
Partições vazias também bloqueiam esta versão do contrato.

Os arquivos são escritos primeiro em um diretório temporário no mesmo filesystem.
Cada Parquet é relido e comparado com o DataFrame normalizado, incluindo valores,
ordem e tipos. Contagens globais por SQL devem corresponder ao perfil de entrada.
Somente após todas as verificações o diretório completo é movido para o destino.
Falhas normais removem o staging e deixam uma auditoria de falha; não publicam uma
Silver parcial. Um encerramento abrupto pode deixar staging, lock e auditoria `running`.

## Armazenamento e consulta

Destino: `data/interim/handbook/<source_commit>/silver_v1/`.
Cada dia fica em `transactions/tx_date=YYYY-MM-DD/part-00000.parquet`, com compressão
Zstandard e Parquet 2.6 para preservar timestamps em nanossegundos. Há nove colunas
físicas. O DuckDB deriva `tx_date` do diretório Hive como uma décima coluna na view
`transactions`, sem duplicar os dados em um banco persistente.

`manifest.json` registra origem, hashes do contrato/inventário e código, partições,
checksums SHA-256 de entrada e saída, tamanhos, contagens e execução responsável.
Auditorias ficam em `data/interim/handbook/<source_commit>/runs/silver_<run_id>.json`:
incluem ambiente, horários UTC, resumo de aceitação, resultado e erro capturado.
Resultados de build: `success`, `reused`, `failed` ou `interrupted`.

Uma repetição verifica a Bronze e a Silver e reutiliza os Parquets íntegros sem
reescrevê-los. Corrupção causa erro; não é sobrescrita automaticamente. Alterar o
contrato exige uma versão e implementação novas. Os hashes distinguem conteúdo,
mas não substituem uma assinatura nem garantem imutabilidade.

`verify` confere cobertura, checksums, schema físico, registros do inventário e
reconciliação SQL. Ele não relê a Bronze nem repete todo o diagnóstico semântico;
esse diagnóstico é realizado pelo `build`. Procedimentos e opções estão em
[Operations](OPERATIONS.md#construir-e-verificar-a-silver).

## EDA e próxima etapa

A frequência global de fraude informada é aproximadamente 0,8369%. Isso descreve
a base; não mede qualidade de um modelo. EDA de distribuições e evolução temporal
pode usar a Silver. Estatísticas que orientem seleção de features, tratamento de
valores ou ajustes do modelo devem respeitar o período de treinamento, preservando
a avaliação final. Features, splits e testes contra vazamento ficam na Gold.
