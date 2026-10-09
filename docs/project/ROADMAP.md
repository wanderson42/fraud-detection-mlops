# Mural de metas — Fraud Detection MLOps

Atualizado em 2026-10-09. Este é o **backlog principal do projeto**: metas, estado,
ordem de trabalho e critérios de conclusão. O [README](../../README.md) é a entrada;
a [arquitetura](../ARCHITECTURE.md) descreve o que existe e os contratos fixam as regras.

## O que queremos demonstrar

Construir um laboratório de priorização de investigação que percorra **dados →
features → modelo → inferência → feedback atrasado → monitoramento → atualização
controlada**, com execução reproduzível, recuperação de falhas e custo mensurado.
O objetivo do portfólio é demonstrar decisões de engenharia e operação de ML.
A fonte é simulada; a utilidade e os limites estão no [contexto](PROBLEM_CONTEXT.md).

**Onde estamos:** pipeline offline, tracking e primeiro ciclo de experimentação
validados localmente. As três ablações não passaram o gate; conservamos a referência
de 19 features. **A avaliação final foi executada e verificada pelo autor:**
AP 0,640703 e precisão diária @100 de 55%, com gate de laboratório aprovado.
O holdout de maio foi consumido; as métricas diárias foram revisadas. A API de
serving, a exportação e o wheel têm checks sintéticos; o autor aprovou 302 testes
locais. A exportação da referência real, Docker, streaming, CD e CT ainda precisam
de execução e evidência própria. O marco de organização/serving foi integrado pelo
[PR #1](https://github.com/wanderson42/fraud-detection-mlops/pull/1). O protocolo da
referência foi executado e verificado pelo autor em 02–15/09: AP 0,621312,
precisão diária @100 de 54,93% e recall médio diário de 72,89%, sem refit.
O autor aprovou 348 testes/94 avisos. A janela foi consumida; 16–30/09 continua
reservada para replay. Teste temporal formal e intervalo de generalização
continuam pendentes.

**Escopo acordado:** interface para novos modelos, otimização limitada com Optuna,
streaming com estado, feedback atrasado, monitoramento e atualização controlada
são entregas do projeto. Aprovar a referência offline não encerra o trabalho.
Controlar complexidade significa implementar essas capacidades com componentes
pequenos e medir seu custo. Redes neurais, serviços de feature store e infraestrutura
adicional continuam condicionados a uma necessidade demonstrada.

**HGB = Histogram-based Gradient Boosting**, ou boosting de árvores baseado em
histogramas. Neste projeto é o `HistGradientBoostingClassifier` do scikit-learn.
A sigla identifica a referência atual; novos modelos dependem de hipótese e protocolo.

## Metas e critérios de conclusão

Estados: **validado localmente** = execução relatada e evidência registrada;
**próximo** = trabalho prioritário ainda pendente; **planejado** = capacidade a construir;
**condicional** = escolha que precisa justificar custo e benefício. Uma meta planejada
não representa uma tecnologia instalada nem uma aprovação em produção.

| Meta | Estado | Entrega que permite concluir |
| --- | --- | --- |
| Dados reproduzíveis: Bronze, Silver e Gold | Validado localmente | Fonte fixada, integridade, contratos, reconciliação, features causais e separação temporal. [Pipeline](../data/DATA_PIPELINE.md), [dicionário](../data/DATA_DICTIONARY.md) e [Gold](../data/GOLD_CONTRACT.md). |
| Qualidade do software e CI | Marco anterior integrado em main; nova suíte aprovada pelo autor; CI da revisão a conferir | Poetry/lockfile, tox, Ruff e pytest; workflow de CI. O autor aprovou 348 testes/94 avisos antes do commit da avaliação. A CI precisa conferir a revisão publicada antes da integração. [Testing](../operations/TESTING.md). |
| Baseline, MLflow e explicabilidade | Validado localmente | Comparação na validação, pipelines skops, recarga, SHAP e permutação. [Baseline](../modeling/BASELINE.md), [MLflow](../operations/MLFLOW.md) e [diagnóstico](../modeling/DIAGNOSTICS.md). |
| Seleção de features por ablação | Primeiro ciclo concluído | Três fits sob política congelada; nenhuma ablação elegível; referência preservada, sem promover modelo. [Decisão e evidência](../modeling/EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08). |
| Congelamento da referência | Concluído | Build/verify informados pelo autor, recibo versionado em `623dc86` antes do acesso ao teste. [Evidência](../../references/evidence/freeze_execution_2026-10-08.json). |
| Avaliação final | Executado e verificado localmente; CSV diário revisado | AP 0,640703 e precisão diária @100 de 55%; critérios atingidos, sem refit ou promoção em produção. [Evidência](../../references/evidence/final_evaluation_execution_2026-10-08.json) e [Model Card](../modeling/MODEL_CARD.md). |
| Análise estatística e testes de hipóteses | Avaliação da referência executada e verificada pelo autor; teste temporal formal e intervalo de generalização pendentes | Modelo de abril e regras fixados antes do acesso; 134.467 transações e 769 capturas por cliente/dia. Referência condicional de fila aleatória não estima incerteza temporal do modelo. Nenhum candidato elegível ou teste confirmatório executado. [Resultado e limites](../modeling/EVALUATION_PROTOCOL.md#resultado-da-referência-em-setembro--relato-do-autor). |
| Interface para colaboradores adicionarem modelos | Implementado; exemplo sintético executado pelo autor | Factory pequena para estimator/Pipeline compatível com scikit-learn; features, classe positiva, parâmetros, seed e persistência explícitos. Adicionar um modelo de exemplo pelo mesmo fluxo, sem duplicar tracking, avaliação ou gates. [Fronteira de modelagem](../modeling/CONTRIBUTING_MODELS.md). |
| Validação temporal e otimização do HGB com Optuna | Estudo concluído no relatório do autor; referência conservada | Fixar novas janelas de desenvolvimento e avaliação antes da busca; respeitar disponibilidade de rótulos em cada corte. Estudo persistente com orçamento global, MLflow, métricas por janela e comparação justa com a referência. Primeiro estudo: HGB com as 19 features, sem misturar ablação e busca. Ganho não é garantido; concluir o estudo pode significar conservar a referência. |
| Contrato de inferência e Docker | API/exportação implementadas; checks sintéticos e wheel conferidos; Docker pendente | Recebe 19 features, devolve score identificado e rejeita entradas inválidas. Paridade JSON/HTTP, health/readiness e wheel fora do checkout conferidos na preparação. Falta exportar a referência no Alienware e medir a imagem Docker. [Contrato e execução](../operations/SERVING_CONTRACT.md). |
| Orquestração com Prefect | Planejado; escolha do projeto | Prefect auto-hospedado para encadear os módulos Python, registrar dependências e falhas, testar retries e retomada sem duplicação. Manter lógica independente do orquestrador e medir recursos. O consumidor de streaming terá contrato próprio. |
| Armazenamento de objetos | Planejado; backend a decidir | Separar dados e artefatos do container, preservar manifestos e demonstrar recuperação. Escolher S3 compatível, como RustFS, **ou** armazenamento Azure conforme o cenário; validar acessos e custo. Parquet/DuckDB continuam adequados à etapa local. |
| Streaming por replay histórico e paridade de features | Planejado; entrega central | Publicar transações e feedback como eventos separados; consumidor com estado causal e relógio explícito. Demonstrar duplicidade, empates, atraso, reinício e retomada; comparar features/scores offline e online e medir lag/recursos. Replay acelerado preserva os sete dias no relógio dos eventos. O replay será identificado como simulação. |
| Gestão de features e eventual feature store | Capacidade planejada; ferramenta condicional | Compartilhar definição/versionamento das features, manter estado e demonstrar paridade offline/online e disponibilidade no instante da decisão. Adotar um serviço de feature store apenas se resolver compartilhamento, latência ou governança que a solução simples não atender. |
| Monitoramento e dashboards | Planejado | Prometheus/Grafana para serviço, latência, erros e atraso do processamento; pipeline para qualidade, drift e desempenho quando os rótulos chegarem. Painel de investigação com orçamento, acertos e fraudes perdidas. Demonstrar alertas e recuperação em falhas controladas. |
| CD e governança de promoção | Planejado | Entregar imagem e modelo identificados, executar smoke tests e gates de dados/modelo/serviço, controlar a promoção e demonstrar rollback. Registry/aliases MLflow entram com o fluxo de release; o gate exploratório atual não autoriza deployment. |
| CT: treinamento contínuo controlado | Planejado | Definir gatilhos, disponibilidade dos rótulos, orçamento e avaliação temporal do candidato. Orquestrar treinamento, comparação e decisão auditável; manter a referência quando os gates falharem. Drift isolado não basta para promover modelo. |
| Kubernetes local com kind | Condicional | Adotar quando o serviço e o replay estiverem estáveis e houver cenário de implantação a demonstrar. Comprovar persistência, probes, recuperação e rollback; medir custo de recursos. |
| Terraform e implantação em cloud | Condicional | Selecionar um ambiente e orçamento, provisionar recursos realmente necessários, conferir recriação e destruição controlada. Evitar manter duas clouds ou duplicar a responsabilidade dos manifests de aplicação. |
| Novos ensembles e redes neurais | Condicional | Utilizar a interface comum e abrir comparação com hipótese, orçamento e avaliação temporal. HGB já é um ensemble de árvores. A complexidade adicional deve justificar desempenho, latência, memória e manutenção; a entrega de Optuna acima não depende de adotar outra família de modelos. |

## Próxima entrega concreta

1. Publicar a branch da avaliação com o relato do autor e conferir a CI antes
   da integração. A execução e a verificação nativas foram informadas; preservar
   manifesto, auditoria e predições no Alienware. [Resultado](../modeling/EVALUATION_PROTOCOL.md#resultado-da-referência-em-setembro--relato-do-autor).
2. Exportar os bytes do HGB congelado com paridade sobre a validação já usada e
   conferir HTTP/wheel identificado. Construir e medir Docker: usuário sem root,
   modelo somente leitura, memória, latência e recuperação. O
   [procedimento](../operations/SERVING_CONTRACT.md) mantém o escopo de laboratório.
3. Encadear operações estáveis com um fluxo pequeno de Prefect, conferindo retries
   e retomada sem duplicação. Depois implementar replay causal e monitoramento
   com feedback atrasado, sob seus contratos próprios.

O [resultado fornecido](../modeling/EVALUATION_PROTOCOL.md#resultado-da-busca-informado-pelo-autor)
registra 20 trials, 63 fits, nenhuma falha e `retain_reference`. Não houve confirmação
de candidato; 02–15/09 foi usada na avaliação da referência. Refatoração estrutural e qualidade dos modelos têm evidências
separadas; os testes usam dados sintéticos.

## Próximo ciclo de modelos e operação

O ciclo inicial produziu uma referência congelada, não um modelo definitivo.
O [protocolo executável](../../references/hgb_optuna_protocol_v1.json) autoriza desenvolvimento
em junho–agosto e reserva setembro. A tabela e o procedimento ficam no
[protocolo de avaliação](../modeling/EVALUATION_PROTOCOL.md#otimização-temporal-do-hgb-com-optuna-v1).
A Gold desse ciclo tem identidade própria; os artefatos da avaliação inicial
permanecem preservados.

**Optuna:** TPE, SQLite local e execução sequencial, com até 20 trials globais,
60 ajustes de busca e três ajustes de referência: **63 tentativas no total**.
O primeiro comando mede três ajustes de referência e um trial de três ajustes.
O custo real orientará os lotes seguintes; não há garantia de duração sem medição.
Limite de quatro threads; histórico causal e rótulos disponíveis em cada corte.

Os cinco hiperparâmetros variam no espaço fixado antes da busca. AP média dos três
cortes é o objetivo. Gates práticos exigem ganhos absolutos médios de AP ≥ 0,01 e
precisão @100 ≥ 0,02, sem perda de AP maior que 0,02 em qualquer corte. São critérios
de seleção de desenvolvimento, não testes de hipótese. Não há pruning, early stopping,
remoção de features, expansão do catálogo ou ajuste final nesta autorização.
O estudo foi concluído no relatório fornecido pelo autor: nenhum trial passou
o gate e a referência foi conservada. A confirmação permanece fechada.

**Degradação:** primeiro medir a referência fixa ao longo de períodos posteriores.
O modelo não perde qualidade apenas por envelhecer; mudanças no processo e nos
dados podem reduzir sua capacidade de ordenar fraudes. Monitorar prevalência,
distribuição de features/scores, AP e precisão/recall da fila quando os rótulos
ficarem disponíveis. Drift é um sinal de investigação, não prova isolada de queda
nem autorização para promover outro modelo. Comparações estatísticas precisam de
protocolo próprio e respeito à dependência temporal e por entidade.

**Streaming:** o marco é processar eventos, produzir features causais e scores,
receber rótulos posteriores e atualizar métricas. O replay não entrega o rótulo
junto à transação. A política de investigação será definida em relação ao momento
de decisão; a fila retrospectiva de um dia completo não vira uma decisão instantânea.
O pipeline deverá recuperar estado após reinício, tratar duplicatas e aplicar uma
política explícita para eventos atrasados, sem alterar silenciosamente scores já
emitidos. Comparar com o cálculo offline sob a mesma política de eventos.

**CT e promoção:** comparar a referência em serviço com candidatos treinados apenas
com rótulos disponíveis, nas mesmas janelas de avaliação. Retreinar pode reutilizar
hiperparâmetros aprovados; não executar uma busca Optuna a cada chegada de dados.
Promoção exige os gates definidos, identidade de release e possibilidade de rollback.
Prefect coordenará jobs de treinamento, avaliação e agregação; o consumidor de
eventos terá seu próprio processo. Não precisamos instalar Kubernetes para iniciar
essa demonstração.

Uma demonstração de portfólio deve mostrar o percurso completo: eventos chegando,
predições identificadas, feedback atrasado, painel de desempenho e recursos,
falha controlada com recuperação, candidato aceito ou rejeitado e rollback.
Cenários artificiais de drift ou falha terão identificação própria, separados
dos dados originais e das conclusões sobre a evolução histórica.

## Marco de análise estatística e testes de hipóteses

Optuna seleciona configurações; não fornece confirmação estatística por si só.
Como nenhum trial passou o gate, o
[protocolo v1](../modeling/EVALUATION_PROTOCOL.md#protocolo-estatístico-da-referência--v1)
fixou a avaliação do modelo de abril em 02–15/09. O plano lê somente JSONs; o
executor autorizado foi executado e verificado pelo autor, consumindo a reserva
de qualidade dessa janela. O cálculo de referência de fila aleatória
condiciona nas contagens observadas; seu intervalo descreve essa política, não a
qualidade futura do modelo.

Uma comparação confirmatória futura exige candidato congelado, avaliação ainda
não consultada, efeito relevante e método de incerteza justificados para a
dependência temporal e recorrência de entidades. O protocolo especifica essas
condições; teste formal e intervalo de generalização continuam pendentes.
O volume de transações não equivale à informação independente. Resultados de maio
são históricos; uma queda de AP isolada não identifica a causa da degradação.
A documentação de avaliação concentra os detalhes, sem um documento por métrica.

## Sequência operacional e controle de custo

O protocolo estatístico antecede a retomada da infraestrutura e o acesso reservado.
Serving/empacotamento antecedem a implantação. Prefect e armazenamento compartilhado
podem ser desenvolvidos nesse bloco, com interfaces pequenas. O replay estabelece
estado e disponibilidade das features; monitoramento e CD vêm com uma operação
observável. CT depende de feedback, avaliação e promoção já definidos. kind e
Terraform entram quando esses contratos estiverem estáveis e seu uso for justificado.

Os 183 dias e 1.754.155 transações permitem exercitar replay, janelas, feedback e
falhas. Isso não demonstra carga de uma instituição real. Definiremos cenários
de carga e mediremos latência, memória, throughput e custo no ambiente escolhido.
Dados de períodos posteriores só entram sob protocolo temporal explícito; não são
automaticamente novas amostras independentes para confirmar os achados da validação.

Cada entrega deve resolver uma operação ou falha concreta, indicar seu custo e
produzir evidência suficiente para revisão. Não exigimos ferramenta, documento,
notebook ou teste novo para toda alteração. Mantemos uma referência principal por
assunto, preferimos APIs nativas e removemos caminhos de escrita aposentados.

## Evidências e referências

- [Ablações: decisão e resultados registrados em 2026-10-08](../modeling/EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08).
- [Avaliação final informada pelo autor](../../references/evidence/final_evaluation_execution_2026-10-08.json) e [Model Card](../modeling/MODEL_CARD.md).
- [Protocolo temporal](../modeling/EVALUATION_PROTOCOL.md): dados, rótulos e métricas.
- [Stakeholders](STAKEHOLDERS.md): interpretação e utilidade dos resultados.
- [Google Cloud: MLOps, CI/CD/CT e operação de sistemas de ML](https://docs.cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning).
- [scikit-learn: HistGradientBoostingClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html).
- [Handbook: validação temporal e atraso de rótulos](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html).
- [Optuna: estudo, armazenamento e retomada](https://optuna.readthedocs.io/en/stable/reference/generated/optuna.create_study.html).
- [Optuna: amostragem e pruning](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/003_efficient_optimization_algorithms.html).

O mural é uma decisão de escopo deste projeto, informado pelas referências.
Não é uma certificação de maturidade nem uma promessa de eficácia antifraude real.
