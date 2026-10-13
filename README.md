# Fraud Detection MLOps

Laboratório de MLOps para priorização de investigação de fraude, desenvolvido por
Wanderson Ferreira. Usa dados simulados do Fraud Detection Handbook, histórico
causal, feedback atrasado e evidências auditáveis. A inferência por streaming
para triagem próxima do tempo real é um objetivo central do projeto.

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
O autor exportou a referência congelada com 67.255 linhas de paridade e validou
HTTP/wheel fora do checkout, sem as dependências de experimentação. O
[relato nativo](references/evidence/laboratory_serving_native_validation_2026-10-09.json)
preserva identidade e limites. A suíte local do marco anterior aprovou 302 testes,
e esse marco foi integrado em `main` pelo
[PR #1](https://github.com/wanderson42/fraud-detection-mlops/pull/1).
O [protocolo estatístico da referência](docs/modeling/EVALUATION_PROTOCOL.md#protocolo-estatístico-da-referência--v1)
fixa 2–15/09 para avaliação e 16–30/09 para replay. O
[executor auditável](docs/modeling/EVALUATION_PROTOCOL.md#executor-auditável-da-janela-fixada)
foi executado e verificado pelo autor em 02–15/09/2018: **134.467 transações**,
AP **0,621312**, precisão diária @100 de **54,93%** e recall médio diário de clientes
fraudulentos de **72,89%**, sem refit ou novas runs. O autor aprovou **362 testes**,
com 94 avisos. Essa janela foi consumida; **16–30/09 continua reservado para replay**.
O [relato da execução](docs/modeling/EVALUATION_PROTOCOL.md#resultado-da-referência-em-setembro--relato-do-autor)
mantém os limites estatísticos. O diagnóstico e a reamostragem nativa por blocos
foram revisados: **o escopo exploratório está concluído**, com todas as réplicas
definidas e faixas da AP entre aproximadamente 0,579 e 0,667 ao comparar os quatro
comprimentos. Essas faixas não têm cobertura de generalização demonstrada.
O [PR #2](https://github.com/wanderson42/fraud-detection-mlops/pull/2) integrou essa
etapa, após 379 testes nativos e CI aprovados. O autor também construiu e validou
Docker: paridade HTTP, reinício, proteções e três falhas de carga passaram.
A imagem reportada tem **166,8 MiB**; o snapshot de memória foi **178,5 MiB** e
a latência p95 **4,74 ms** em 100 requisições sequenciais ao caso de controle.
São medições locais, sem validação de carga concorrente ou SLA.
O [recibo nativo](references/evidence/laboratory_serving_docker_native_validation_2026-10-09.json)
preserva o resultado e seu alcance. O PR Docker foi integrado pelo autor.
O autor validou e commitou os contratos e o executor local de replay em
`23c863e`: **448 testes aprovados** e demonstração sintética interrompida,
retomada e repetida. Ao final, 20 eventos únicos e dez requests de atributos
permaneceram gravados; a repetição aceitou zero eventos e reaproveitou 22 entregas.
[Relato e limites](docs/operations/CAUSAL_REPLAY.md#execução-no-ambiente-do-autor--2026-10-13).
Executor e relato foram integrados pelo
[PR #4](https://github.com/wanderson42/fraud-detection-mlops/pull/4). A política de
alerta no primeiro cruzamento de limiar está preparada com fixtures: até cem
clientes/dia, sem substituição posterior. O autor validou 502 testes e executou/
verificou o seletor em 201.155 transações de 21 dias de desenvolvimento: limiar
**0,8826493808370081**, com configuração identificada. A integração durável e
a qualidade/latência da fila causal seguem pendentes.
[Resultado e limites](docs/operations/CAUSAL_REPLAY.md#resultado-do-limiar-informado-pelo-autor--2026-10-13).
[Regras e avaliação](docs/operations/CAUSAL_REPLAY.md#política-operacional-de-triagem).
O replay nativo continua pendente e 16–30/09 permanece reservado, conforme o
[mural](docs/project/ROADMAP.md#próxima-entrega-concreta). A adoção de um
orquestrador depende de necessidade operacional demonstrada.
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
| `streaming/contracts/`, `features/`, `replay/`, `decisions/` | Eventos, estado causal, replay local e política de investigação |

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
