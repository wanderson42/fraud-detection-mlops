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

| Etapa | Estado |
| --- | --- |
| Ambiente Poetry e CI | Implementados e validados |
| Aquisição Bronze | Implementada; cobertura e integridade de arquivos validadas localmente |
| Qualidade semântica e Silver em Parquet/DuckDB | Planejadas |
| Features, treinamento e avaliação temporal | Planejados |
| Streaming, feature store, serving e monitoramento | Evolução pretendida; desenho e validação pendentes |

A base é simulada. Ainda não há modelo avaliado ou resultado de detecção em operação.

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

## Dados e fluxo ELT

Fonte: [Fraud-Detection-Handbook/simulated-data-raw](https://github.com/Fraud-Detection-Handbook/simulated-data-raw),
fixada no commit `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a`.
O [inventário versionado](references/handbook_source.json) descreve 183 arquivos diários
`.pkl`, de 2018-04-01 a 2018-09-30, totalizando 107.121.710 bytes.

Extraímos e carregamos os bytes originais na Bronze local em
`data/raw/handbook/<source_commit>/`. A transformação para Silver e Gold será feita
sobre essa base. A aquisição confere tamanho e Git blob SHA-1, registra SHA-256,
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
| `fraud_detection_mlops/dataset.py` | CLI de extração e verificação |
| `references/` | Inventário e recibos documentais |
| `tests/` | Integridade, recuperação e integração Parquet/SQL |
| `.github/workflows/ci.yml` | Qualidade automatizada |

Os módulos de features, modelagem e gráficos permanecem scaffolds do template.
