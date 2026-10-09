# Fraud Detection MLOps

Laboratório reproduzível de MLOps para **priorização de investigação de fraude**,
com dados temporais simulados do Fraud Detection Handbook, feedback atrasado e
evidências auditáveis. Desenvolvido por Wanderson Ferreira.

> Com capacidade limitada de investigação, quais clientes devemos priorizar usando
> somente a informação disponível naquele momento, e como manter essa decisão
> confiável quando dados e padrões mudam?

**[Mural de metas e próximos passos](docs/ROADMAP.md)** · [Contexto do problema](docs/PROBLEM_CONTEXT.md) · [Notebook principal](notebooks/fraud_detection_mlops.ipynb)

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

Atualizado em 2026-10-08. Temos um **pipeline offline reproduzível e um primeiro
ciclo de experimentação concluído**. A referência conservada é HGB
(**Histogram-based Gradient Boosting**, boosting de árvores baseado em histogramas),
implementada por `HistGradientBoostingClassifier`, do scikit-learn.

| Capacidade | Evidência atual |
| --- | --- |
| Bronze e Silver | 183 arquivos/partições, 1.754.155 transações, 14.681 fraudes e 42 valores zero preservados; construção e verificação locais |
| EDA e Gold temporal | EDA de treino; 19 preditores, 42 partições e 402.877 linhas distribuídas entre treino, validação e teste |
| Baseline e MLflow/skops | Três modelos comparados e publicados; referência com AP 0,623949 e precisão diária @100 de 54% na validação |
| Diagnóstico e ablações | SHAP/permutação e três ablações concluídas; nenhum candidato passou o gate; conservamos as 19 features |
| Qualidade de software | Poetry, tox, Ruff, pytest e workflow de CI; último relato do autor: 164 testes aprovados em 21,11 s |
| Congelamento | Concluído e versionado em `623dc86`; 19 features, modelo e critérios fixados antes do teste |
| Avaliação final | Executada e verificada pelo autor: AP **0,640703**, precisão diária @100 **55%**; gate de laboratório aprovado |
| Operação | Serving, replay, orquestração, armazenamento remoto, monitoramento, CD e CT ainda planejados |

As execuções reais foram informadas pelo autor e registradas em
[`references/evidence/`](references/evidence/). A
[evidência da avaliação final](references/evidence/final_evaluation_execution_2026-10-08.json)
registra métricas, decisão e limites da conferência. Aprovação
local e CI de cada revisão são evidências distintas.

O [mural](docs/ROADMAP.md) reúne todas as metas, prioridade e critérios de conclusão,
incluindo testes de hipóteses, Prefect, Docker, streaming, Prometheus/Grafana e
as condições para kind/Terraform. A base é simulada; o resultado de validação não
comprova desempenho em operação. [Interpretação para stakeholders](docs/STAKEHOLDERS.md).

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
descrito abaixo; o teste foi preservado durante essa preparação e avaliado
posteriormente, conforme a Model Card.

## Baseline temporal

Depois de instalar o lockfile atualizado e verificar a Gold:

```bash
poetry install
make validate
poetry run python -m fraud_detection_mlops.modeling.train run
```

O experimento compara controle constante, regressão logística com escala ajustada
no treino e gradient boosting. A seleção usa AP da validação e não acessa o teste.
A avaliação final posterior está registrada na Model Card.
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

