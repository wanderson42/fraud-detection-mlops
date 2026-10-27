# Histórico do roadmap até a integração do PR #11

Registro histórico preservado. Expressões como "próximo", "atual" e "pendente"
no texto abaixo se referem ao incremento em que foram escritas. O estado
corrente e as prioridades estão em [ROADMAP.md](ROADMAP.md); os comandos diários
estão em [PRODUCT_OPERATIONS.md](../operations/PRODUCT_OPERATIONS.md).

---

# Mural de metas — Fraud Detection MLOps

<!-- kind-operations-acceptance-2026-10-21:start -->
**Estado em 2026-10-21 — marco KinD/Helm concluído no escopo do laboratório.**
Parada/subida e reboot planejado aprovados no mesmo node e volumes, com inotify
persistente e leituras de tracking, serving e monitoramento conferidas.
[Sequência integrada, aceites e limites](../operations/KIND_PLATFORM.md). Próximo: simplificação
e fechamento funcional do portfólio; Terraform permanece posterior. Notas
datadas abaixo preservam o estado de cada incremento, sem reabrir gates aceitos.
<!-- kind-operations-acceptance-2026-10-21:end -->


Atualizado em **2026-10-21**. Backlog principal, em ordem de dependência e valor
para a demonstração. O [README](../../README.md) apresenta o projeto; a
[arquitetura](../ARCHITECTURE.md) descreve os componentes e os
[stakeholders](STAKEHOLDERS.md) interpretam os resultados.

## O que queremos demonstrar

Priorizar investigação com dados e features causais, modelo identificado,
feedback atrasado, operação observável, recuperação e atualização controlada.
O diferencial de portfólio é conseguir explicar uma decisão e demonstrar o que
acontece quando o processo para, os dados mudam ou um candidato é rejeitado.
O painel torna essas evidências visíveis. Dados simulados e laboratório local
não comprovam eficácia antifraude real nem SLA de uma instituição.

## Próximo marco — simplificação e fechamento funcional

A [rodada operacional canônica](../operations/OPERATIONAL_ROUND.md) foi aceita pelo
autor: dados recompostos, três candidatos com Inputs, HGB versão 1 em champion,
API com os mesmos pesos e rollback verificado. A implementação de
[substituição recorrente](../operations/MODEL_UPDATES.md) inclui busca opcional por
modelo e aprovação explícita da mudança de tráfego. Em 2026-10-22, as rodadas
fixa e de busca validaram a retenção nativa do champion; ativação, rollback e
recuperação de um sucessor com pesos diferentes no KinD continuam pendentes.

O marco de infraestrutura KinD + Helm está concluído **no escopo de laboratório**:
componentes aceitos por incrementos, parada/subida e reboot planejado aprovados,
mesmo node/PVCs e leituras funcionais conferidas. A [sequência integrada](../operations/KIND_PLATFORM.md)
consolida os comandos e os limites. Isso não declara migração primária, promoção
histórica, recuperação por perda do host ou uma nova execução monolítica do fluxo.

A [jornada de entrega ao usuário](../operations/PRODUCT_DELIVERY.md) conecta
eventos e features causais à API do champion, persiste scores/decisões,
prioriza investigação e associa feedback. O ensaio nativo passou em
2026-10-22, com pausa/retomada, reconciliação e consulta de casos. A
[manutenção idempotente da release](../operations/SERVING_RECOVERY.md) também foi
aplicada e verificada no KinD, mantendo modelo, release e casos consultáveis.
A [integração SHAP por caso](../operations/CASE_EXPLANATIONS.md) foi aceita pelo
autor em 2026-10-22: cinco casos do champion, 19 contribuições por caso,
reconstrução do score e verificação offline do relatório, preservando o modelo.

O [portal de investigação](../operations/FRAUD_PORTAL.md) e sua
[página de desempenho](../operations/PORTAL_PERFORMANCE.md) foram aceitos
funcional e visualmente em 2026-10-22: 200 casos consultáveis e 67.255 transações
de validação identificadas, com comparação, curvas e métricas da política.
O portal é independente do algoritmo; explicações são capacidades opcionais.
Esse recorte está encerrado.

A [primeira simplificação do código](../operations/CODEBASE_SIMPLIFICATION.md)
organiza o Make por finalidade, consolida o acesso ao deployment ativo e retira
uma revisão pontual de plano histórico já aplicada. Regressão e acesso nativo ao champion foram aprovados pelo autor em 2026-10-22.
Os [acessos locais em segundo plano](../operations/LOCAL_ACCESS.md) acrescentam
nohup, logs privados, identidade de processo e parada por serviço.
Isso não encerra a chegada gradual nem o aceite de um sucessor com novos pesos.

