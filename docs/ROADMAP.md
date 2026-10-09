# Mural de metas — Fraud Detection MLOps

Atualizado em 2026-10-08. Este é o **backlog principal do projeto**: metas, estado,
ordem de trabalho e critérios de conclusão. O [README](../README.md) é a entrada;
a [arquitetura](ARCHITECTURE.md) descreve o que existe e os contratos fixam as regras.

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
O holdout foi consumido; as métricas diárias foram revisadas. Serving, streaming, CD e
CT ainda precisam de implementação e evidência própria.

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
| Dados reproduzíveis: Bronze, Silver e Gold | Validado localmente | Fonte fixada, integridade, contratos, reconciliação, features causais e separação temporal. [Pipeline](DATA_PIPELINE.md), [dicionário](DATA_DICTIONARY.md) e [Gold](GOLD_CONTRACT.md). |
| Qualidade do software e CI | Implementado; suíte validada localmente | Poetry/lockfile, tox, Ruff e pytest; workflow de CI. Último relato do autor: 164 testes aprovados. Cada revisão precisa dos seus checks. [Testing](TESTING.md). |
| Baseline, MLflow e explicabilidade | Validado localmente | Comparação na validação, pipelines skops, recarga, SHAP e permutação. [Baseline](BASELINE.md), [MLflow](MLFLOW.md) e [diagnóstico](DIAGNOSTICS.md). |
| Seleção de features por ablação | Primeiro ciclo concluído | Três fits sob política congelada; nenhuma ablação elegível; referência preservada, sem promover modelo. [Decisão e evidência](EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08). |
| Congelamento da referência | Concluído | Build/verify informados pelo autor, recibo versionado em `623dc86` antes do acesso ao teste. [Evidência](../references/evidence/freeze_execution_2026-10-08.json). |
| Avaliação final | Executado e verificado localmente; CSV diário revisado | AP 0,640703 e precisão diária @100 de 55%; critérios atingidos, sem refit ou promoção em produção. [Evidência](../references/evidence/final_evaluation_execution_2026-10-08.json) e [Model Card](MODEL_CARD.md). |
| Análise estatística e testes de hipóteses | Planejado; entrega do escopo; protocolo confirmatório pendente | Definir hipótese, efeito relevante, unidade de inferência e dependência temporal/por entidade antes de novas comparações. Obter mais evidência temporal separada do desenvolvimento; registrar tamanho de efeito e incerteza apropriada. A exclusão de um dia da ablação é sensibilidade, não intervalo de confiança. [Marco estatístico](#marco-de-análise-estatística-e-testes-de-hipóteses). |
| Interface para colaboradores adicionarem modelos | Implementado; exemplo sintético executado pelo autor | Factory pequena para estimator/Pipeline compatível com scikit-learn; features, classe positiva, parâmetros, seed e persistência explícitos. Adicionar um modelo de exemplo pelo mesmo fluxo, sem duplicar tracking, avaliação ou gates. [Fronteira de modelagem](ARCHITECTURE.md#interface-para-contribuição-de-modelos). |
| Validação temporal e otimização com Optuna | Implementado; execução local pendente | Fixar novas janelas de desenvolvimento e avaliação antes da busca; respeitar disponibilidade de rótulos em cada corte. Estudo persistente com orçamento global, MLflow, métricas por janela e comparação justa com a referência. Primeiro estudo: HGB com as 19 features, sem misturar ablação e busca. Ganho não é garantido; concluir o estudo pode significar conservar a referência. |
| Contrato de inferência e Docker | Contrato inicial definido; após revisão da busca e protocolo confirmatório | Receber 19 features calculadas, devolver score identificado e rejeitar entradas inválidas. [Contrato proposto](ARCHITECTURE.md#primeiro-contrato-de-inferência--definido-implementação-pendente). Demonstrar paridade, execução fora do checkout, health/readiness, imagem mensurada e recuperação. |
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

1. Versionar o protocolo e o executor Optuna; preparar a Gold de desenvolvimento.
   O autor já consolidou a avaliação final e executou o exemplo da interface.
2. Executar três ajustes de referência e um trial nas mesmas três janelas; conferir
   integridade, custos e métricas. Continuar em lotes pequenos até o orçamento global
   de 20 trials, incluindo falhas; a conclusão pode ser conservar a referência.
3. Revisar efeitos, precisão diária, variação por corte e custo. Fixar o protocolo
   confirmatório e estatístico antes de consultar setembro. A seleção de desenvolvimento
   não demonstra superioridade nem autoriza promoção.
4. Executar a confirmação pareada em sua janela reservada, conforme esse novo
   protocolo, e documentar a decisão. Preservar o teste histórico consumido.
5. Implementar serving com paridade, identidade, health/readiness e execução fora do
   checkout; depois medir Docker e demonstrar streaming, feedback atrasado,
   monitoramento, atualização controlada e rollback.

A preparação do executor é validada com dados sintéticos. Ganho de desempenho,
custo real e estabilidade da referência ainda dependem de execução local.

## Próximo ciclo de modelos e operação

O ciclo inicial produziu uma referência congelada, não um modelo definitivo.
O [protocolo executável](../references/optuna_protocol_v1.json) autoriza desenvolvimento
em junho–agosto e reserva setembro. A tabela e o procedimento ficam no
[protocolo de avaliação](EVALUATION_PROTOCOL.md#busca-temporal-com-optuna-v1).
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
A execução e a análise do estudo ainda estão pendentes.

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

Esta entrega permanece no escopo e tem propósito próprio. Optuna seleciona
configurações; a análise estatística estima efeito e incerteza nas comparações.
O protocolo deve ser fixado antes de observar os resultados das janelas reservadas
para avaliação do candidato, após concluir a seleção no desenvolvimento.

1. Formular uma hipótese principal para a comparação entre candidato e referência,
   definir AP como desfecho principal e o ganho mínimo relevante. Precisão/recall
   da fila e custo entram como critérios operacionais declarados antes da avaliação.
2. Definir cortes temporais, disponibilidade de rótulos e população comum de
   comparação. Registrar scores pareados por transação e efeitos por janela;
   as métricas de investigação usam clientes por dia e não transações como unidade.
3. Examinar dependência temporal e recorrência de entidades para justificar a
   unidade de inferência e o método de incerteza. Planejar, por exemplo, reamostragem
   pareada em blocos temporais suficientemente longos, se as condições permitirem;
   não usar bootstrap de linhas independentes como padrão. A quantidade de
   transações não equivale à quantidade de observações independentes.
4. Relatar tamanho de efeito e intervalo de incerteza; um teste formal e seu nível
   de significância precisam de justificativa prévia. Tratar comparações múltiplas
   e consultas repetidas ao monitoramento conforme a política definida. Não gerar
   p-values para cada gráfico ou confundir significância com utilidade operacional.
5. Separar análise exploratória, comparação confirmatória e acompanhamento
   operacional. Os sete dias já utilizados não serão reaproveitados como confirmação
   independente das hipóteses que eles ajudaram a criar. Se as novas janelas não
   sustentarem a inferência escolhida, registrar a limitação e apresentar resultados
   descritivos, sem afirmar superioridade ou ausência de degradação.

O resultado desse marco é uma comparação revisável com hipótese, população,
efeito, incerteza e decisão. A análise de degradação também considerará prevalência
e política da fila: uma queda de AP isolada não identifica a causa. Manteremos
o protocolo e seus resultados nos documentos de avaliação já existentes, evitando
um documento adicional para cada teste ou métrica.

## Sequência operacional e controle de custo

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

- [Ablações: decisão e resultados registrados em 2026-10-08](EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08).
- [Avaliação final informada pelo autor](../references/evidence/final_evaluation_execution_2026-10-08.json) e [Model Card](MODEL_CARD.md).
- [Protocolo temporal](EVALUATION_PROTOCOL.md): dados, rótulos e métricas.
- [Stakeholders](STAKEHOLDERS.md): interpretação e utilidade dos resultados.
- [Google Cloud: MLOps, CI/CD/CT e operação de sistemas de ML](https://docs.cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning).
- [scikit-learn: HistGradientBoostingClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html).
- [Handbook: validação temporal e atraso de rótulos](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html).
- [Optuna: estudo, armazenamento e retomada](https://optuna.readthedocs.io/en/stable/reference/generated/optuna.create_study.html).
- [Optuna: amostragem e pruning](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/003_efficient_optimization_algorithms.html).

O mural é uma decisão de escopo deste projeto, informado pelas referências.
Não é uma certificação de maturidade nem uma promessa de eficácia antifraude real.