O [diagnóstico interpretado](docs/DIAGNOSTICS.md#resultados)
relaciona esses resultados às regras do simulador e distingue importância SHAP de
queda de AP. Features não foram removidas. O
[protocolo de experimentação](docs/EXPERIMENT_PROTOCOL.md) define três ablações,
orçamento de três fits e critérios práticos antes das próximas execuções.
O autor executou e verificou `21ccedf10d944092ba874153c1d21257` sobre `de41ee0`:
18 artefatos verificados, três candidatos registrados no MLflow e teste preservado
naquele marco.
Nenhuma ablação atingiu os dois ganhos mínimos do gate. Com 700 vagas semanais,
a referência priorizou 378 ocorrências fraudulentas de cliente/dia; as ablações,
374, 373 e 372. A precisão operacional piorou em todas as exclusões de dia.
A [revisão dos resultados](docs/EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08)
registra a decisão de conservar a referência. O catálogo foi encerrado, sem
promoção em produção ou hipótese confirmatória. A avaliação final veio depois
do congelamento dessa decisão.

## Contrato para contribuições de modelos

O contrato executável `model_interface_v1` define construção, entradas, rótulos,
pré-processamento e probabilidades esperadas. Os três modelos existentes usam essa
fronteira; uma contribuição Gaussian Naive Bayes demonstra fit, artefatos e MLflow
com dados sintéticos, fora da baseline fixa. A
[arquitetura](docs/ARCHITECTURE.md#interface-para-contribuição-de-modelos) contém as
regras, o procedimento de contribuição e o comando do exemplo.

Os checks rejeitam componentes já ajustados, desalinhamento, features inválidas e
saídas incompatíveis. Compatibilidade com a interface não autoriza um experimento
ou promove um modelo. A busca temporal com Optuna usa esse contrato, com execução
sobre os dados do autor ainda pendente; veja o [mural](docs/ROADMAP.md).

## Otimização temporal do HGB com Optuna

O executor `modeling.hgb_optuna` compara cinco hiperparâmetros do HGB em três cortes
posteriores, com 19 features, treino de 28 dias, gap de sete dias e validação de
sete dias. Os parâmetros de referência são retreinados nos mesmos dados de cada
corte: a comparação isola a configuração do efeito de atualizar o treino.

O estudo SQLite tem até **20 trials globais e 63 tentativas de ajuste**, incluindo
três ajustes de referência. A primeira chamada executa um trial (três ajustes),
além da referência; registre tempo e memória antes de continuar. Artefatos
concluídos são reutilizados; falhas consomem orçamento. Setembro permanece
reservado, e o teste histórico de maio não é aberto.

Depois de instalar as dependências, validar e fazer commit dos insumos:

```bash
poetry run python -m fraud_detection_mlops.modeling.hgb_optuna prepare
# Copie development_path retornado pelo comando acima.
DEVELOPMENT_PATH="CAMINHO_RETORNADO"
poetry run python -m fraud_detection_mlops.modeling.hgb_optuna optimize "$DEVELOPMENT_PATH"
poetry run python -m fraud_detection_mlops.modeling.hgb_optuna verify "$DEVELOPMENT_PATH/study"
```

`study_incomplete` é esperado após o primeiro trial. A conclusão pode ser
`retain_reference` ou `candidate_for_confirmation_review`; a busca não promove um
modelo. Janelas, espaço de busca, custos, gates e recuperação estão no
[protocolo](docs/EVALUATION_PROTOCOL.md#otimização-temporal-do-hgb-com-optuna-v1). O
[notebook 10](notebooks/stages/10_hgb_optuna.ipynb) revisa apenas resultados
salvos. Ganhos, degradação e superioridade estatística ainda precisam de evidência.

## Congelar antes de avaliar o teste

O executor `modeling.freeze` conserva o HGB existente e suas 19 features, confere
paridade na validação e cria `references/frozen_candidate_v1.json`. Versione primeiro
a implementação e depois o recibo, conforme o
[procedimento](docs/EVALUATION_PROTOCOL.md#procedimento-local-em-dois-commits).
O autor concluiu o congelamento e a verificação com `--require-committed`.
O [recibo versionado](references/frozen_candidate_v1.json) identifica essa decisão.

A política propõe AP ≥ 0,50 e precisão diária @100 ≥ 0,45 para seguir ao serving
de laboratório. São critérios práticos definidos antes do teste, sem estimativa
financeira ou autorização para produção. O executor da avaliação final usa o
recibo e publica uma run MLflow própria, preservando os modelos históricos.

Depois de validar e versionar a implementação:

```bash
poetry run python -m fraud_detection_mlops.modeling.evaluation run
```

O comando original consulta o teste reservado e gera scores, métricas diárias, decisão e
Model Card. Uma nova chamada reutiliza o resultado verificado. O
[procedimento e a recuperação](docs/EVALUATION_PROTOCOL.md#executar-a-avaliação-final)
explicam a fronteira do teste; o
[notebook da etapa](notebooks/stages/09_final_evaluation.ipynb) orienta a revisão.

O autor executou e verificou o teste: AP 0,640703 e precisão diária @100 de 55%,
sem refit. Ambos os critérios foram atingidos. A [Model Card](docs/MODEL_CARD.md)
registra o resultado e os limites. A revisão diária identificou recall médio
de **73,02%**, com precisão variando entre **47% e 61%**.
**Este holdout foi consumido.** Melhorias futuras exigem outra janela e protocolo.
Para conferir esse resultado, use `evaluation verify` sobre os artefatos salvos.

## Documentação

Comece pelo **[mural de metas](docs/ROADMAP.md)** para localizar o estado atual e a
próxima entrega. Os detalhes têm uma referência principal por assunto:

| Pergunta | Referências |
| --- | --- |
| Por que existe e para quem serve? | [Contexto](docs/PROBLEM_CONTEXT.md) e [stakeholders](docs/STAKEHOLDERS.md) |
| Quais dados e regras usamos? | [Dicionário](docs/DATA_DICTIONARY.md), [pipeline](docs/DATA_PIPELINE.md), contratos [Silver](docs/SILVER_CONTRACT.md) e [Gold](docs/GOLD_CONTRACT.md) |
| Como avaliamos e o que aprendemos? | [Model Card](docs/MODEL_CARD.md), [EDA](docs/EDA.md), [avaliação temporal](docs/EVALUATION_PROTOCOL.md), [baseline](docs/BASELINE.md), [diagnóstico](docs/DIAGNOSTICS.md) e [experimentos](docs/EXPERIMENT_PROTOCOL.md) |
| Como executar, recuperar e validar? | [Operations](docs/OPERATIONS.md), [MLflow](docs/MLFLOW.md), [execução das ablações](docs/EXPERIMENT_EXECUTION.md) e [Testing](docs/TESTING.md) |
| Como organizar e manter o projeto? | [Arquitetura](docs/ARCHITECTURE.md) e [política documental](docs/DOCUMENTATION_POLICY.md) |

O [notebook principal](notebooks/fraud_detection_mlops.ipynb) apresenta a narrativa
e aponta para os [notebooks de cada etapa](notebooks/stages/), legíveis no GitHub.
Outputs publicados preservam sua origem; células pendentes não recebem resultados
pré-fabricados. A Model Card resume o modelo avaliado; infraestrutura terá
documentação quando houver implementação concreta, conforme o mural.

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
| `fraud_detection_mlops/modeling/experiments.py` | Execução limitada das ablações, checkpoints e verificação offline |
| `fraud_detection_mlops/modeling/comparison.py` | Efeitos pareados, exclusão de dias e gate de desenvolvimento |
| `fraud_detection_mlops/modeling/freeze.py` | Congelamento da referência existente, sem ajuste ou leitura do teste |
| `fraud_detection_mlops/modeling/evaluation.py` | Teste final do candidato congelado, decisão fixa e publicação da avaliação |
| `fraud_detection_mlops/dataset.py` | CLI de extração e verificação |
| `references/` | Inventário e recibos documentais |
| `tests/` | Integridade, recuperação e integração Parquet/SQL |
| `tox.toml` | Sequência de qualidade no ambiente Python 3.14 isolado |
| `.github/workflows/ci.yml` | Qualidade automatizada |

Os scaffolds vazios foram removidos. Serving será implementado quando houver
um contrato de inferência; a EDA já produz gráficos reais. Os testes de modelagem
espelham `modeling/` e compartilham fixtures por `conftest.py`.
