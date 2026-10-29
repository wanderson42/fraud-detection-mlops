# Roadmap — Fraud Detection MLOps

Atualizado em **2026-10-28**. A jornada diária foi integrada no PR #12 e o CI
pós-merge da main foi aprovado. A reprodução limpa está em execução numa VM
isolada: fundação, tracking, dados e primeira rodada canônica foram verificados;
a sessão HTTP e o monitoramento em cinco dashboards foram verificados.
A reprodução integral e os gates de recuperação da VM continuam pendentes.

O [guia operacional principal](../operations/PRODUCT_OPERATIONS.md) concentra o
percurso do ambiente original. O [histórico](ROADMAP_HISTORY.md) preserva os
incrementos anteriores. Os aceites são específicos de cada ambiente.

## O produto que entregamos

Priorizar investigação com features causais, modelo identificado, política de
alertas, fila consultável, explicações verificadas quando disponíveis e feedback
posterior. O sistema transacional recebe pontuação; o analista recebe contexto;
a operação acompanha saúde e progresso; ML acompanha linhagem e atualização.
O modelo não assume sozinho a decisão sobre pagamentos.

## Marcos aceitos

| Entrega | Aceite e fonte |
| --- | --- |
| Dados e rodada canônica original | Bronze/Silver/Gold identificados, três candidatos com Inputs, champion e API com os mesmos pesos; [rodada](../operations/OPERATIONAL_ROUND.md) |
| Governança de candidatos | Retenção nativa do champion nas rodadas fixa e de busca; [atualizações](../operations/MODEL_UPDATES.md) |
| Jornada diária original pela API | 9.619 decisões/100 alertas, contexto causal, pausa/retomada, feedback e reconciliação; [aceite](../operations/LIVE_OPERATIONS.md#aceite-nativo-e-visual-final--2026-10-27) |
| Portal e transparência originais | Portal único aceito funcional/visualmente; sessão, 200 casos/cinco explicações e avaliação de 67.255 transações com origens explícitas; [portal](../operations/UNIFIED_PORTAL.md) |
| Monitoramento original | Mesma sessão durante chegadas no Prometheus/Grafana e expiração/retomada; [operação](../operations/SESSION_MONITORING.md) |
| Ciclo de vida original | Parada/subida canônica no mesmo node/volumes; reboot no escopo histórico registrado; [runbook](../operations/KIND_OPERATIONS.md). Não transferir esse aceite para a VM |
| Simplificação | Entradas Make por finalidade, IO/CRI compartilhados, importador concluído e serving sintético aposentados; [revisão](../operations/CODEBASE_SIMPLIFICATION.md) |
| Footprint | Reduções e qualificações registradas em [IMAGE_FOOTPRINT.md](../operations/IMAGE_FOOTPRINT.md). Nova rodada de treino executada na VM; nenhuma nova medida de tamanho é inferida disso |
| Integração | PRs #11 e #12 integrados; main `6c01e44d48f694c7218446f917491e3329029f83`, CI pós-PR #12 aprovado conforme saída do autor |
| Fundação e tracking na VM | KinD criado, PostgreSQL/RustFS prontos, PVCs Bound; artefato S3 e reload HTTP verificados após recriar o pod MLflow |
| Dados na VM | Bronze adquirido da origem, 183/183 arquivos; Silver/Gold produzidos por Jobs, publicados e restaurados com verificação independente |
| Sessão e Grafana na VM | 200 decisões/13 alertas; cinco dashboards, métricas e preservação de recibos/PVCs conferidos; [acompanhamento](../operations/VM_REPRODUCTION_PROGRESS.md) |
| Rodada canônica na VM | `repro-v1`: três candidatos, gate, alias champion versão 1, API com os pesos esperados, troca blue/green, falha 502 e rollback; `canonical_round_complete` |

O estado original permanece vinculado à baseline `baseline-arrivals-day-v1.json`,
conforme o guia operacional. Essa baseline pertence ao ambiente original e não
deve ser copiada para assumir ownership da VM. Consulte o
[estado da reprodução na VM](../operations/VM_REPRODUCTION_PROGRESS.md).
A evidência da VM é a saída de terminal fornecida pelo autor; os recibos completos
continuam no guest. Não atribuímos hashes a arquivos que não foram recebidos.

## Reprodução limpa: estado atual

| Etapa na VM `fraud-repro` | Estado |
| --- | --- |
| Ubuntu, Docker, Python/Poetry, KinD, kubectl, Helm e inotify | Instalados e verificados |
| Fundação PostgreSQL/RustFS e tracking | Verificados nativamente |
| Aquisição Bronze e construção Silver/Gold | Verificadas nativamente |
| Treinamento, Registry, API e rollback da rodada `repro-v1` | Verificados nativamente |
| Fonte de eventos da sessão aceita | Sessão reconciliada com 269.334 eventos; o ledger de contexto não representa inferências |
| Sessão HTTP na VM | Verificada: 200 decisões, 13 alertas, 199 clientes e 200 feedbacks nas decisões |
| Grafana da VM | Cinco dashboards; métricas verificadas, expiração/retomada ensaiadas e revisão visual recebida |
| Portal da VM | Exportação da sessão adaptada; desempenho, SHAP e aceite integral do portal ainda precisam de fechamento próprio |
| Stop/start, reboot e restauração de backup na VM | Pendentes; não cobertos pelos aceites anteriores |
| Reprodução completa do produto | Pendente |

## Backlog ordenado

| Ordem | Recorte | Critério de conclusão |
| --- | --- | --- |
| 1 — em andamento | Concluir reprodução limpa | Consolidar os marcos já aceitos, fechar o portal e qualificar instalação do zero; recibos próprios e limitações explícitas |
| 2 | Consolidar operação da VM | Inventário de serviços e tarefas, supervisão, acessos SSH, status/logs, limites de recursos e recuperação; rotina diária sem depender de terminais persistentes |
| 3 | Decidir coordenação dos pipelines | Testar fluxo limitado com falha/retomada e invocação duplicada; comparar executor existente e uma alternativa; adotar orquestrador somente se reduzir complexidade total |
| 4 | Terraform local | Declarar ambiente estabilizado e ownership; providers/locks/state protegidos, apply real e segundo plan sem mudanças; preservação/restauração dos dados explícitas |
| Opcional, após consolidação local | Aprendizado e POC temporária em nuvem | Confirmar elegibilidade/plano, serviços disponíveis e limite financeiro; escopo mínimo, execução verificável e remoção conferida; sem hospedagem permanente obrigatória |
| Condicionado | Sucessor do champion | Candidato qualificado, autorização, ativação/rollback com pesos diferentes e recuperação coerente; gates preservados |
| Projeto separado, posteriormente | Retomar portfólio de energia | Revisar pipeline de treinamento e reprodução em VM; usar o resultado para rever o esboço Terraform daquele repositório |

## Próxima entrega concreta

Consolidar a operação da VM: inventariar processos, supervisionar o observador,
organizar status/logs e simplificar os acessos após rollout. Preservar os aceites
da sessão concluída e fechar separadamente as evidências restantes do portal.
O código da branch `feat/vm-session-portal`, até `0f7f277`, foi enviado ao GitHub
e recebido no Alienware; isso não atualiza os serviços do ambiente original nem
comprova CI ou merge dessa branch.

Instalação completa a partir de checkout vazio, stop/start, reboot e restauração
de backup precisam de gates próprios. O aceite atual não cobre novas chegadas
na VM, firing de alertas de monitoramento ou confirmação independente de qualidade.

## Critérios de arquitetura e custo

Terraform provisiona infraestrutura; Kubernetes/ECS supervisionam workloads;
um executor de pipeline coordena etapas e seus estados. Terraform e uma eventual
POC em nuvem não obrigam instalar Prefect, Airflow ou outra plataforma.
Treinamento automático e ativação automática de modelos permanecem decisões
separadas. Atribuir alias champion não equivale a atualizar a API.

A nuvem é uma extensão opcional de aprendizado. O projeto local deve continuar
reproduzível sem conta ou pagamento. ECS, EKS, Step Functions e MLflow gerenciado
são alternativas de estudo, não componentes aprovados ou provisionados.
Veja [CLOUD_LEARNING.md](../operations/CLOUD_LEARNING.md) para o plano gratuito,
restrições e critério de um ensaio temporário. Não existe autorização neste
roadmap para contratar recursos, mudar para plano pago ou manter implantação.

## Simplificação após maturação da infraestrutura

Os incrementos de simplificação aceitos permanecem preservados. Novas remoções
ou otimizações dependem de redundância ou gargalo observado. O adaptador externo
de eventos é uma ponte da reprodução; sua incorporação recorrente exige código
versionado, testes e supervisão com escopo próprio.

A análise de CT, orquestração, feature store e broker continua em
[ROADMAP_HISTORY.md](ROADMAP_HISTORY.md#análise-de-adoção-ct-orquestração-feature-store-e-broker).
Essas opções não são obrigações automáticas da reprodução, de Terraform ou da
nuvem. Reutilizar períodos já observados demonstra operação, não nova confirmação
independente de qualidade.

## Auditoria de reprodução limpa — acompanhamento

A auditoria introduzida no commit local `0aecf81` foi executada no Alienware:
fontes auditadas compatíveis, nó canônico presente e destino ainda não selecionado
naquele preflight. Posteriormente o autor criou a VM e executou os marcos acima.
A auditoria é somente leitura; seu sucesso não é aceite de reprodução completa.
O procedimento histórico está em `make reproduction-audit` e
[VM_REPRODUCTION_PROGRESS.md](../operations/VM_REPRODUCTION_PROGRESS.md) registra
a evolução posterior. Quando presente na branch, o guia
`docs/operations/CLEAN_REPRODUCTION.md` permanece preservado como fonte do
preflight e das lacunas originalmente encontradas.
