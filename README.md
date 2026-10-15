# Fraud Detection MLOps

Laboratório de MLOps para priorização de investigação de fraude, desenvolvido por
Wanderson Ferreira. Usa dados simulados do Fraud Detection Handbook, histórico
causal, feedback atrasado e evidências auditáveis. A inferência por streaming
para triagem próxima do tempo real é um objetivo central do projeto.

Com capacidade limitada de investigação, quais clientes priorizar usando apenas
a informação disponível no momento da decisão?

## Estado do projeto

Atualizado em **2026-10-15**. O laboratório percorre Bronze → Silver → Gold,
validação temporal, tracking MLflow/skops, serving HTTP/Docker e replay causal com
feedback atrasado. A referência HGB (Histogram-based Gradient Boosting) de 19 features foi conservada após ablações e
20 trials Optuna/63 fits: nenhum candidato passou os gates de substituição.

**O replay corretivo de 16–30/09/2018 foi executado e verificado pelo autor.**
São 556.824 eventos, 143.945 transações pontuadas e 1.453 alertas. A regra
`exact_binary64_mean_v1` resolveu a divergência SQLite/DuckDB: os 19 atributos
coincidem exatamente e os scores passaram o gate original (`atol=1e-12`, `rtol=0`).
O checkpoint anterior, o modelo e o limiar permaneceram preservados.

| Resultado nos 15 dias | Fila causal | Top-100 retrospectivo |
| --- | ---: | ---: |
| Alertas de cliente-dia | 1.453 | 1.500 |
| Clientes-dia fraudulentos capturados | 768 | 800 |
| Clientes-dia fraudulentos fora dos alertas | 358 | 326 |
| Precisão agregada | 52,86% | 53,33% |
| Recall agregado | 68,21% | 71,05% |

A fila usa o primeiro cruzamento do limiar e até 100 clientes por dia. O
retrospectivo ordena pelo máximo score após observar o dia completo. A comparação
é descritiva; as 32 capturas adicionais não provam superioridade operacional.
Cliente-dia não é pessoa única, e captura não equivale a perda financeira evitada.
AP transacional no replay: **0,578783**; ROC AUC: **0,876322**. Os resultados de
maio (AP 0,640703) e de 02–15/09 (AP 0,621312) pertencem a outras janelas e não
constituem, isoladamente, evidência de degradação. Essas janelas foram consumidas.

A [evidência reconciliada](references/evidence/native_numerical_correction_author_validation_2026-10-13.json)
transcreve o `run` e o `verify` fornecidos pelo autor. Os bytes dos checkpoints,
scores e arquivos de medição continuam locais e não foram inspecionados pelo
assistente. [Resultado, procedimento e limites](docs/operations/NUMERICAL_CORRECTIVE_REPLAY.md#resultado-nativo-informado-pelo-autor--2026-10-13).

HTTP/wheel e Docker também foram validados pelo autor. A imagem reportada tem
166,8 MiB; o snapshot de memória foi 178,5 MiB e o p95 HTTP foi 4,74 ms em 100
requisições sequenciais ao caso de controle. Essa medição é de serving local e
não mede a latência do fluxo de eventos. [Serving](docs/operations/SERVING_CONTRACT.md).
A preparação da correção aprovou **595 testes/93 avisos**, com tox `py314`, Ruff,
formatação e lockfile; isso é evidência de fixtures, separada do replay nativo.

**Incremento atual:** replay com emissão programada/fila limitada, telemetria,
exporter, Prometheus e dois dashboards Grafana preparados e validados com dados
gerados. Execução nativa e ritmo 1x/SLA continuam pendentes.
[Procedimento e limites](docs/operations/OBSERVABILITY.md).
**Sequência revista:** medir operação/monitoramento → RustFS S3 local e restauração
→ CD/rollback → dados/features e CT. Feature store tem spike condicionado a
reutilização e correção temporal; orquestração e Kubernetes vêm depois da necessidade.
Não há custo obrigatório de serviço cloud nesta fase.
O [mural](docs/project/ROADMAP.md) mantém critérios e prioridades; a
[síntese para stakeholders](docs/project/STAKEHOLDERS.md) explica a utilidade.
A fonte é simulada; não há operação bancária real, publicação externa de alertas
ou promoção em produção. A organização inicial usou
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
| `streaming/contracts/`, `features/`, `replay/`, `decisions/`, `evaluation/` | Eventos, estado causal, replay durável, triagem e avaliação da fila emitida |

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
