# Fraud Detection MLOps

Laboratório reproduzível de MLOps para **priorização de investigação de fraude**,
com dados temporais simulados do Fraud Detection Handbook, feedback atrasado e
evidências auditáveis. Desenvolvido por Wanderson Ferreira.

> Com capacidade limitada de investigação, quais clientes devemos priorizar usando
> somente a informação disponível naquele momento, e como manter essa decisão
> confiável quando dados e padrões mudam?

## Por que este projeto existe

Fraude envolve uma decisão sob restrições: investigar custa tempo, alertas incorretos
consomem capacidade e uma confirmação pode chegar depois da transação. O modelo
precisa apoiar esse processo. O Handbook fornece a base conceitual e uma simulação
controlada; nosso trabalho liga essa base a contratos de dados, avaliação temporal,
experimentos rastreáveis e à futura operação por eventos.

A contribuição do portfólio é tornar essas decisões reproduzíveis e discutíveis:
qual informação estava disponível, quem entra no orçamento de revisão, como o
resultado muda e qual evidência permitiria promover um modelo. Benchmarks ajudam
na comparação; nossa avaliação também precisa explicar a política de investigação
e os limites da simulação.

O [contexto do problema](docs/PROBLEM_CONTEXT.md) apresenta marcos de 1994 aos
relatórios recentes de pagamentos, fontes primárias, relevância para o Brasil,
usos possíveis e limites de generalização. A implementação atual é um pipeline
offline validado localmente, com baseline e diagnóstico. Streaming, serving,
monitoramento e avaliação final ainda têm marcos próprios.

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
| Baseline e comparação na validação | Execução real informada pelo autor; AP 0,623949 do gradient boosting; teste reservado |
| MLflow e skops | Três modelos publicados e verificados localmente pelo autor; sem refit ou avaliação do teste |
| Diagnóstico da baseline | Execução e verificação locais informadas; sete outputs e tabelas de erros, permutação e SHAP |
| Experimentos controlados | Protocolo `experiment_v1` definido; runner e ablações ainda pendentes |
| Avaliação final do teste | Reservada para depois do congelamento das escolhas |
| Streaming, feature store, serving e monitoramento | Evolução pretendida; desenho e validação pendentes |

A base é simulada. Há comparação de modelos na validação; ainda não há avaliação
final do teste ou resultado comprovado de detecção em operação.

O diagnóstico completo informou IDs globalmente únicos, 14.681 fraudes, 42 valores
monetários zero e nenhuma violação nos demais controles implementados. Os zeros
foram preservados conforme o [contrato da Silver](docs/SILVER_CONTRACT.md).
O [recibo do diagnóstico](references/evidence/silver_profile_2026-10-06.json)
registra a saída local e os 50 testes aprovados informados nessa etapa.

## Quick Start

A estrutura inicial usou [Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/),
com responsabilidades ajustadas ao escopo descrito na [arquitetura](docs/ARCHITECTURE.md).

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
controladas; a construção real foi executada pelo autor. O baseline ajustado está
descrito abaixo; o teste final permanece reservado.

## Baseline temporal

Depois de instalar o lockfile atualizado e verificar a Gold:

```bash
poetry install
make validate
poetry run python -m fraud_detection_mlops.modeling.train run
```

O experimento compara controle constante, regressão logística com escala ajustada
no treino e gradient boosting. A seleção usa AP da validação; o teste fica reservado.
Novas execuções usam `baseline_v2`, pipelines skops e três runs MLflow independentes.
A política de modelagem permanece igual. Não é necessário repetir o baseline
histórico para atualizar o código. Use o `baseline_path` retornado para verificar:

```bash
poetry run python -m fraud_detection_mlops.modeling.train verify "<baseline_path>"
```

O [baseline](docs/BASELINE.md) define candidatos, métricas, artefatos e limites.
O autor informou execução e verificação reais: AP 0,623949 do gradient boosting,
contra 0,435001 da regressão e 0,008624 do controle. [Evidência](references/evidence/baseline_validation_2026-10-07.json).
A Gold e seu protocolo permanecem com as mesmas versões. Uma semana de validação
não comprova estabilidade, superioridade estatística ou desempenho em produção.

## Tracking local e modelos skops

A [etapa MLflow](docs/MLFLOW.md) publica os três pipelines já treinados em runs
separadas, sem refit ou avaliação do teste. SQLite e artefatos ficam sob `data/tracking`.
O pacote do modelo usa skops, com scores conferidos após recarga e assinatura das
19 features. `make mlflow-ui` abre a interface em <http://127.0.0.1:5001>.
O [runbook](docs/MLFLOW.md) descreve a publicação nativa durante o treinamento.
A migração histórica foi concluída e retirada do código ativo; seus artefatos
continuam preservados. A [arquitetura](docs/ARCHITECTURE.md) registra a organização
e os critérios para controlar a complexidade.

