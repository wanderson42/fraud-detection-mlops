# Mural de metas — Fraud Detection MLOps

Atualizado em **2026-10-15**. Backlog principal, em ordem de dependência e valor
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

## Marcos concluídos e evidência preservada

| Marco | Estado e limite |
| --- | --- |
| Bronze, Silver e Gold | 183 dias; 1.754.155 transações; 19 preditores causais; contratos, manifestos e verificadores |
| Referência e experimentos | HGB congelado, três ablações e 20 trials/63 fits; nenhum candidato elegível |
| Avaliação temporal | Maio: AP 0,640703; referência de 02–15/09: AP 0,621312; reservas consumidas |
| Estatística exploratória | Dependência e reamostragem documentadas; sem intervalo de generalização ou superioridade formal |
| Tracking e serving | MLflow/skops, HTTP/wheel e Docker validados pelo autor; imagem e release identificadas |
| Política e estado causal | Primeiro cruzamento do limiar 0,8826493808370081; até 100 clientes/dia; feedback separado sete dias depois |
| Replay nativo e correção | 556.824 eventos/143.945 scores; 19 features exatas e gate de scores aprovado; 1.453 alertas preservados |
| Resultado descritivo da fila | 768 capturas/358 perdas de clientes-dia fraudulentos em 16–30/09; precisão 52,86% e recall 68,21% agregados |

O replay corretivo já foi executado e verificado pelo autor. Não volta ao backlog
como execução futura. [Recibo e origem](../../references/evidence/native_numerical_correction_author_validation_2026-10-13.json).
Os protocolos, os resultados anteriores e seus checkpoints permanecem preservados.
Os números de testes históricos descrevem a entrega correspondente, não a CI de
um commit ainda não publicado.

## Backlog ordenado

| Ordem | Entrega | Estado atual | Critério de conclusão |
| --- | --- | --- | --- |
| 1 | Replay com emissão programada e recuperação medida | Adaptador, protocolo e testes gerados preparados nesta entrega; execução nativa pendente | Reusar entrada materializada, registrar velocidade/capacidade/latências/CPU/RSS, interromper e retomar, verificar as 19 features, scores e decisões sem alterar o histórico |
| 2 | Monitoramento e dashboards locais | Exporter, Prometheus, dois painéis Grafana e regras preparados; validação descrita no runbook | Demonstrar suporte/maturidade, filas e decomposição de latência; provocar falhas controladas, observar alarme, recuperar e registrar evidência no ambiente do autor |
| 3 | Armazenamento de objetos e recuperação | RustFS local escolhido; perfil Compose opcional preparado | Upload idempotente, manifesto/checksums, credenciais fora do Git, download e restauração verificável de artefatos; medir espaço e tempo; provar recuperação após perda do diretório de trabalho |
| 4 | CD e rollback de release | Backlog | Imagem e modelo identificados, gates de integridade/paridade/saúde, troca controlada e retorno à release anterior, com falha injetada e evidência |
| 5 | Monitoramento de dados/features e gestão de features | Contratos e paridade existentes; próxima extensão após métricas operacionais | Medir schema, ausência, frescor/atraso e distribuições de features com suporte; baseline temporal versionado e testes de resposta. Não confundir score drift com feature drift |
| 6 | CT controlado | Backlog, depende de 2–5 | Gatilho explícito, labels disponíveis, orçamento, treino e avaliação temporal comparável; candidato rejeitado ou promovido com gates e rollback. Drift sozinho não promove |
| 7 | Orquestração de jobs | Condicional à composição real de jobs | Coordenar treino/avaliação/publicação, retries e estados; escolher ferramenta pelo problema demonstrado. O consumidor de eventos continua processo próprio |
| 8 | Spike de feature store | Condicional aos critérios abaixo | Demonstrar reutilização, joins point-in-time corretos, disponibilidade/frescor online, paridade, recuperação e custo comparados à solução atual |
| 9 | Transporte externo e carga sustentada | Condicional à necessidade demonstrada | Broker, política de atraso/reentrega, backpressure, falha e recuperação medidos sob carga declarada; não inferir entrega distribuída a partir de uma fila em processo |
| 10 | Kubernetes/kind e IaC | Adiado até operação local e rollback estáveis | Justificar custo/complexidade; provar ganho de implantação e recuperação. Terraform/cloud não são pré-requisitos do laboratório |
| 11 | Novo ciclo de modelos e confirmação estatística | Condicional a hipótese/candidato e avaliação preservada | Novo desenho antes do acesso, efeito relevante e dependência temporal/por entidade tratados; não reutilizar maio ou setembro como confirmação nova |

As ordens 1 e 2 formam o incremento atual. Armazenamento de objetos sobe para a
ordem 3 e antecede CD/CT: não fica escondido atrás de Kubernetes ou de novas
buscas de hiperparâmetros. Dados/features, CT, orquestração e inferência por eventos
continuam no plano. O incremento de Prefect de checagem isolada foi retirado;
um futuro orquestrador depende de uma composição de jobs que o justifique.

## Incremento atual — monitoramento local

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

**Decisão: armazenamento S3 compatível local, com RustFS como backend inicial.**
Não exige conta de nuvem. Prometheus, Grafana OSS e RustFS rodam em perfis locais;
RustFS é opcional enquanto o fluxo de arquivamento não está implementado.
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
pode ajudar quando duas ou mais aplicações/treinos realmente reutilizarem as
mesmas features e o trabalho de materialização/frescor precisar de gestão comum.
O spike, por exemplo com Feast local, só começa com esse caso e orçamento claros.

| Pergunta do spike | Evidência exigida |
| --- | --- |
| Qual consumidor adicional reutiliza quais features? | Consulta e materialização comparadas com o fluxo atual, sem duplicar lógica |
| O join histórico respeita o instante de decisão? | Testes de point-in-time e de disponibilidade: feedback conhecido só sete dias depois; um timestamp de ocorrência sozinho não resolve disponibilidade |
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