A [revisão de todo o checkout](../operations/CODEBASE_SIMPLIFICATION.md#varredura-do-checkout)
retira o importador histórico isolado e seus testes exclusivos, consolida os
smokes de CLI e registra as dependências dos diretórios mantidos. A simplificação
mais ampla dos controladores com pins continua uma tarefa explícita; não é
confundida com a entrega de novos atalhos de operação.

**Recorte em andamento: ampliação de janela/volume da sessão e paginação da fila.**
Treinadores compartilham protocolo/requisição e as consultas CRI têm implementação
única. README e catálogo ficam separados da identidade executável. Recibos antigos
permanecem íntegros, com transições de hashes revisadas e commitadas; a instalação
sintética foi retirada com readbacks nativos preservados, e seu controlador
exclusivo foi removido após revisar consumidores.
A consolidação do ciclo de vida agora prepara baseline v2 do champion ativo,
reutiliza o gate da API e restaura réplicas salvas, inclusive zero no slot inativo.
Parada/subida canônica aceita pelo autor em 2026-10-23: 1 teste em 276,71 s,
mesmo node/volumes, plano de réplicas restaurado e proxy Grafana após reinício.
Não altera o estado da promoção recorrente de sucessor, que continua condicionado
a candidato qualificado e validação própria. [Procedimento](../operations/KIND_OPERATIONS.md).

O [balanço da varredura](../operations/CODEBASE_SIMPLIFICATION.md#varredura-do-checkout)
registra a retirada do importador, a consolidação dos testes e o compartilhamento
de entrada de treino/consulta CRI. A compatibilidade dos pins tem transição exata
revisada; a família nativa sintética foi aposentada, preservando CI e helpers
compartilhados. A
[consolidação da demonstração operacional](../operations/PRODUCT_OPERATIONS.md)
continua como entrega seguinte, sem encerrar a limpeza por antecipação.
Unificar comandos/destino ativo e demonstrar chegada gradual de eventos pela API,
fila e métricas da mesma sessão, com pausa/retomada e atualização visível.
O portal de evidências usa snapshot; a tela operacional acompanha a sessão corrente. O monitoramento KinD aceito ainda usa cópia concluída.
Não abrir novos estudos de modelo, broker, feature store ou CT para esse recorte.
A simplificação e revisão de redundâncias fazem parte dessa consolidação.

A simplificação consolidará comandos/responsabilidades, removerá componentes
substituídos após revisar dependências e validará instalação limpa e demonstração.
O guia principal será orientado ao produto, com uma seção própria para evidências.
A ativação nativa de um sucessor com pesos diferentes permanece como aceite
específico, sem tornar obrigatório buscar um vencedor artificialmente.
Terraform vem depois desse fechamento.

## Marcos concluídos e evidência preservada

Uma linha concluída encerra somente o escopo demonstrado. As extensões ainda
necessárias estão separadas no backlog; não reabrimos os ensaios já aprovados.

| Marco | Estado e limite |
| --- | --- |
| Portal e transparência do modelo | Aceite nativo e visual em 2026-10-22: 200 decisões/13 alertas/cinco explicações; avaliação de 67.255 transações, comparação, curvas e recall/falsos positivos com unidade/política explícitas. Mesmo modelo, champion preservado. Consulta por snapshot; sessão contínua é próxima consolidação. [Evidência](../../references/evidence/portal_performance_author_validation_2026-10-22.json) |
| Explicações SHAP dos casos do champion | Aceite nativo em 2026-10-22: cinco casos identificados pelos mesmos pesos/release/requests, 19 contribuições por caso, score reconstruído, HTML/SVG/JSON publicados e verificação offline sem carregar modelo. Champion preservado. [Evidência](../../references/evidence/case_explanations_author_validation_2026-10-22.json) |
| Retomada idempotente do instalador do champion | Aceite nativo em 2026-10-22: Job com reinício no mesmo volume, manutenção aprovada da portadora, API/Registry versão 1 no blue e cinco casos consultados após a troca. Mesmo modelo e release; sem novo treino. [Evidência](../../references/evidence/serving_seed_recovery_author_validation_2026-10-22.json) |
| Eventos causais pela API do champion | Aceite do autor em 2026-10-22: teste nativo aprovado, decisões HTTP e feedback consultáveis, pausa/retomada e reinvocação conferidas pelo teste. Não altera champion nem conclui SHAP por caso. [Evidência](../../references/evidence/product_http_flow_author_validation_2026-10-22.json) |
| Retenção governada do champion | Aceite nativo em 2026-10-22: duas rodadas, três candidatos por rodada, Inputs/artefatos/validação conferidos, ganho insuficiente e champion versão 1 preservado. Não conclui ativação ou rollback de sucessor com pesos diferentes. [Evidência](../../references/evidence/model_updates_author_validation_2026-10-22.json) |
| Infraestrutura do fluxo em KinD + Helm | Concluída no escopo do laboratório por aceites incrementais de fundação, tracking, datasets, Jobs, replay, serving, observabilidade e operação. Sequência consolidada; fontes históricas preservadas. Não é migração primária nem execução integral nova em produção. [Guia integrado](../operations/KIND_PLATFORM.md) |
| Ciclo de vida do champion ativo | Aceite nativo em 2026-10-23: baseline v2, 11 controllers retomados, green em zero, mesma identidade Registry/HTTP e volumes, oito regras/quatro dashboards e proxy Grafana aprovados após reinício; quatro acessos principais reabertos. [Evidência](../../references/evidence/canonical_lifecycle_author_validation_2026-10-23.json) |
| Parada/subida e reboot planejado | Aceite nativo em 2026-10-21: oito workloads retomados no mesmo node/PVCs, boot_id mudou e inotify 512/524288 persistiu. Tracking/S3, serving e monitoramento iguais ao baseline. Sem recuperação por perda do host ou interrupção abrupta. [Evidência](../../references/evidence/kind_operations_author_validation_2026-10-21.json) |
| Política de cores dos dashboards | Aceite separado em 2026-10-21: cores, mappings, thresholds e legendas conferidos via API e visualmente pelo autor; métricas e três PVCs iguais ao aceite anterior. Informações não herdam classificação arbitrária por magnitude. Sem replay ou novo resultado de qualidade. [Evidência](../../references/evidence/dashboard_color_policy_author_validation_2026-10-21.json) |
| Observabilidade no KinD | Aceite nativo e visual em 2026-10-21: cópia de replay concluído conciliada, scrape, oito regras, proxy Grafana e quatro dashboards conferidos; exporter recriado no mesmo PVC, origem e demais recursos preservados. Sem ingestão ao vivo, novo resultado de qualidade, firing ou reboot validados. [Evidência](../../references/evidence/kind_observability_author_validation_2026-10-21.json) |
| Serving via Helm no KinD | Aceite nativo em 2026-10-21: seis eventos blue → green → blue, pacote inválido bloqueado, EndpointSlices conferidos, falha 502 e rollback Helm em 1,99 s observado. Mesma imagem qualificada/pesos sintéticos, baseline/gateway e fundação preservados. Baseline instalada, candidata removida; sem promoção histórica ou garantia de zero downtime. [Evidência](../../references/evidence/kind_serving_author_validation_2026-10-21.json) |
| Replay sintético com checkpoint no KinD | Aceite nativo em 2026-10-20: pod recriado durante transação de feedback, rollback e retomada no mesmo PVC; todas as tabelas iguais à execução contínua, reentrega sem rescoring. 20 eventos/10 scores/6 alertas; processamento removido e PVC retido. Sem migração histórica ou recuperação por perda do host. [Evidência](../../references/evidence/kind_replay_author_validation_2026-10-20.json) |
| Produtores Bronze → Silver → Gold em Jobs Helm | Aceite nativo em 2026-10-20: Silver 1.754.155 transações; Gold 402.877 linhas/19 features; ligação exata dos novos bundles, restores independentes, fundação preservada e Jobs removidos. Gold recalculou features, sem treino, nova avaliação ou mudança primária. [Evidência](../../references/evidence/kind_pipeline_author_validation_2026-10-20.json) |
| Bundles dos quatro datasets no RustFS do KinD | Aceite nativo em 2026-10-20: 489 arquivos/493 objetos verificados, 210.997.363 bytes; reenvio sem novos uploads e restauração com verificadores próprios, fundação preservada. Leitores atuais mantidos; sem recuperação por perda do cluster/host. [Evidência](../../references/evidence/kind_datasets_author_validation_2026-10-20.json) |
| MLflow na fundação KinD/Helm | Publicação e recarga independente HTTP, hash S3 e recarga após recriação do pod aprovados; erro zero, mesmo Deployment/imagem e fundação preservada. Fixture sintética; sem migração histórica ou backup/restore do novo destino. [Evidência](../../references/evidence/kind_tracking_author_validation_2026-10-20.json) |
| Fundação PostgreSQL/RustFS no KinD | Aceite nativo em 2026-10-20: dois pods recriados, mesmos PVCs/PVs e marcadores SQL/S3 relidos; 45 testes aprovados. Sem backup/restore ou migração primária. [Evidência](../../references/evidence/kind_foundation_author_validation_2026-10-20.json) |
| Bronze, Silver e Gold | 183 dias; 1.754.155 transações; 19 preditores causais; contratos, manifestos e verificadores |
| Referência e experimentos | HGB congelado, três ablações e 20 trials/63 fits; nenhum candidato elegível |
| Avaliação temporal | Maio: AP 0,640703; referência de 02–15/09: AP 0,621312; reservas consumidas |
| Estatística exploratória | Dependência e reamostragem documentadas; sem intervalo de generalização ou superioridade formal |
| Tracking e serving | MLflow/skops, HTTP/wheel e Docker validados pelo autor; imagem e release identificadas |
| Política e estado causal | Primeiro cruzamento do limiar 0,8826493808370081; até 100 clientes/dia; feedback separado sete dias depois |
| Replay nativo e correção | 556.824 eventos/143.945 scores; 19 features exatas e gate de scores aprovado; 1.453 alertas preservados |
| Resultado descritivo da fila | 768 capturas/358 perdas de clientes-dia fraudulentos em 16–30/09; precisão 52,86% e recall 68,21% agregados |
| Replay programado nativo | 556.824 eventos em 14 sessões a 8.640×; 143.945 decisões medidas, zero lacunas e paridade independente aprovada pelo autor; não avalia SLA |
| Visibilidade local inicial | Stack Docker ativa, scrape Prometheus e valores dos dois dashboards confirmados pelo autor no smoke; o fechamento nativo está registrado na linha seguinte |
| Coleta nativa e recuperação do exporter | Resumo/Prometheus conferidos no volume final; alerta observado em 56,36 s e retorno em 12,29 s, arquivos preservados; rotina incorporada com pytest/tox |
| Arquivamento e recuperação S3 dos datasets | Bronze, Silver, Gold e dataset de desenvolvimento publicados, reenviados sem novos uploads e restaurados com verificações próprias. Não declara S3 como origem primária dos leitores. [Gates e evidências](../operations/STORAGE.md) |
| Backup e recuperação do estudo HGB concluído | Snapshot consistente SQLite + artefatos; 322 arquivos, 20 trials/63 tentativas e decisão `retain_reference` preservados; ciclo S3 aprovado. Sem retomada de treino ou conversão do estudo para PostgreSQL. [Evidência](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json) |
| Backup e recuperação do tracking histórico | Dois stores: 71 runs e 67 modelos; snapshot/S3 e acesso ao histórico pelo SDK aprovados; referência recarregada com erro zero. Sem migração primária. [Runbook](../operations/MLFLOW.md) |
| Inventário do checkout e contenção inicial dos destinos | Coleta/revisão do checkout e quatro CLIs aprovadas; consumidores revisados em 17 registros, com sete fronteiras locais mapeadas. Não encerra cobertura global de writers, volumes ou arquivos externos. [Inventário](../../references/evidence/persistence_inventory_author_validation_2026-10-16.json) e [consumidores](../../references/evidence/tracking_consumers_author_validation_2026-10-19.json) |
| Tracking PostgreSQL/S3 e produtores HTTP | Persistência após recriação dos containers; contribuição, fold HGB e trio dummy/logística/HGB com recarga independente e paridade aprovados. Rotas explícitas; histórico preservado e busca Optuna completa não repetida. [Operação](../operations/MLFLOW.md) |
| Recuperação PostgreSQL/S3 do tracking | Captura e restore isolado aprovados em 2026-10-19: 38 objetos, 8 runs, 6 modelos e 44 pontos históricos; recarga pelo proxy com erro zero; fonte preservada e alvo removido. Não comprova recuperação após perda da máquina. [Evidência](../../references/evidence/tracking_restore_author_validation_2026-10-19.json) |
| Troca blue-green e rollback no Docker | A → B → A na mesma URL; pacote inválido bloqueado, falha 502 e retorno verificado em 3,10 s; pesos/imagem preservados. Ensaio isolado, sem promoção de novo modelo ou garantia de zero downtime. [Evidência](../../references/evidence/serving_rollout_author_validation_2026-10-19.json) |
| Entrega versionada e consumo da imagem | Build único, qualificação sintética, exportação e recarga nativos aprovados; entrega original preservada e recursos próprios removidos. [Produção](../../references/evidence/serving_delivery_author_validation_2026-10-19.json) e [consumo](../../references/evidence/serving_consumer_author_validation_2026-10-19.json) |
| CI de entrega entre runners | `quality` → `serving-delivery` → `serving-consumer` aprovados na execução 39306262564, commit `6a3782b`; mesma imagem transferida, recarregada e conferida por HTTP em outro runner, sem rebuild/fit no consumidor. Sem registry ou deploy permanente. [Evidência](../../references/evidence/serving_actions_validation_2026-10-19.json) |
| Monitoramento mínimo de dados e features | Snapshot nativo, exporter, oito regras, coleta Prometheus e dashboard conciliados; duas janelas de 1.000 decisões, sem problemas de qualidade/relógio e quatro divergências abaixo de 0,1. Sem CT, concept drift comprovado ou nova avaliação do modelo. [Evidência](../../references/evidence/feature_monitoring_author_validation_2026-10-19.json) |

O replay corretivo e os gates nativos/CI acima não são execuções futuras.
Recibos, protocolos, checkpoints e resultados históricos permanecem preservados.
Contagens de testes e tempos pertencem à execução registrada; não são SLA nem
aceite automático de outro commit, ambiente ou modelo.

## Backlog ordenado

O compromisso principal agora fica limitado a **um marco restante**. Terraform é o
**desfecho posterior**, conforme desejo do autor. As ferramentas em análise
abaixo não se tornam entregas obrigatórias por aparecerem neste documento.

| Ordem | Marco | Critério de conclusão |
| --- | --- | --- |
| 1 | Simplificação e fechamento funcional do portfolio | Entregar eventos → features causais → API do champion → decisões/fila → feedback com reconciliação e retomada; reaproveitar SHAP em casos do modelo ativo; consolidar comandos e remover redundâncias após revisar dependências. Demonstrar instalação limpa e jornada de investigação. [Contrato e sequência](../operations/PRODUCT_DELIVERY.md) |
| 2 — posterior | Terraform como desfecho de infraestrutura como código | Após a simplificação, gerenciar o ambiente estabilizado com configuração declarativa, versões/lock de providers e state protegido. Reutilizar o chart Helm; demonstrar apply real no alvo escolhido, segundo plan sem mudanças e recuperação/recriação documentadas. Delimitar bootstrap do KinD e ownership dos recursos; não exigir cloud paga |

A conferência visual do dashboard de features foi concluída usando o estado
nativo já aprovado, sem repetir a janela. A cor neutra da contagem é um ajuste
de apresentação posterior, sem alterar o aceite funcional. O ensaio 1× só será
executado com pergunta, protocolo e orçamento explícitos; a ausência desse
ensaio continuará documentada, sem bloquear o fechamento funcional.

A revisão dos destinos e a limpeza pertencem à simplificação. Não significam migrar
indiscriminadamente todo SQLite ou inventariar todo o Linux. Os sete consumidores
históricos mapeados exigem decisão explícita de suporte/preservação; não serão
adaptados apenas para eliminar uma ferramenta do diagrama. PVCs, quando houver
serviços com estado, precisam de testes de persistência e backup/restore.

## Análise de adoção: CT, orquestração, feature store e broker

A análise é planejada após validar o monitoramento e estabilizar a infraestrutura
provisionada, antes de decidir
se alguma extensão entra no escopo da simplificação. Concluir essa análise é
registrar uma decisão fundamentada; não exige instalar todas as opções. Se uma
extensão for aprovada, ganha recorte, orçamento e aceite próprios antes de Terraform. Terraform permanece posterior à simplificação do conjunto então escolhido.

| Possibilidade | Benefício que precisa demonstrar | Decisão atual |
| --- | --- | --- |
| CT automático com orquestração | Ciclo repetível de dados/labels elegíveis → validação → treino → avaliação temporal → candidato aceito/rejeitado, com gatilho, identidade, orçamento, exclusão mútua, retomada e recibos | Analisar um fluxo pequeno e limitado. Treino automático e promoção automática são decisões separadas; manter promoção explícita inicialmente. Não reexecutar períodos já consumidos como teste estatístico novo |
| Plataforma de orquestração | Dependências, retries, recuperação e estados que se tornem difíceis de manter no fluxo existente | CT exige coordenação das etapas, mas não implica adotar Prefect/Airflow/Kubeflow. Comparar primeiro o executor atual e workflows/jobs existentes; adicionar serviço apenas se reduzir complexidade total |
| Feature store | Reutilização concreta de features entre treino e serving/consumidores, recuperação histórica e leitura online com frescor/paridade demonstrados | Desejo do autor mantido como spike com decisão de adoção. Avaliar um pequeno grupo de features de cliente/terminal contra a solução atual, sem substituir as 19 features nem mudar sua semântica para acomodar a ferramenta |
| Broker externo | Desacoplar produtores/consumidores ou permitir reentrega, múltiplos consumidores e recuperação que a fila local não atende | Adiar enquanto não houver esse requisito demonstrado. Não é dependência automática de CT, de um orquestrador ou de uma feature store |

### CT e orquestração: proposta para a análise

O primeiro passo será explicitar de onde vêm dados adicionais e labels já
amadurecidos. No problema atual, feedback aparece sete dias depois; um evento
novo não é automaticamente um exemplo rotulado elegível. Mudança de distribuição
é sinal de investigação, não prova de que retreinar melhora o modelo.

Usar o pipeline/modelos existentes e um orçamento limitado; evitar começar por
outra busca Optuna. Preservar identidades e a referência congelada. Um ensaio
sintético pode provar agendamento, falhas/retries, rejeição e recibos; deve ser
identificado como demonstração operacional, não como melhoria estatística.
Sem dados adicionais elegíveis e protocolo novo, não alegar CT produtivo contínuo.

O fluxo deve provar também que repetir o gatilho não duplica a mesma execução e
que falhas não promovem candidato incompleto. O resultado pode ser manter a
referência. Escolher o mecanismo de execução depois desse contrato; o servidor
do orquestrador é uma opção de implementação, não o objetivo do marco.

### Feature store: decidir com um spike útil

Começar por identificar uma leitura histórica e uma leitura online que realmente
reutilizem as mesmas features. Comparar com a implementação atual: duplicação de
lógica evitada, latência, frescor, RAM, disco e esforço de recuperação/operação.
Testar dados atrasados, backfills e a diferença entre instante do evento e
instante em que o valor/feedback ficou disponível. O estado causal atual e seus
verificadores continuam sendo a referência da comparação.

Feast é um candidato, não uma escolha já aprovada. A documentação distingue
join por event timestamp de filtragem pela disponibilidade/created timestamp;
o comportamento depende da configuração e do offline store. O spike precisa
fixar uma versão compatível e testar essas condições, sem assumir que instalar
uma feature store elimina vazamento temporal. Adotar somente se houver ganho
mensurável com custo aceitável. Caso contrário, documentar a decisão de manter
a gestão de features existente.

### Terraform: etapa final após a simplificação

O chart Helm continua responsável pelos manifests da aplicação. A futura camada
Terraform poderá gerenciar releases Helm e recursos de infraestrutura com
ownership claro, sem dois controladores disputando os mesmos objetos. Definir
se o KinD é bootstrap separado ou recurso gerenciado; não tratar um
`local-exec` que roda scripts como prova suficiente de IaC reproduzível.

Preferir um alvo local para este portfolio, sem custo de cloud obrigatório.
O aceite exige provisionamento real, um segundo plan sem mudanças, state e
credenciais fora do Git, e política explícita para preservar/restaurar dados
persistentes. Destroy/recriação será exercitado em ambiente descartável; a
retenção dos dados precisa ser deliberada, não um efeito colateral.

Referências técnicas consultadas em 2026-10-19:
[MLOps e CT](https://docs.cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning),
[Feast: point-in-time e disponibilidade](https://docs.feast.dev/getting-started/concepts/point-in-time-joins),
[Terraform e Helm](https://developer.hashicorp.com/terraform/tutorials/kubernetes/helm-provider),
[state e dados sensíveis](https://developer.hashicorp.com/terraform/language/manage-sensitive-data).

## Registros dos incrementos e critérios de extensão

As seções abaixo preservam a evolução e a justificativa dos ensaios. Referências
a próximos passos de um incremento devem ser lidas em sua sequência histórica;
o estado consolidado, o compromisso de escopo e a prioridade atuais são os das
tabelas de marcos/backlog e da análise de adoção acima. Desejos de expansão
nesses registros não acrescentam obrigações ao backlog principal.

## Monitoramento local — marco exercitado

O autor concluiu o ensaio nativo: 556.824 eventos, 143.945 decisões e 1.453 alertas,
em 14 sessões, com zero commits sem medição. A verificação confirmou features,
scores e política; a qualidade repetiu a referência já consultada.
[Evidência e limites](../../references/evidence/paced_native_author_validation_2026-10-15.json).
O p95 de processamento foi 23,71 ms, enquanto agendado→commit chegou a 379,57 s
no p95 sob aceleração 8.640×. Não somar quantis das etapas nem interpretar o
processamento como inferência isolada. Os máximos e a ausência de ensaio contínuo
1x permanecem registrados no runbook.

O autor confirmou coleta válida e os 556.824 eventos no Prometheus; o teste do
exporter retornou alerta em 56,36 s, recuperação em 12,29 s e arquivos inalterados.
[Evidência](../../references/evidence/exporter_recovery_author_validation_2026-10-15.json).
O utilitário foi incorporado fora dos scripts identificados do replay, com testes
de controle no tox padrão e execução real explícita. Não é necessária nova pontuação.

[Runbook de observabilidade](../operations/OBSERVABILITY.md) e
[protocolo](../../references/paced_monitoring_protocol_v1.json) concentram execução,
identidades, métricas e limites. A fila limitada fica entre produtor programado e
consumidor durável. O contexto anterior ao início da janela é aquecido sem pacing
e excluído das latências. Cada retomada tem uma nova âncora; lacunas de tempo
após commit são explícitas, sem fabricar amostras de latência.

O exporter lê somente o estado do novo ensaio. Não reabre Silver, não pontua
novamente e não consulta o oráculo. Qualidade usa dias completos com feedback
amadurecido; AP é transacional, precisão/recall da fila são por cliente/data.
Jensen–Shannon dos scores é um sinal descritivo com suporte, sem CT automático.
A observabilidade de features completas e o monitoramento de recursos do host
continuam com evidência própria; os custos do replay são coletados por invocação.

Velocidade > 1 é aceleração. O alvo local de p95 agendado→commit ≤ 1 s só pode
ser examinado em execução 1x contínua, completa, sem lacunas de tempo e no escopo
de fila local. Uma janela inteira em ritmo 1x dura semanas: comece pelo ensaio
acelerado, registre seu custo e planeje um recorte 1x sob protocolo antes de
alterar a janela. Não tratamos o p95 HTTP sequencial como latência do replay.

## Objetos S3 e custo inicial

O [plano de armazenamento](../operations/STORAGE.md) inclui desde Bronze/Silver/Gold
até tracking, estado, métricas e eventual feature store. DuckDB é o motor de
consulta; S3 guarda conjuntos publicados; bancos ativos exigem disco persistente
e backup consistente. PostgreSQL é o destino escolhido para o servidor MLflow,
com migração versionada/reconciliada própria. Inventário e piloto S3 limitados
passaram testes locais e o autor validou o round-trip de dois documentos no RustFS.
Os dez arquivos foram interpretados e a release completa foi restaurada e
recarregada: identidade preservada e erro zero no score histórico. Agora ampliar
com critérios específicos por camada; o limite de 64 arquivos/64 MiB pertence ao
piloto e não cobre todos os conjuntos nativos. A ampliação precisa ter orçamento
explícito e preservar manifestos/bytes, sem fragmentação manual só para contornar o teto.
O preparo Bronze tem perfil separado e verificação com inventário arquivado;
o autor aprovou 183 partições, 188 arquivos e 107.309.243 bytes. A transferência
para `fraud-datasets` já usa um job por comando e um cliente S3 por job, com
manifesto por último, reenvio idempotente e restore submetido ao verificador
Bronze. O autor aprovou o build e as três operações: 189 objetos publicados,
zero uploads/189 reutilizados e 183 partições restauradas/verificadas.
[Evidência](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
Os gates de arquivamento/recuperação Bronze e Silver estão fechados. O autor
aprovou o preparo e round-trip Silver: 187 arquivos/40.288.931 bytes, contrato,
inventário e auditoria, 188 objetos e reenvio sem uploads. A cópia passou schema
e SQL: 183 partições/1.754.155 linhas e IDs distintos, com contagens históricas
reconciliadas. [Evidência Silver](../../references/evidence/silver_archive_author_validation_2026-10-15.json).
O inventário Gold foi recebido: 506 arquivos/326.821.198 bytes em datasets,
estudos e resultados. O primeiro perfil cobre `gold_v1` (43 arquivos no snapshot,
mais auditorias próprias e quatro referências), pois já tem verificador específico.
O autor aprovou preparo, publicação/reenvio/restore Gold no RustFS: 48 arquivos,
25.196.448 bytes, 49 objetos e zero uploads no reenvio. `verify_gold` sobre a cópia
com quatro referências baixadas aprovou 42 partições/19 features/402.877 linhas
(268.668 treino, 67.255 validação, 66.954 teste). Gate `gold_v1` fechado.
[Evidência nativa Gold](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
O inventário de desenvolvimento delimitou 64 arquivos/38.159.334 bytes de dataset
e 319 arquivos/232.943.522 bytes de estudo, com SQLite de 540.672 bytes. O perfil
do dataset foi validado pelo autor com inventário/protocolo HGB: 66 arquivos,
38.202.741 bytes, 67 objetos e reenvio sem uploads. O restore aprovou 63 partições,
19 features e 603.669 linhas sem ler originais ou estudo, preservando run ID e
hash do manifesto. Gate de arquivamento/recuperação do dataset fechado.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
O preparo offline do estudo foi implementado/testado: lock de escritor real,
SQLite Online Backup API e cópia coordenada de artefatos, com integridade/digest
lógico e verificador existente sobre visão descartável. Vínculos por run ID,
hash do manifesto e bundle do dataset; relatórios/URIs históricos preservados.
Preparo nativo aprovado: 322 arquivos/233.048.483 bytes, 20 trials concluídos,
63 tentativas e decisão `retain_reference` preservada.
[Evidência](../../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
A transferência agora usa o worker existente em `fraud-artifacts/hgb-study/v1/`,
com dependência explícita e verificador antes do destino final. O autor aprovou
publicação/reenvio/restore em 2026-10-16: 323 objetos, zero uploads no reenvio e
322 arquivos/233.048.483 bytes restaurados, preservando 20 trials, 63 tentativas,
hash lógico e vínculo com o dataset. Gate de recuperação do estudo fechado.
[Evidência nativa S3](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
Não há migração para PostgreSQL ou retomada de treino neste passo.
[Preparação e limites](../../references/evidence/hgb_study_backup_preparation_2026-10-15.json).
[Preparação](../../references/evidence/development_gold_archive_preparation_2026-10-15.json).
Adoção pelos leitores, cobertura dos demais modelos/avaliações,
tracking e seus backups continuam com gates próprios. O grupo de
desenvolvimento mistura dataset e estudo; o código Optuna usa SQLite, que exige
backup consistente. Não ampliar o teto só para copiar a família como um conjunto.
[Evidência de preparação](../../references/evidence/silver_archive_preparation_2026-10-15.json).
Incremento atual: inventário MLflow somente leitura implementado/testado, com
critérios de backup, transferência PostgreSQL + S3, IDs/URIs, recarga e rollback.
Coleta corrigida e revisão do Default concluídas pelo autor: zero referências
não resolvidas/localizações gerenciadas ausentes; Default sem runs/modelos ou
diretório. Gate de inventário fechado; preparo e verificação de backup offline banco + artefatos
implementados/testados, com preparo/verificação nativos aprovados e publicação, reenvio idempotente e recuperação S3 aprovados pelo autor.
[Procedimento e gates](../operations/MLFLOW.md#inventário-antes-da-migração-postgresql--s3).
Não é necessário migrar o SQLite do estudo concluído para avançar.
Inventário e restauração precedem a adoção de S3 como cópia canônica; nenhuma
camada está declarada migrada nesta entrega.

KinD + Helm é a meta final de provisionamento reproduzível, conforme decisão do
autor. Compose permite medir e validar os componentes agora; a migração virá
depois dos critérios de persistência/restauração e rollback. Não implica
duplicar imediatamente toda a infraestrutura nem contratar nuvem.

**Decisão: armazenamento S3 compatível local, com RustFS como backend inicial.**
Não exige conta de nuvem. Prometheus, Grafana OSS e RustFS rodam em perfis locais;
RustFS é opcional; o piloto real de documentos passou, sem concluir migração ampla.
[Configuração](../../infra/observability/README.md). Imagens são fixadas por digest,
credenciais são geradas localmente e as portas publicadas ficam no loopback.

A meta de objetos inclui datasets/artefatos imutáveis, releases, manifests,
relatórios e backups consistentes de estado. SQLite ativo continua em disco
local: S3 não substitui seu sistema de arquivos, locking ou commit atômico.
Restaurar e conferir hashes é requisito; apenas iniciar o serviço não fecha o
marco. Um único disco/nó RustFS não fornece redundância ou proteção contra perda
da máquina. Outra cópia independente continua necessária para disaster recovery.

Sem despesa de serviço cloud nesta fase; CPU, RAM, disco, energia e manutenção
da máquina continuam sendo recursos consumidos. Medir tempo/CPU/RSS por
invocação e volumes em disco, limitar retenção do Prometheus a 48 h/512 MB e
usar somente os perfis necessários. Não adicionar serviços pagos como requisito.

## Quando uma feature store vale a pena

A gestão atual já inclui schema, 19 features causais, versão da regra numérica,
features offline e estado online com paridade independente. Uma feature store
pode ajudar quando treino e serving ou outros consumidores realmente reutilizarem
as mesmas features e houver ganho na materialização, recuperação histórica ou
gestão de frescor. O spike de adoção descrito acima, por exemplo com Feast local,
começa com esse caso e orçamento claros; não exige criar outro consumidor artificial.

| Pergunta do spike | Evidência exigida |
| --- | --- |
| Qual consumidor adicional reutiliza quais features? | Consulta e materialização comparadas com o fluxo atual, sem duplicar lógica |
| O join histórico respeita o instante de decisão? | Testes de point-in-time e disponibilidade, incluindo atrasos/backfills: feedback conhecido só sete dias depois; verificar também suporte/configuração do store para created timestamp |
| O online respeita frescor e atraso? | TTL, freshness, política de eventos atrasados, materialização, falha e recuperação explícitos |
| A semântica fica igual? | As 19 features e os scores mantêm a regra numérica e a paridade; não trocar a definição para acomodar a ferramenta |
| Qual custo e ganho operacional? | RAM, disco, processos, latência e esforço de manutenção medidos contra o estado SQLite atual |

Adoção depende do resultado. Não instalar uma feature store apenas para completar
uma lista de ferramentas. Não confundir bucket de Parquet com feature store online.

## Leitura e evidência

- [Stakeholders](STAKEHOLDERS.md), [Model Card](../modeling/MODEL_CARD.md) e [avaliação](../modeling/EVALUATION_PROTOCOL.md).
- [Replay corrigido](../operations/NUMERICAL_CORRECTIVE_REPLAY.md) e [monitoramento](../operations/OBSERVABILITY.md).
- [Prometheus: instrumentação e cardinalidade](https://prometheus.io/docs/practices/instrumentation/).
- [Grafana: provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
- [RustFS: implantação local](https://docs.rustfs.com/en/installation/container).
- [Feast: point-in-time joins](https://docs.feast.dev/getting-started/concepts/point-in-time-joins).

As prioridades são decisões deste projeto. Não representam certificação de
maturidade ou promessa de qualidade futura.

## Simplificação após maturação da infraestrutura

Critério de fechamento solicitado pelo autor: depois de persistência, recuperação
e rollback demonstrados, revisar scripts temporários, comandos redundantes,
dependências, testes e documentação substituídos. Remover o que não atende mais
a um fluxo suportado e consolidar interfaces por responsabilidade. Não criar
pastas permanentes de legados para evitar exclusões; o Git preserva o histórico.

Antes de excluir, verificar chamadas em código, CI, Makefile, notebooks e runbooks,
além dos vínculos dos verificadores com identidades históricas. Versionar adaptações
necessárias sem reescrever evidências antigas. Validar instalação limpa, execução
e recuperação dos fluxos atuais após a limpeza. Não há exclusões neste fechamento.


O preparo nativo detectou o segundo store documentado em
`data/tracking/interface-smoke`: 1 run concluída, 1 logged model, 6 arquivos/32.088
bytes, sem localizações gerenciadas ausentes. A correção inclui os dois stores
com bancos/WAL e artefatos conferidos independentemente e limites agregados,
preservando a separação do smoke sintético. Bancos desconhecidos continuam
bloqueados. Retentativa de preparo e verificação nativos aprovadas para os dois stores;
publicação, reenvio idempotente e recuperação S3 aprovados pelo autor. Migração,
recarga operacional e limpeza permanecem abertas. [Cobertura e comando](../operations/MLFLOW.md#cobertura-dos-dois-stores-mlflow--correção-de-2026-10-16).
[Registro do diagnóstico e preparação](../../references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).


O backup MLflow teve **preparo e verificação locais aprovados pelo autor**:
dois stores, 71 runs, 67 logged models, 633 artefatos; pacote de 640 arquivos e
256.354.588 bytes. O verificador conferiu os dois bancos e artefatos sem ler
originais. Tox nativo: 70 testes aprovados, um aviso, Ruff/formatação/lockfile
aprovados. [Registro nativo](../../references/evidence/mlflow_backup_author_validation_2026-10-16.json). A transferência S3 agora está
implementada/testada com o job existente, incluindo restauração em diretório
separado e nova verificação por store; publicação/reenvio/restore nativos aprovados.
A recuperação sobre cópia descartável foi validada pelo autor em 2026-10-16; o resultado local via SDK está no runbook MLflow.
[Procedimento](../operations/MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow).
O acesso local ao histórico e à referência pelo SDK foi validado depois desse
preparo. Migração PostgreSQL, adoção pelos consumidores e inventário/limpeza
globais mantêm critérios próprios. Código científico, contratos,
evidências e URIs históricas permanecem preservados.


**Ciclo S3 do snapshot MLflow aprovado pelo autor em 2026-10-16:** publicação com
641 objetos enviados/verificados, reenvio com zero uploads/641 objetos reutilizados
e restore em diretório separado com 640 arquivos/256.354.588 bytes. Os dois stores
mantiveram 71 runs, 67 logged models, 633 artefatos e os hashes SQLite/lógicos;
integridade/FKs/tabelas/URIs conferidas, sem leitura dos originais pelo verificador.
16 testes nativos passaram no tox. [Evidência nativa](../../references/evidence/mlflow_transfer_author_validation_2026-10-16.json).
Gate de acesso ao histórico e recarga do modelo concluído em cópia descartável,
com paths históricos mapeados somente nessa cópia e sem depender dos originais.
O autor validou esse uso local pelo SDK em 2026-10-16.
O snapshot conferido permanece preservado; PostgreSQL, adoção pelos leitores,
rollback, inventário global e limpeza continuam abertos.
[Resultado e próximo critério](../operations/MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16).

### Recuperação do tracking — marco concluído em 2026-10-16

Ensaio nativo concluído: histórico dos dois stores, hash/score da referência,
preservação dos restores e limpeza da cópia aprovados. Resultado e limites em [MLflow](../operations/MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).
A prova encerra esse recorte de recuperação, sem liberar automaticamente conversão
PostgreSQL, adoção S3 por leitores ou remoção de scripts/dados. Esses passos exigem
cobertura dos produtores, destinos, dependências e rollback. Feature store continua
condicional; KinD/Helm/PVCs permanecem após os critérios de persistência.

**Recuperação local MLflow aprovada pelo autor em 2026-10-16:** os dois stores
forneceram 71 runs, 67 logged models e 321 registros de histórico. A referência
recarregada reproduziu o score com erro zero; restores e tabelas foram preservados
e a cópia descartável removida. O gate fecha acesso local pelo SDK e um smoke de
inferência; serviço HTTP/PostgreSQL, migração primária e inventário global mantêm
critérios próprios. [Resultado e evidência](../operations/MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

## Inventário de persistência do checkout — recorte nativo revisado

Justificativa: o inventário inicial exclui `data/storage/` e arquivos fora de `data/`.
O novo coletor cobre metadados do checkout, com catálogo de responsáveis/produtores e
fronteiras externas declaradas. Implementado com 14 testes pytest/tox; coleta nativa
aprovada no recorte do checkout. Não cria serviço, migra storage, modifica writers ou autoriza limpeza.
[Comando e interpretação](../operations/STORAGE.md#inventário-do-checkout-e-catálogo-de-produtores).

Coleta e revisão inicial concluídas; os limites externos continuam explícitos.
Depois, ordenar cobertura faltante, contenção dos produtores e ensaio de serviço
MLflow/PostgreSQL + S3, antes do cutover. Inventário global e recuperação fora da
máquina permanecem abertos; Helm/PVCs e feature store mantêm seus critérios anteriores.

## Primeira contenção dos destinos — validada pelo autor

Quatro CLIs passam a recusar saídas fora de suas áreas em `data/`, antes de efeitos
caros; defaults de relatórios migram para `data/reports/`, sem mover arquivos.
Funções internas e outras CLIs mantêm cobertura pendente. Testes de regressão e
recusa antecipada aprovados nativamente: 82 testes no tox.
[Escopo e comandos](../operations/STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).

Próximo: ensaio isolado MLflow com PostgreSQL e S3, separado dos stores históricos,
validando escrita, leitura de artefato e recuperação antes de ligar consumidores.
Não há cutover ou exclusões neste incremento. Expansão da política, retenção,
1× contínuo, fechamento visual, rollback, Helm/PVCs e feature store conservam os
gates existentes; a feature store exige necessidade demonstrada.

## Incremento atual — serviço de tracking isolado

Justificativa: backups/recarga e destinos conhecidos permitem testar o serviço
sem transformar uma migração histórica em pré-requisito. O Compose dedicado
adiciona PostgreSQL com volume nomeado e MLflow com proxy para um bucket novo.
O papel da aplicação não é superusuário; o cliente do ensaio não recebe chaves S3.
A UI em `127.0.0.1:15001` permite observar o registro operacional na prática.

**Gate concluído em 2026-10-17:** o autor aprovou SQL, S3 e proxy HTTP antes e
depois da recriação dos dois containers dedicados. Os IDs mudaram e as imagens,
o volume PostgreSQL, histórico e bytes permaneceram iguais. Resultado: 1 teste
aprovado; tox `tracking-lab` OK.
[Evidência](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

Depois: adotar um produtor de novas runs com URI explícita e rollback; conferir
um artefato de modelo pelo serviço. Migração histórica/URIs, backup PostgreSQL,
leitores de datasets, contenção restante, limpeza e Helm/PVCs mantêm gates próprios.
Não exigir inventário de todo o Linux para iniciar esse ensaio. Feature store,
monitoramento de features, ensaio 1× e CD/rollback seguem priorizados pelos critérios
já descritos acima, sem acrescentar serviços sem necessidade demonstrada.

### Retentativa do ensaio: mensagens do SDK separadas do JSON

A primeira execução nativa parou no parser após a escrita/leitura HTTP do probe.
O worker passa a enviar mensagens do SDK para stderr, mantendo stdout como JSON.
A regressão usa um subprocesso real; o parser do controlador permanece estrito.
A retentativa posterior foi aprovada pelo autor: conferências SQL/S3/HTTP e
recriação concluídas. O registro anterior da falha permanece preservado.
[Retentativa](../../infra/tracking/README.md#retomar-após-a-falha-no-canal-json--2026-10-16).

### Primeiro produtor HTTP — aceite concluído

Justificativa: o serviço agora demonstrou persistência, mas os produtores ainda
usam os destinos anteriores. Conectar um único produtor de novas runs com URI
HTTP explícita e validar a recarga de seu artefato de modelo pelo proxy torna
o serviço útil no fluxo do projeto. O aceite deve incluir destino conferido,
paridade, ausência de saídas locais imprevistas e retorno ao destino anterior.
Adoção do exemplo de contribuição aprovada no serviço nativo, com recarga
independente e retorno local explícito. A integração HGB é o próximo gate. Histórico, backup
PostgreSQL, datasets primários, limpeza e Helm/PVCs conservam gates próprios.

O exemplo usa 80/20 linhas em memória e exercita modelo skops completo pelo proxy,
sem consumir Handbook. Isso justifica a primeira integração antes de alterar
baseline/HGB e suas identidades persistidas. A falha HTTP é explícita; voltar ao
local muda apenas o destino de novas runs. O aceite nativo deve preservar os
containers e comprovar recarga/paridade sem os arquivos locais disponíveis.
[Procedimento](../modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

### Correção do transporte do produtor HTTP — 2026-10-17

A regressão nativa teve 79 testes aprovados, mas a primeira execução do produtor
parou ao baixar os cinco arquivos do modelo por chunks. No MLflow 3.17, o cliente
pode habilitar URLs assinadas automaticamente a partir das capacidades do backend
S3 anunciadas pelo servidor. O fixture anterior com arquivos locais não cobria
essa diferença. A recarga e o rollback local não foram aprovados nessa tentativa.

O contexto HTTP agora fixa `MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD=false` e
`MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD=false`: o transporte deste produtor passa
pelo servidor MLflow. As duas variáveis, a URI e os limites anteriores são
restaurados ao sair. Não foi aumentado timeout ou removida a revisão skops/paridade.
A retentativa nativa cria outra contribuição e preserva os registros da falha.

[Diagnóstico, retentativa e limites](../../infra/tracking/README.md#retentativa-do-produtor-após-falha-de-download-por-chunks).

### Incremento atual — tracking HTTP do HGB

O exemplo de contribuição foi aprovado pelo autor em 2026-10-17: 34 testes de
regressão e 1 teste nativo, recarga sem saída local disponível, erro zero e retorno
local explícito. [Evidência](../../references/evidence/model_contribution_http_author_validation_2026-10-17.json).
O gate antes descrito como pendente está concluído para esse produtor.

O HGB vem antes da ampliação ao baseline nesta etapa. A justificativa é avançar
no produtor de estudo temporal e proteger seu vínculo entre destino, identidade,
retomada e orçamento. A opção HTTP foi acrescentada para novos estudos; o próximo
aceite nativo é limitado a um fold sintético, sem repetir a busca Handbook.
SQLite permanece no estado Optuna; PostgreSQL recebe metadados MLflow do serviço.

Gate atual: recarga independente do HGB pelo proxy, erro até `1e-12`, saída local
indisponível, uma tentativa e reutilização sem fit/publicação, containers intactos.
O autor aprovou o fold nativo com erro zero e reutilização, em 2026-10-17.
Busca completa, consumidores locais,
inventário entre stores e backup PostgreSQL seguem abertos antes de adoção ampla.
[Procedimento](../operations/HGB_TRACKING.md).

### Incremento atual — os três baselines pelo tracking HTTP

O fold HGB foi aprovado pelo autor: 84 linhas de treino, 21 de validação, uma
tentativa e erro zero, sem publicação repetida ou alteração dos containers.
[Evidência](../../references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).

O próximo aceite cobre o trio inicial de `baseline_experiment.py`: dummy,
regressão logística e HGB, com uma run por modelo e recarga independente de todos.
O transporte HTTP é explícito; o local continua padrão. O teste usa uma Gold
sintética pequena, não repete a avaliação Handbook nem o estudo Optuna.

Gate: três runs concluídas e vinculadas, métricas conferidas, recarga sem modelos
locais disponíveis, paridade até `1e-12`, sem tracking SQLite ou mudança nos
containers. Falha parcial deve preservar audit e não publicar baseline concluído.
O autor aprovou o gate nativo do trio em 2026-10-17. Consumidores/recuperação dos
novos destinos precisam de critérios próprios antes da adoção primária; ampliar
infraestrutura exige justificativa.
[Procedimento](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Aceite do trio concluído — 2026-10-17

Dummy, regressão logística e HGB tiveram suas três runs e recargas independentes
aprovadas pelo autor: dados sintéticos 160/160, erro zero, modelos locais
indisponíveis e containers intactos. [Evidência](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).
Este ensaio não volta ao backlog como validação pendente.

A integração dos produtores desta etapa está fechada. O próximo planejamento
é localizar os consumidores que ainda assumem store local e delimitar uma prova
de recuperação PostgreSQL com os artefatos S3 correspondentes. A inspeção dos
consumidores pode ser somente leitura; uma mudança de destino primário deve
aguardar recuperação e rollback demonstrados. O estado remoto já existe, e
ampliar consumo antes dessa prova aumentaria a dependência de estado sem
restauração ensaiada. Este fechamento não implementa a próxima infraestrutura.
Helm/PVCs, limpeza, feature store e novas buscas continuam sujeitos aos critérios
existentes; nenhum novo serviço é necessário para registrar este aceite.

### Incremento atual — consumidores revisados e protocolo de recuperação

A revisão do consumo foi materializada no catálogo existente e no modo opt-in
do coletor. São 17 registros, com sete fronteiras locais que dependem de
identidade/hashes/ownership ou ferramentas históricas. Há detecção sintática de
fontes novas e divergência de código; sem alegar resolução completa de aliases
ou inventário global. O relatório do checkout do autor foi observado e aprovado no recorte declarado.

A captura coordenada e o restore PostgreSQL/S3 foram implementados e aprovados
pelo autor: volumes/rede isolados, histórico API completo e modelo/score fixados
por recibo anterior. O resultado encerra este recorte de recuperação. A próxima
entrega é CD/rollback do serving; as sete fronteiras de consumidores históricos
continuam mapeadas no backlog, sem ampliar esta etapa de inventário.
[Mapa, comando e critérios](../operations/TRACKING_RECOVERY.md).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte. O build e a exportação da entrega
versionada também foram aprovados nativamente; o consumo do arquivo em outro
alvo tem aceite nativo informado pelo autor; CI remota aprovada no aceite abaixo. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](../operations/SERVING_ROLLOUT.md).

### Continuação da entrega após o gate aprovado

Manter blue-green como direção do portfolio, com uma réplica ativa e candidata
durante a atualização. A versão anterior ficará disponível por uma janela de
rollback a definir; depois poderá ser reduzida a zero. Essa política permanente segue planejada; o fluxo de build/qualificação/exportação
já foi implementado e aprovado nativamente. Canary fica condicionado a tráfego
representativo, roteamento ponderado e critérios de observação; não é requisito
para encerrar este marco. [Decisão e próximos gates](../operations/SERVING_ROLLOUT.md#estratégia-escolhida-para-o-portfolio).

### Gate atual da entrega versionada

O workflow existente encadeia `quality` → `serving-delivery`. O novo job constrói
uma imagem do commit, verifica HTTP/rollback com release sintética e preserva
o arquivo exato com descritor/hashes no artifact (três dias). A implementação e
a regressão offline foram concluídas; o **aceite Docker local foi aprovado**.
A execução GitHub Actions foi aprovada em 2026-10-19; o aceite está registrado abaixo.
Não requer nova confirmação estatística, acesso à referência ou migração.

Consumo do arquivo e identidade/HTTP concluídos nos aceites nativo e CI abaixo.
Permanece pendente a promoção explícita de release aprovada. O ensaio anterior
com os mesmos pesos continua fechado. A política de retenção anterior e o
provisionamento KinD + Helm permanecem planejados; publicar uma imagem no
registry ou adicionar um controlador não é parte deste incremento.
[Gate e limites](../operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

O aceite de 2026-10-19 vincula a entrega ao commit `0b95136`, com hashes da imagem,
release e descritor preservados na [evidência](../../references/evidence/serving_delivery_author_validation_2026-10-19.json).
Não reabrir build/qualificação, consumo local ou CI como pendências. Os aceites
posteriores registram a recarga e a execução entre runners; promoção e
provisionamento continuam no backlog.

### Gate de consumo e CI entre jobs — preparação de 2026-10-19

O consumidor verifica o pacote por commit/hash externos, carrega a imagem exata
e testa HTTP em um container novo, sem rebuild ou fit. A entrega original e
as tags alheias são preservadas. A CI encadeia `quality` → `serving-delivery` →
`serving-consumer`; o último job recebe o artifact e o hash do produtor em outro
runner. Implementação, aceite nativo e **GitHub Actions aprovados**; detalhes no aceite remoto abaixo.
O teste local usa o arquivo já aprovado e não exige repetir o build.
[Procedimento, pins e limites](../operations/SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Três jobs do GitHub Actions aprovados; próximo: promoção explícita e provisionamento**, sem repetir
os gates locais encerrados. [Resultado e limites](../operations/SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).

### CI de entrega e consumo aprovada — 2026-10-19

A [execução 39306262564](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
passou nos três jobs do commit `6a3782b0ccb9d6c0a098251743483aed73e49079`:
qualidade → build/qualificação → consumo do artifact em outro runner. A API
confirma sucesso e runners distintos; o artifact pequeno do consumidor foi
baixado, teve digest conferido e seus recibos de recarga/HTTP/cleanup lidos.
**Gate remoto encerrado.** Próximo: promoção explícita e provisionamento
reproduzível KinD/Helm; registry e deploy permanente ainda têm critérios próprios.
[Resultado e limites](../operations/SERVING_ROLLOUT.md#aceite-do-fluxo-no-github-actions--2026-10-19).

### Primeiro recorte de monitoramento de features — preparação de 2026-10-19

Implementados leitura somente de requests persistidos, métricas no exporter
existente, terceiro dashboard Grafana e alertas descritivos. Referência: primeiras
1.000 decisões; comparação: últimas 1.000, sem sobreposição e com dados válidos.
Não é baseline de treino nem medida de perda de qualidade. Demonstração gerada
sem fit cobre igualdade, mudança de valor, ausência e atraso de relógio. O aceite
nativo e a conferência da stack ainda estão pendentes.

[Procedimento e limites](../operations/FEATURE_MONITORING.md). Depois desse aceite,
seguir para o provisionamento do fluxo completo com KinD + Helm; CT permanece
sujeito a dados/labels/protocolo elegíveis, e Terraform sucede a simplificação.

### Aceite mínimo de features concluído — 2026-10-19

O aceite pendente na nota de preparação acima foi concluído: 116 testes e demo
aprovados; duas janelas válidas; exporter reconstruído, oito regras validadas,
scrape e dashboard conciliados. O marco foi removido do backlog e registrado
na tabela de concluídos. A contagem passa a usar cor neutra; sua apresentação
atualizada será conferida no próximo refresh. Não houve teste de firing dos
novos alertas, CT, migração primária ou nova confirmação de qualidade do modelo.
[Evidência](../../references/evidence/feature_monitoring_author_validation_2026-10-19.json).
Próximo: fluxo completo em KinD + Helm, seguido de simplificação; Terraform
permanece o desfecho posterior. Feature store, broker e CT são condicionais.

### Fundação do provisionamento — preparação de 2026-10-20

O próximo marco permanece no backlog. O primeiro recorte prepara cluster
`fraud-platform`, RustFS/PostgreSQL com PVCs, credenciais próprias e gate de
releitura SQL/S3 após recriação de pods. Templates e testes são verificados em
ambiente controlado; aceite Kubernetes real pendente. Não há migração primária
nem adoção de volumes Compose. Dados Bronze–Silver–Gold, jobs, tracking, replay,
serving e observabilidade terão gates de integração no mesmo fluxo.
[Operação](../operations/KIND_FOUNDATION.md).

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](../operations/KIND_TRACKING.md#aceite-nativo--2026-10-20).

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.

### Replay no KinD — aceite nativo de 2026-10-20

O gate passou: 1 teste em 17,89 s, tox em 33,80 s. O pod interrompido em
`feedback:2` foi substituído; a transação incompleta foi revertida, o PVC/PV e
o Deployment permaneceram os mesmos e todas as tabelas coincidiram com a
execução sintética sem interrupção. Reentrega sem rescoring, fundação preservada,
processamento removido e PVC retido. O recorte foi registrado como concluído;
o fluxo completo continua no backlog pelos gates restantes de serving e
observabilidade. [Operação e limites](../operations/KIND_REPLAY.md).

### Serving no KinD — aceite nativo de 2026-10-21

O autor aprovou os seis eventos do gate em 2026-10-21: baseline, bloqueio de
pacote inválido, candidata direta, troca na mesma URL, falha HTTP 502 e retorno
por `helm rollback`. A imagem qualificada da API foi reutilizada, sem rebuild
do runtime ou fit; somente o carrier foi construído. Os mesmos pesos sintéticos
produziram score `0.03463335444035967` antes/depois da troca e do rollback.
Baseline/gateway mantiveram seus pods e a fundação permaneceu igual; candidata
removida, baseline instalada no laboratório e port-forward temporário encerrado.
Gate: 1 teste em 19,48 s; tox em 34,53 s. Próximo: observabilidade no KinD.
[Operação e limites](../operations/KIND_SERVING.md#aceite-nativo--2026-10-21).

### Observabilidade no KinD — aceite nativo e visual de 2026-10-21

O autor aprovou o gate nativo e confirmou a conferência visual em 2026-10-21.
A cópia somente de leitura foi conciliada com a origem: **556.824 eventos,
143.945 decisões e 1.453 alertas**. Scrape Prometheus, oito regras saudáveis,
proxy autenticado do Grafana e quatro dashboards provisionados foram conferidos.
O exporter ganhou novo pod, manteve Deployment/PVC e repetiu as verificações.
Prometheus, Grafana, fundação, tracking, serving e PVC retido do replay
preservaram suas identidades; arquivos da origem permaneceram iguais.
Gate: 1 teste em 62,64 s; tox em 73,91 s. Próximo: operação e fechamento
integrado, mantendo o marco de infraestrutura aberto até o aceite desses gates.
[Escopo e limites](../operations/KIND_OBSERVABILITY.md#aceite-nativo-e-visual--2026-10-21).

Refinamento visual aprovado em 2026-10-21: [política semântica de cores](../operations/DASHBOARD_COLORS.md#aceite-da-política--2026-10-21),
com informações azuis e estados explícitos em texto/cor. Configuração verificada
pela API antes/depois de recriar o exporter e conferência visual confirmada pelo
autor. Métricas, arquivos de origem/cópia e três PVCs iguais ao aceite anterior.
Próximo: operação de parada/subida, retomada após reboot e fechamento integrado.


### Fechamento operacional e integrado — 2026-10-21

O gate nativo de parada/subida passou (1 teste em 252,23 s; tox 264,31 s).
Em seguida, o reboot manual foi reconhecido por outro boot_id; o mesmo container,
node, engine e PVCs foram preservados. Réplicas restauradas, histórico MLflow,
hash S3, score/identidade HTTP e métricas/dashboards conferidos. Valores inotify
512/524288 persistiram. Sem fit, replay, nova avaliação ou promoção histórica.
Os pendentes operacionais das notas anteriores estão encerrados nesse escopo.
[Runbook](../operations/KIND_OPERATIONS.md) e [guia integrado](../operations/KIND_PLATFORM.md).


### Recorte concluído — desempenho e transparência no portal

Quarta página implementada, com contrato independente do algoritmo, Inputs e
pesos conferidos, ranking temporal, curvas, métricas da política e rastreabilidade.
A fila pode existir sem explicador compatível. Testes e navegador locais usam
fixtures; o autor aceitou a avaliação do snapshot nativo e a conferência visual
em 2026-10-22. O produto
prioriza investigação, com políticas e feedback; não transforma alvo binário em
veredito automático nem afirma economia operacional sem medição.
[Guia](../operations/PORTAL_PERFORMANCE.md). O próximo fechamento consolida o
aceite visual/funcional e a demonstração por usuário. A promoção de sucessor com
pesos diferentes continua dependendo de candidato qualificado.

### Sessão operacional — preparação em 2026-10-23

Implementação aditiva de produtor com ritmo configurável, pausa/retomada,
checkpoint causal HTTP e página de investigação com atualização automática.
[Guia](../operations/PRODUCT_SESSION.md). Não substitui os snapshots aceitos.
Ensaio nativo de 200 decisões aceito pelo autor e captura final conferida em 2026-10-23. Os indicadores da sessão foram integrados ao
Prometheus/Grafana, com aceite nativo e visual em 2026-10-24. A unificação das
páginas de sessão e evidências em um portal continua no recorte seguinte. O gate de promoção permanece inalterado.

### Aceite de sessão e ampliação — 2026-10-23

Sessão `arrival-v1` aceita: 200 decisões/recibos HTTP, 13 alertas, pausa/retomada, feedback e reconciliação; zero novas pontuações na reexecução. [Evidência](../../references/evidence/product_session_author_validation_2026-10-23.json). Preparação da ampliação com janela/volume explícitos, paginação SQL e medição de fases em [PRODUCT_SESSION_SCALE.md](../operations/PRODUCT_SESSION_SCALE.md); ensaio ampliado aceito pelo autor com 2.001 decisões, 81 alertas, 1.561 clientes e zero novas chamadas na reutilização. [Evidência](../../references/evidence/product_session_scale_author_validation_2026-10-23.json). Monitoramento da mesma sessão aceito em [SESSION_MONITORING.md](../operations/SESSION_MONITORING.md), com confirmação nativa e visual em 2026-10-24; unificação do portal permanece no recorte seguinte.

### Monitoramento da sessão — aceite em 2026-10-24

A mesma sessão da fila alimenta uma projeção de agregados no exporter existente,
com jobs Prometheus distintos, dashboard próprio e expiração explícita da fonte.
O aceite nativo conferiu o vínculo com `arrival-2k-v1`, a expiração e a retomada,
sem novo scoring ou alteração do champion. O autor confirmou a revisão visual;
a suíte geral passou com 1.705 testes e 12 subtestes. O baseline da configuração
nova foi emitido e passou no `platform-check`, com portal e desempenho intactos.
[Evidência](../../references/evidence/session_monitoring_author_validation_2026-10-24.json).
A chegada concorrente permanece fora do escopo desse ensaio de sessão concluída.
Próximo recorte: unificar fila corrente e evidências no mesmo portal Streamlit,
com navegação e identidade de sessão/modelo explícitas.
Promoção recorrente com pesos diferentes e rollback continuam condicionados a
candidato qualificado; o gate não foi afrouxado.

### Portal único — aceite concluído em 2026-10-24

Sessão corrente, fila, contexto, explicações verificadas e avaliação passam à
mesma aplicação Streamlit. Origem e identidade acompanham cada área; nenhum
resultado da avaliação é apresentado como qualidade da janela corrente.
Vínculo explícito e leitura paginada preservam os recibos e o baseline atuais.
[Guia](../operations/UNIFIED_PORTAL.md). Aceite nativo com arquivos aceitos e
conferência visual concluídos pelo autor, incluindo o cabeçalho refinado.
[Evidência](../../references/evidence/unified_portal_author_validation_2026-10-24.json).
O gate de promoção permanece inalterado.

### Próximo recorte — footprint medido das imagens

Consolidar as imagens ativas por papel, tamanho reportado por runtime e camadas
compartilhadas/cache. A API já usa build em estágios e há imagens especializadas,
mas não existe aceite de otimização global da infraestrutura. O inventário deve
separar Docker do host, containerd do node KinD, workloads ativos e imagens de
jobs/ensaios. [Avaliação de código e medição](../operations/IMAGE_FOOTPRINT.md).
Uma alteração posterior de imagem será qualificada com os gates de sua função;
a promoção/rollback de sucessor com novos pesos continua dependente de candidato
qualificado, sem afrouxamento do gate.


### Manutenção do runtime qualificado — preparação de 2026-10-25

Redução de 2,38% e gates Docker com o mesmo champion aceitos em
[IMAGE_FOOTPRINT.md](../operations/IMAGE_FOOTPRINT.md). A manutenção agora tem
preparação isolada no KinD, aprovação vinculada à imagem/release, apply e rollback
retomáveis, cadeia de recibos compatível e verificação dos readbacks anteriores.
Execução nativa e baseline correspondente **pendentes**. O chart mantém Recreate;
não é um aceite de zero downtime nem de promoção de sucessor com pesos diferentes.
Guia: [SERVING_RECOVERY.md](../operations/SERVING_RECOVERY.md).