O [diagnóstico da baseline](docs/DIAGNOSTICS.md) usa o HGB já publicado para analisar
erros diários, importância por permutação e SHAP na validação. O orçamento padrão
é de cinco embaralhamentos por feature e 1.000 exemplos uniformes para SHAP.
O autor executou e verificou o diagnóstico `961bb0c32fb84433af922908ea58e76b`:
sete outputs, 142 testes locais aprovados e notebook atualizado. O
[recibo](references/evidence/diagnostics_execution_2026-10-07.json) registra as tabelas
compartilhadas e seus limites. Ao mesmo orçamento de 100 clientes por dia, HGB
priorizou 378 ocorrências fraudulentas de cliente/dia, contra 336 da regressão,
nos sete dias de validação. Não são pessoas únicas nem perdas financeiras evitadas.

O [diagnóstico interpretado](docs/DIAGNOSTICS.md#resultados-reais-informados-pelo-autor)
relaciona esses resultados às regras do simulador e distingue importância SHAP de
queda de AP. Features não foram removidas. O
[protocolo de experimentação](docs/EXPERIMENT_PROTOCOL.md) define três ablações,
orçamento de três fits e critérios práticos antes das próximas execuções.
Usaremos a referência existente e análise pareada de influência dos sete dias;
testes formais de superioridade precisam de mais evidência temporal. O runner
ainda será implementado, e o teste final permanece reservado.

## Documentação

| Leitura | Propósito |
| --- | --- |
| [Dicionário de dados](docs/DATA_DICTIONARY.md) | Campos Bronze/Silver, preditores Gold, fórmulas e metadados |
| [Contexto e propósito](docs/PROBLEM_CONTEXT.md) | História, problema operacional, relevância atual, referências e limites da simulação |
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
| [Baseline](docs/BASELINE.md) | Candidatos fixos, treinamento no treino e comparação na validação |
| [MLflow](docs/MLFLOW.md) | Tracking nativo, assinatura, recarga e armazenamento |
| [Arquitetura](docs/ARCHITECTURE.md) | Responsabilidades, testes e controle de complexidade |
| [Notebook MLflow](notebooks/stages/06_mlflow_tracking.ipynb) | Tracking atual e registros estáticos da execução histórica real |
| [Notebook do baseline](notebooks/stages/05_temporal_baseline.ipynb) | Leitura de uma execução explícita e interpretação das métricas |
| [Diagnóstico da baseline](docs/DIAGNOSTICS.md) | Erros diários, permutação por AP e SHAP na validação |
| [Notebook do diagnóstico](notebooks/stages/07_baseline_diagnostics.ipynb) | Leitura dos artefatos locais e registro de hipóteses |
| [Protocolo de experimentação](docs/EXPERIMENT_PROTOCOL.md) | Hipóteses, catálogo de ablações, análise pareada e gate de desenvolvimento |
| [Notebook do protocolo](notebooks/stages/08_controlled_experiments.ipynb) | Leitura do catálogo congelado; execuções e resultados ainda pendentes |
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
| `fraud_detection_mlops/artifacts.py` | JSON atômico e SHA256 compartilhados |
| `fraud_detection_mlops/modeling/baseline.py` | Política fixa e verificação de artefatos v1/v2 |
| `fraud_detection_mlops/modeling/persistence.py` | Pipelines skops e recarga |
| `fraud_detection_mlops/modeling/tracking.py` | Logging nativo MLflow e CLI de UI/verificação |
| `fraud_detection_mlops/modeling/train.py` | Treinamento, publicação e verificação do experimento de validação |
| `fraud_detection_mlops/modeling/metrics.py` | AP, ROC AUC e precisão diária por cliente |
| `fraud_detection_mlops/modeling/diagnostics.py` | Inspeção do modelo existente, usando somente a validação |
| `fraud_detection_mlops/dataset.py` | CLI de extração e verificação |
| `references/` | Inventário e recibos documentais |
| `tests/` | Integridade, recuperação e integração Parquet/SQL |
| `tox.toml` | Sequência de qualidade no ambiente Python 3.14 isolado |
| `.github/workflows/ci.yml` | Qualidade automatizada |

Os scaffolds vazios foram removidos. Serving será implementado quando houver
um contrato de inferência; a EDA já produz gráficos reais. Os testes de modelagem
espelham `modeling/` e compartilham fixtures por `conftest.py`.
