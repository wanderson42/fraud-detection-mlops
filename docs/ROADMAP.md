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
| Análise estatística e testes de hipóteses | Planejado; protocolo confirmatório pendente | Definir hipótese, efeito relevante, unidade de inferência e dependência temporal/por entidade antes de novas comparações. Obter mais evidência temporal separada do desenvolvimento; registrar tamanho de efeito e incerteza apropriada. A exclusão de um dia da ablação é sensibilidade, não intervalo de confiança. |
| Contrato de inferência e Docker | Contrato inicial definido; implementação próxima | Receber 19 features calculadas, devolver score identificado e rejeitar entradas inválidas. [Contrato proposto](ARCHITECTURE.md#primeiro-contrato-de-inferência--definido-implementação-pendente). Demonstrar paridade, execução fora do checkout, health/readiness, imagem mensurada e recuperação. |
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

1. Consolidar no Git a avaliação e a revisão do CSV diário; conferir localmente
   `model_card.md` da execução original. Não é necessário repetir o teste ou treinar.
2. Implementar o primeiro contrato de pontuação e conferir paridade por round-trip
   de serialização usando scores já salvos, sem qualquer escolha de modelagem.
3. Preparar um artefato de serving identificado e somente leitura; demonstrar carga
   fora do checkout, `/health`, `/ready` e falhas de contrato antes do Docker.
4. Construir e medir a imagem: dependências de runtime explícitas, usuário sem root,
   ausência de dados/credenciais na imagem, recursos e latência observados. A política
   de investigação online e o cálculo de históricos terão validação própria.

A revisão diária foi concluída sobre o CSV fornecido; a entrega técnica de
serving precisará de evidência própria.
Mudanças futuras de modelo exigem outro protocolo e outra janela previamente
preservada; este teste final já foi consumido.

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

O mural é uma decisão de escopo deste projeto, informado pelas referências.
Não é uma certificação de maturidade nem uma promessa de eficácia antifraude real.
