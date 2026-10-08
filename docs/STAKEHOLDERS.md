# Síntese para stakeholders

Atualizado em 2026-10-08. Estado atual: pipeline offline, baseline temporal,
diagnóstico e três ablações executados localmente pelo autor. A referência foi
conservada e a avaliação final passou o gate de laboratório. Operação por eventos
continua futura; as métricas diárias do teste foram revisadas.
Todas as metas e prioridades estão no [mural do projeto](ROADMAP.md).

## A decisão que pretendemos apoiar

Uma equipe com capacidade limitada precisa escolher quais clientes investigar.
Queremos avaliar como o ranking usa o histórico disponível, quais fraudes ficam
fora dos alertas e como conferir a origem de cada experimento. Isso aproxima a
modelagem de uma rotina de triagem, com orçamento e evidências explícitos.

O projeto parte de cartões simulados pelo Handbook. O modelo é uma parte de um
processo que também envolve regras, autenticação e investigação. A história dessa
área e sua relevância atual estão no [contexto do problema](PROBLEM_CONTEXT.md),
com fontes primárias e distinção entre cartões, pagamentos europeus e Pix.

## Quem pode usar o laboratório

| Público | Utilidade pretendida | Evidência que torna a utilidade concreta |
| --- | --- | --- |
| Profissionais em formação | Aprender causalidade temporal, feedback atrasado e operação de ML | Reproduzir o pipeline e investigar falhas controladas |
| Quem avalia o portfólio | Avaliar decisões e capacidade de construir sistemas de ML | Contratos, experimentos, testes e limites revisáveis |
| Equipes de dados e engenharia | Examinar uma referência de práticas em ambiente controlado | Adaptar e validar contratos e ensaios com seus requisitos |
| Analistas de investigação, como público futuro | Entender a fila de prioridades e seus erros | Política operacional, explicações e métricas de revisão; integração ainda pendente |

A utilidade demonstrada hoje é a reprodução e análise offline. Usar o modelo em
uma instituição exigiria dados autorizados, sinais do domínio, validação externa,
requisitos operacionais e uma política de ação própria.

## O que foi entregue

**HGB = Histogram-based Gradient Boosting**, ou boosting de árvores baseado em
histogramas. É o modelo `HistGradientBoostingClassifier` descrito no
[baseline](BASELINE.md), conservado após o primeiro ciclo de ablações.

| Capacidade | Resultado observado e origem |
| --- | --- |
| Dados rastreáveis | Fonte fixada e 183 arquivos íntegros; execução local informada |
| Base consultável | 1.754.155 linhas na Silver, com 14.681 fraudes e 42 valores zero preservados |
| Preparação temporal | Gold verificada: 19 preditores, 42 partições; treino, validação e teste separados |
| Comparação reproduzível | AP da validação: HGB 0,623949; regressão 0,435001; controle 0,008624 |
| Modelos identificáveis | Três pipelines MLflow/skops publicados e verificados pelo autor |
| Inspeção do resultado | Sete outputs de diagnóstico verificados; erros diários, permutação e SHAP informados |
| Experimentos limitados | Três ablações verificadas; nenhuma elegível; referência de 19 features conservada |
| Qualidade de implementação | Último relato local: 152 testes aprovados, lint, formatação e lockfile aprovados |

Os testes usam dados controlados. O sucesso deles não demonstra eficácia antifraude;
a execução real sobre a simulação é uma evidência separada. Os arquivos nativos
permanecem locais e não foram enviados nesta etapa.

## O que os números significam para a investigação

Na validação de sete dias, reservamos cem posições de clientes por dia. A regressão
priorizou 336 ocorrências fraudulentas de cliente/dia; o HGB priorizou 378, ao mesmo
orçamento total de 700 posições. Isso corresponde a precisão média de 48% e 54%.
O HGB deixou 133 ocorrências fraudulentas fora dos alertas, contra 175 da regressão.

O ganho observado foi de **42 ocorrências fraudulentas adicionais priorizadas**.
Não são necessariamente 42 pessoas diferentes: um cliente pode aparecer em vários
dias. Também não estimamos perdas evitadas, recuperação financeira ou transações
bloqueadas. O ranking usa o dia completo, retrospectivamente; não representa ainda
uma fila de decisões online. A [interpretação do diagnóstico](DIAGNOSTICS.md#resultados)
explica o cálculo, as importâncias e as hipóteses.

## Limites que afetam a leitura do resultado

A simulação contém regras conhecidas, inclusive um limiar artificial de valor que
marca fraude. A dependência do modelo desses sinais é informativa para verificar
o laboratório, mas não valida seu desempenho contra golpes reais.

A janela de validação tem sete dias, com clientes repetidos. A vantagem observada
não comprova superioridade estatística ou estabilidade em outros períodos. Não há
confirmação de calibração, deployment ou demonstração de
latência e disponibilidade do serviço. Todos os rótulos acabam disponíveis após
um atraso assumido; revisão seletiva e seus efeitos ainda não são modelados.

## Próximos marcos orientados pelo risco

A avaliação final foi executada e verificada pelo autor: AP 0,640703 e precisão
diária por cliente @100 de 55%, sem refit. A [Model Card](MODEL_CARD.md) concentra
a interpretação, os limites e a evidência; o gate permite revisão de serving de
laboratório, sem promoção em produção. O CSV diário apresentou recall médio de
73,02%, com precisão entre 47% e 61%. No total, 142 ocorrências de cliente com
fraude por dia ficaram fora das filas; pessoas podem reaparecer em outros dias.
O [mural de metas](ROADMAP.md) concentra a sequência de
serving, replay, orquestração, monitoramento e atualização controlada, além dos
critérios para Kubernetes e infraestrutura em cloud. A utilidade operacional
precisa de evidência própria em cada marco.

## Evidências e leituras

| Marco | Registro principal |
| --- | --- |
| Aquisição e contrato dos dados | [Bronze](../references/evidence/bronze_2026-10-06.json), [perfil](../references/evidence/silver_profile_2026-10-06.json) e [Silver](../references/evidence/silver_build_2026-10-06.json) |
| Exploração e preparação temporal | [EDA](../references/evidence/eda_training_2026-10-07.json) e [Gold](../references/evidence/gold_build_2026-10-07.json) |
| Modelos e comparação | [Baseline](../references/evidence/baseline_validation_2026-10-07.json) e [tracking histórico](../references/evidence/mlflow_execution_2026-10-07.json) |
| Diagnóstico | [Execução informada](../references/evidence/diagnostics_execution_2026-10-07.json) |
| Ablações | [Decisão e resultados registrados](EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08) |
| Avaliação final | [Execução informada](../references/evidence/final_evaluation_execution_2026-10-08.json) e [Model Card](MODEL_CARD.md) |

A [política de documentação](DOCUMENTATION_POLICY.md) define como separar relatos
locais, preparação e CI. [Arquitetura](ARCHITECTURE.md) descreve responsabilidades;
[Operations](OPERATIONS.md) descreve execução e recuperação. Cada recibo preserva
a origem e os limites de seu marco, sem atribuir resultados a revisões futuras.
