# Fraud Detection MLOps

Projeto de portfólio para construir um pipeline reproduzível de detecção de fraude
com dados temporais do Fraud Detection Handbook. Estrutura inicial criada com
[Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/).

## Estado do projeto

Marco de 2026-10-06: **Bronze completa, com 183/183 arquivos verificados no ambiente
do autor**, 39 testes locais aprovados e
[CI aprovada para a implementação `18242fc`](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37514553686).
As origens e os limites das evidências estão no
[recibo da etapa](references/evidence/bronze_2026-10-06.json).

No mesmo dia, o autor construiu e verificou a **Silver `silver_v1` completa**:
183 partições, 1.754.155 linhas e IDs distintos, com os 42 valores zero preservados.
Os checks locais passaram, incluindo 70 testes. O
[recibo da Silver](references/evidence/silver_build_2026-10-06.json) registra as
saídas de `build` e `verify` e a execução responsável. A
[CI da Silver `acf1516`](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37563463479)
foi aprovada. O autor informou aprovação local do tox: 70 testes em 1,57 s,
com lint e formatação aprovados (execução completa: 4,61 s). A integração tox e a EDA foram publicadas na revisão
[`fc22a48`](https://github.com/wanderson42/fraud-detection-mlops/commit/fc22a48af5e6d9a0f9648a62effa95d39e02de02),
com [CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37634000505). O autor informou a EDA do treino: 268.668 transações,
1.505 fraudes e nove outputs verificados. O
[recibo da EDA](references/evidence/eda_training_2026-10-07.json) registra a execução.

| Etapa | Estado |
| --- | --- |
| Ambiente Poetry e CI | Implementados e validados |
| Aquisição Bronze | Implementada; cobertura e integridade de arquivos validadas localmente |
| Diagnóstico semântico da Bronze | Auditoria dos 183 arquivos informada pelo autor; 1.754.155 transações |
| Contrato e Silver em Parquet/DuckDB | `silver_v1` construída e verificada localmente; 183 partições reconciliadas |
| EDA da Silver e protocolo temporal | Executada localmente; nove outputs verificados, resumo e quatro tabelas informados pelo autor |
| Gold com features e splits temporais | Construída e verificada no ambiente do autor: 42 partições, 19 preditores e 402.877 linhas |
| Treinamento e avaliação temporal | Planejados; janelas e métricas iniciais documentadas |
| Streaming, feature store, serving e monitoramento | Evolução pretendida; desenho e validação pendentes |

A base é simulada. Ainda não há modelo avaliado ou resultado de detecção em operação.

O diagnóstico completo informou IDs globalmente únicos, 14.681 fraudes, 42 valores
monetários zero e nenhuma violação nos demais controles implementados. Os zeros
foram preservados conforme o [contrato da Silver](docs/SILVER_CONTRACT.md).
O [recibo do diagnóstico](references/evidence/silver_profile_2026-10-06.json)
registra a saída local e os 50 testes aprovados informados nessa etapa.

## Quick Start

Python 3.14.4 e Poetry 2.4.3. Na raiz do checkout:

```bash
poetry install
poetry check --lock
poetry run tox -e py314
```

As dependências são fixadas em `poetry.lock`. Os testes da CI usam respostas de rede
simuladas e não baixam o dataset real.

O tox cria `.tox/py314` e usa o Poetry para instalar as versões do lockfile nesse
ambiente antes de executar Ruff e pytest. `make validate` reproduz o mesmo fluxo.
Para testes rápidos, `poetry run pytest -q` continua disponível.
Consulte [Testing](docs/TESTING.md) para entender o isolamento e os limites desta validação.

Para adquirir todo o histórico e verificar sua integridade:

```bash
poetry run python -m fraud_detection_mlops.dataset extract
poetry run python -m fraud_detection_mlops.dataset verify --require-complete
```

O resultado final esperado é `Verified: 183/183; complete: True`. Se a Bronze já
existir, os arquivos íntegros serão conferidos e reutilizados. Amostragem de sete dias,
opções e recuperação estão no [runbook](docs/OPERATIONS.md).

Com a Bronze completa, construir e verificar os Parquets:

```bash
poetry run python -m fraud_detection_mlops.silver build
poetry run python -m fraud_detection_mlops.silver verify
```

A saída fica em `data/interim/handbook/<source_commit>/silver_v1/`.
Reconciliação observada pelo autor: 183 partições e 1.754.155 linhas, preservando os 42 zeros.
Consulta DuckDB, auditorias e recuperação estão em
[Operations](docs/OPERATIONS.md#construir-e-verificar-a-silver).

## Dados e fluxo ELT

Fonte: [Fraud-Detection-Handbook/simulated-data-raw](https://github.com/Fraud-Detection-Handbook/simulated-data-raw),
fixada no commit `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a`.
O [inventário versionado](references/handbook_source.json) descreve 183 arquivos diários
`.pkl`, de 2018-04-01 a 2018-09-30, totalizando 107.121.710 bytes.

Extraímos e carregamos os bytes originais na Bronze local em
`data/raw/handbook/<source_commit>/`. A Silver transforma essa base com contrato
explícito e mantém todas as linhas aceitas. A Gold usa essa base verificada.
A aquisição confere tamanho e Git blob SHA-1, registra SHA-256,
atualiza um manifesto e audita cada execução iniciada. O contrato e as decisões
estão em [Data pipeline](docs/DATA_PIPELINE.md).

`data/` é ignorado pelo Git. Preserve arquivos, manifesto e auditorias juntos.
Ainda não há armazenamento remoto de artefatos. Os termos conhecidos da fonte estão
registrados no inventário e no documento do pipeline.

## EDA e próxima avaliação

Com a Silver completa, gerar as tabelas e o painel descritivo:

```bash
poetry run python -m fraud_detection_mlops.eda build
```

A primeira EDA explora somente o treino (1 a 28 de abril de 2018). O
[protocolo temporal](docs/EVALUATION_PROTOCOL.md) reserva validação e teste, com gaps
de sete dias para o feedback dos rótulos. O comando imprime caminhos de relatório
e auditoria; preserve os outputs locais. A [referência da EDA](docs/EDA.md) explica
as tabelas, os gráficos, a verificação e os limites. A execução
`733686de23204cd6b9a5a1ec1cfbc2e7` foi informada pelo autor, com os
[achados de treino](docs/EDA.md#achados-informados-pelo-autor-em-2026-10-07).

> O desbalanceamento já mostra por que **acurácia não será nossa métrica principal**: prever todas as transações como genuínas produziria aproximadamente **99,44% de acurácia**, com **recall de fraude igual a zero**. Isso sustenta a escolha de Average Precision para avaliar o ranking.

A justificativa e o cálculo estão no [protocolo de avaliação](docs/EVALUATION_PROTOCOL.md#por-que-acurácia-não-é-a-métrica-principal).

## Gold: features para modelagem

Com a Silver completa e os checks aprovados:

```bash
poetry run python -m fraud_detection_mlops.gold build
poetry run python -m fraud_detection_mlops.gold verify
```

A Gold gera 19 preditores e preserva todas as linhas nas janelas de treino,
validação e teste. Históricos de cliente/terminal usam somente eventos anteriores;
o risco do terminal considera rótulos disponíveis após sete dias. O
[contrato Gold](docs/GOLD_CONTRACT.md) define janelas, empates de timestamp,
primeiros eventos, schema, loader e limites. O autor informou construção e
verificação reais: 268.668 linhas de treino, 67.255 de validação e 66.954 de teste,
com [recibo da execução](references/evidence/gold_build_2026-10-07.json).
A Gold foi publicada na revisão [`14ab57e`](https://github.com/wanderson42/fraud-detection-mlops/commit/14ab57e15f0931ffb6966b26d390a42b514762b7), com
[CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37652509812):
104 testes em 11,37 s, lint, formatação e lockfile aprovados. O notebook publicado
preserva a verificação e a leitura do treino (`X`: 268.668 × 19). A CI usa fixtures
controladas; a construção real foi executada pelo autor. Ainda não há modelo ajustado
nem avaliação do teste final.

## Documentação

| Leitura | Propósito |
| --- | --- |
| [Política de documentação](docs/DOCUMENTATION_POLICY.md) | Regras editoriais, evidências, versionamento e fechamento de entregas |
| [Notebook principal](notebooks/fraud_detection_mlops.ipynb) | Narrativa técnica curada e síntese dos marcos |
| [Notebook da Bronze](notebooks/stages/01_bronze_ingestion.ipynb) | Decisões, leitura do algoritmo e evidências da etapa |
| [Contrato da Silver](docs/SILVER_CONTRACT.md) | Schema, política para zeros, aceitação e proveniência |
| [Notebook da Silver](notebooks/stages/02_silver_data_contract.ipynb) | Diagnóstico completo, decisões e consulta da Silver |
| [EDA](docs/EDA.md) | Análise descritiva da Silver, outputs e interpretação |
| [Protocolo temporal](docs/EVALUATION_PROTOCOL.md) | Janelas, atraso de rótulos, população e métricas planejadas |
| [Notebook da EDA](notebooks/stages/03_silver_eda.ipynb) | Leitura dos resultados locais e registro de hipóteses |
| [Contrato da Gold](docs/GOLD_CONTRACT.md) | Features causais, splits, schema e comportamento sem histórico |
| [Notebook da Gold](notebooks/stages/04_gold_temporal_features.ipynb) | Decisões, causalidade e leitura da preparação para modelagem |
| [Data pipeline](docs/DATA_PIPELINE.md) | Fonte, ELT, contratos e limites |
| [Operations](docs/OPERATIONS.md) | Execução, diagnóstico e recuperação |
| [Testing](docs/TESTING.md) | Integração tox–Poetry, ambiente isolado e checks compartilhados com a CI |
| [Stakeholders](docs/STAKEHOLDERS.md) | Objetivo, entregas e próximos marcos em linguagem de negócio |

Os notebooks podem ser lidos no GitHub. As células de código da Bronze são opcionais,
não têm outputs pré-fabricados e não fazem downloads. Infraestrutura e Model Card
terão documentos próprios quando forem implementados.

## Organização do código

| Caminho | Responsabilidade |
| --- | --- |
| `fraud_detection_mlops/bronze.py` | Aquisição, checksums, retomada e auditoria |
| `fraud_detection_mlops/profiling.py` | Diagnóstico offline da Bronze para definir a Silver |
| `fraud_detection_mlops/silver.py` | Conversão Parquet, manifesto, auditoria, verificação e consulta DuckDB |
| `fraud_detection_mlops/eda.py` | Agregações de treino, gráficos, manifesto e auditoria da EDA |
| `fraud_detection_mlops/temporal.py` | Validação do protocolo temporal versionado |
| `fraud_detection_mlops/features.py` | Cálculo SQL das features com janelas causais e atraso de rótulos |
| `fraud_detection_mlops/gold.py` | Publicação Parquet, auditoria, verificação e loader dos splits Gold |
| `fraud_detection_mlops/dataset.py` | CLI de extração e verificação |
| `references/` | Inventário e recibos documentais |
| `tests/` | Integridade, recuperação e integração Parquet/SQL |
| `tox.toml` | Sequência de qualidade no ambiente Python 3.14 isolado |
| `.github/workflows/ci.yml` | Qualidade automatizada |

Os módulos de modelagem e o scaffold de gráficos do template permanecem para etapas futuras.
