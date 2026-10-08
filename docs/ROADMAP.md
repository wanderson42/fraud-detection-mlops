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
de 19 features. **O teste final permanece reservado.** Serving, streaming, CD e CT
ainda precisam de implementação e evidência própria.

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
| Congelamento da referência | Concluído | Build/verify informados pelo autor, recibo versionado em `623dc86` e teste reservado. [Evidência](../references/evidence/freeze_execution_2026-10-08.json). |
| Avaliação final | Implementado; **execução real pendente** | Candidato fixo, controle constante, métricas, gate de laboratório, Model Card e run MLflow de avaliação. [Procedimento](EVALUATION_PROTOCOL.md#executar-a-avaliação-final). |
| Análise estatística e testes de hipóteses | Planejado; protocolo confirmatório pendente | Definir hipótese, efeito relevante, unidade de inferência e dependência temporal/por entidade antes de novas comparações. Obter mais evidência temporal separada do desenvolvimento; registrar tamanho de efeito e incerteza apropriada. A exclusão de um dia da ablação é sensibilidade, não intervalo de confiança. |
| Contrato de inferência e Docker | Planejado | Definir entradas, saídas, versão e estado das features; conferir paridade com scores offline. Empacotar recursos de `references/`, executar fora do checkout e demonstrar health check, erros e persistência. |
| Orquestração com Prefect | Planejado; escolha do projeto | Prefect auto-hospedado para encadear os módulos Python, registrar dependências e falhas, testar retries e retomada sem duplicação. Manter lógica independente do orquestrador e medir recursos. O consumidor de streaming terá contrato próprio. |
| Armazenamento de objetos | Planejado; backend a decidir | Separar dados e artefatos do container, preservar manifestos e demonstrar recuperação. Escolher S3 compatível, como RustFS, **ou** armazenamento Azure conforme o cenário; validar acessos e custo. Parquet/DuckDB continuam adequados à etapa local. |
| Streaming por replay histórico e paridade de features | Planejado | Reproduzir eventos em ordem temporal com relógio explícito, feedback após o atraso e estado causal. Definir duplicidade, empates, atrasos e recuperação; comparar features/scores offline e online. O replay será identificado como simulação. |
| Gestão de features e eventual feature store | Capacidade planejada; ferramenta condicional | Compartilhar definição/versionamento das features, manter estado e demonstrar paridade offline/online e disponibilidade no instante da decisão. Adotar um serviço de feature store apenas se resolver compartilhamento, latência ou governança que a solução simples não atender. |
| Monitoramento e dashboards | Planejado | Prometheus/Grafana para serviço, latência, erros e atraso do processamento; pipeline para qualidade, drift e desempenho quando os rótulos chegarem. Painel de investigação com orçamento, acertos e fraudes perdidas. Demonstrar alertas e recuperação em falhas controladas. |
| CD e governança de promoção | Planejado | Entregar imagem e modelo identificados, executar smoke tests e gates de dados/modelo/serviço, controlar a promoção e demonstrar rollback. Registry/aliases MLflow entram com o fluxo de release; o gate exploratório atual não autoriza deployment. |
| CT: treinamento contínuo controlado | Planejado | Definir gatilhos, disponibilidade dos rótulos, orçamento e avaliação temporal do candidato. Orquestrar treinamento, comparação e decisão auditável; manter a referência quando os gates falharem. Drift isolado não basta para promover modelo. |
| Kubernetes local com kind | Condicional | Adotar quando o serviço e o replay estiverem estáveis e houver cenário de implantação a demonstrar. Comprovar persistência, probes, recuperação e rollback; medir custo de recursos. |
| Terraform e implantação em cloud | Condicional | Selecionar um ambiente e orçamento, provisionar recursos realmente necessários, conferir recriação e destruição controlada. Evitar manter duas clouds ou duplicar a responsabilidade dos manifests de aplicação. |
| Hiperparâmetros, novos ensembles e redes neurais | Condicional | Abrir protocolo novo apenas com hipótese e ganho esperado relevante; limitar fits/tempo e comparar com a referência sob a mesma política. HGB já é um ensemble de árvores. A complexidade adicional deve justificar desempenho e custo operacional. |

## Próxima entrega concreta

1. Aplicar, validar e versionar o executor da avaliação final. O recibo de
   congelamento e os critérios já estão no Git e serão conservados.
2. Executar e verificar a avaliação; revisar resultados diários, decisão e Model Card.
   O gate exige AP ≥ 0,50 e precisão diária @100 ≥ 0,45, sem promoção em produção.
   A execução real permanece pendente; não há resultado de teste nesta preparação.
3. Documentar a evidência real e definir o contrato de inferência e Docker.
   A política de alertas online
   terá contrato e validação próprios, pois hoje usamos o dia completo retrospectivamente.

Uma nova decisão de modelagem antes do congelamento exige protocolo próprio e
atualização deste mural. Após consultar o teste final, ele deixa de ser um holdout
intocado; novas escolhas precisam de outra avaliação temporal definida previamente.

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

- [Ablações e checks locais: execução informada em 2026-10-08](../references/evidence/controlled_ablation_execution_2026-10-08.json).
- [Protocolo temporal](EVALUATION_PROTOCOL.md): dados, rótulos e métricas.
- [Stakeholders](STAKEHOLDERS.md): interpretação e utilidade dos resultados.
- [Google Cloud: MLOps, CI/CD/CT e operação de sistemas de ML](https://docs.cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning).
- [scikit-learn: HistGradientBoostingClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html).

O mural é uma decisão de escopo deste projeto, informado pelas referências.
Não é uma certificação de maturidade nem uma promessa de eficácia antifraude real.
