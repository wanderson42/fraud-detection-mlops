# Fraud Detection MLOps

Laboratório de MLOps para priorização de investigação de fraude, desenvolvido por
Wanderson Ferreira. Usa dados simulados do Fraud Detection Handbook, histórico
causal, feedback atrasado e evidências auditáveis.

Com capacidade limitada de investigação, quais clientes priorizar usando apenas
a informação disponível no momento da decisão?

## Estado do projeto

O pipeline offline Bronze → Silver → Gold, a baseline, o tracking MLflow/skops,
os diagnósticos e as ablações estão implementados. A referência conserva as 19
features; a avaliação final informada pelo autor obteve AP **0,640703** e precisão
diária @100 de **55%**, com gate de laboratório aprovado. O teste foi consumido.

A busca temporal com Optuna concluiu **20 trials e 63 fits, sem falhas** no
relatório fornecido pelo autor. Nenhum trial passou o gate: **`retain_reference`**.
A confirmação não foi avaliada. A evidência está no
[protocolo de avaliação](docs/modeling/EVALUATION_PROTOCOL.md#resultado-da-busca-informado-pelo-autor).
Esses resultados simulados não demonstram desempenho em produção.

O serving de laboratório recebe as 19 features e devolve score identificado.
API, exportação sem refit e wheel fora do checkout têm checks sintéticos;
a suíte local do autor aprovou 302 testes. Esse marco foi integrado em `main` pelo
[PR #1](https://github.com/wanderson42/fraud-detection-mlops/pull/1).
O [protocolo estatístico da referência](docs/modeling/EVALUATION_PROTOCOL.md#protocolo-estatístico-da-referência--v1)
fixa 2–15/09 para avaliação e 16–30/09 para replay. O
[executor auditável](docs/modeling/EVALUATION_PROTOCOL.md#executor-auditável-da-janela-fixada)
foi executado e verificado pelo autor em 02–15/09/2018: **134.467 transações**,
AP **0,621312**, precisão diária @100 de **54,93%** e recall médio diário de clientes
fraudulentos de **72,89%**, sem refit ou novas runs. O autor aprovou **362 testes**,
com 94 avisos. Essa janela foi consumida; **16–30/09 continua reservado para replay**.
O [relato da execução](docs/modeling/EVALUATION_PROTOCOL.md#resultado-da-referência-em-setembro--relato-do-autor)
mantém os limites estatísticos. O diagnóstico nativo de dependência foi revisado;
a análise exploratória de reamostragem por blocos está implementada e aguarda
execução sobre as previsões nativas para conferir a AP agrupada. As faixas não têm
cobertura de generalização demonstrada. Exportação, Docker e Prefect vêm depois
desse fechamento, conforme
o [mural](docs/project/ROADMAP.md#próxima-entrega-concreta).
[Contrato e execução](docs/operations/SERVING_CONTRACT.md). Replay por eventos,
orquestração e monitoramento continuam no [mural de metas](docs/project/ROADMAP.md).
A organização inicial usou
[Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/).

## Começar

Python 3.14.4 e Poetry 2.4.3, na raiz do checkout:

```bash
poetry install
make validate
```

`make validate` executa tox, Ruff e pytest com as versões de `poetry.lock`.
Os testes usam dados sintéticos e rede simulada; não baixam o dataset real.
Comandos de aquisição, preparação e experimentação ficam no
[runbook](docs/operations/OPERATIONS.md). Preserve dados, manifestos, estudos e
tracking juntos; `data/` é ignorado pelo Git.

## Organização

| Área | Responsabilidade |
| --- | --- |
| `data/contracts/` | Schemas, aceitação dos datasets e protocolo temporal |
| `data/ingestion/` | Inventário fixado, aquisição Bronze e CLI de download |
| `data/datasets/` | Construção, verificação e consulta de Silver e Gold |
| `data/quality/` | Perfil diagnóstico da Bronze e EDA de treino |
| `features/` | Schema, cálculo causal e validação de valores |
| `modeling/contracts/` | Interface executável dos modelos e schema das predições |
| `modeling/algorithms/` | Uma factory por algoritmo e catálogo explícito |
| `modeling/experiments/` | Políticas, treino compartilhado e executores específicos |
| `evaluation/` | Métricas de ranking, comparações pareadas e diagnósticos |
| `integrations/` | MLflow, persistência skops e exportação da referência |
| `serving/` | Contrato HTTP, carga da release e pontuação sem estado |

Os caminhos são relativos a `fraud_detection_mlops/`. A
[arquitetura](docs/ARCHITECTURE.md) explica dependências e migração. Os adaptadores
antigos foram retirados; use os módulos explícitos dos runbooks.

## Navegar e contribuir

| Objetivo | Referência |
| --- | --- |
| Entender o problema e os limites | [Contexto](docs/project/PROBLEM_CONTEXT.md) e [Model Card](docs/modeling/MODEL_CARD.md) |
| Acompanhar decisões e resultados | [Notebook principal](notebooks/fraud_detection_mlops.ipynb) e [mural](docs/project/ROADMAP.md) |
| Conferir dados e causalidade | [Pipeline](docs/data/DATA_PIPELINE.md), [dicionário](docs/data/DATA_DICTIONARY.md) e [Gold](docs/data/GOLD_CONTRACT.md) |
| Adicionar um modelo compatível | [Guia de contribuição](docs/modeling/CONTRIBUTING_MODELS.md) e [exemplo executável](examples/model_contribution.py) |
| Executar e recuperar uma etapa | [Operações](docs/operations/OPERATIONS.md), [MLflow](docs/operations/MLFLOW.md) e [testes](docs/operations/TESTING.md) |
| Exportar e pontuar o modelo existente | [Serving de laboratório](docs/operations/SERVING_CONTRACT.md) |
| Encontrar todos os documentos | [Índice](docs/README.md) |

Protocolos e recibos versionados ficam em `references/`; dados e modelos reais
ficam no armazenamento operacional. Compatibilidade com a interface não autoriza
abrir um holdout, alterar um gate ou promover um modelo.
