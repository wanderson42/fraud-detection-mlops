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
saídas de `build` e `verify` e a execução responsável. A CI da Silver será
registrada após o commit e o push; a CI citada acima pertence à Bronze.

| Etapa | Estado |
| --- | --- |
| Ambiente Poetry e CI | Implementados e validados |
| Aquisição Bronze | Implementada; cobertura e integridade de arquivos validadas localmente |
| Diagnóstico semântico da Bronze | Auditoria dos 183 arquivos informada pelo autor; 1.754.155 transações |
| Contrato e Silver em Parquet/DuckDB | `silver_v1` construída e verificada localmente; 183 partições reconciliadas |
| Features, treinamento e avaliação temporal | Planejados |
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
poetry run ruff check .
poetry run ruff format --check .
poetry run pytest -q
```

As dependências são fixadas em `poetry.lock`. Os testes da CI usam respostas de rede
simuladas e não baixam o dataset real.

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
explícito e mantém todas as linhas aceitas. A Gold será construída depois.
A aquisição confere tamanho e Git blob SHA-1, registra SHA-256,
atualiza um manifesto e audita cada execução iniciada. O contrato e as decisões
estão em [Data pipeline](docs/DATA_PIPELINE.md).

`data/` é ignorado pelo Git. Preserve arquivos, manifesto e auditorias juntos.
Ainda não há armazenamento remoto de artefatos. Os termos conhecidos da fonte estão
registrados no inventário e no documento do pipeline.

## Documentação

| Leitura | Propósito |
| --- | --- |
| [Política de documentação](docs/DOCUMENTATION_POLICY.md) | Regras editoriais, evidências, versionamento e fechamento de entregas |
| [Notebook principal](notebooks/fraud_detection_mlops.ipynb) | Narrativa técnica curada e síntese dos marcos |
| [Notebook da Bronze](notebooks/stages/01_bronze_ingestion.ipynb) | Decisões, leitura do algoritmo e evidências da etapa |
| [Contrato da Silver](docs/SILVER_CONTRACT.md) | Schema, política para zeros, aceitação e proveniência |
| [Notebook da Silver](notebooks/stages/02_silver_data_contract.ipynb) | Diagnóstico completo, decisões e consulta da Silver |
| [Data pipeline](docs/DATA_PIPELINE.md) | Fonte, ELT, contratos e limites |
| [Operations](docs/OPERATIONS.md) | Execução, diagnóstico e recuperação |
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
| `fraud_detection_mlops/dataset.py` | CLI de extração e verificação |
| `references/` | Inventário e recibos documentais |
| `tests/` | Integridade, recuperação e integração Parquet/SQL |
| `.github/workflows/ci.yml` | Qualidade automatizada |

Os módulos de features, modelagem e gráficos permanecem scaffolds do template.
