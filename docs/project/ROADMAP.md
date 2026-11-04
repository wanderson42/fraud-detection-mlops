# Roadmap — Fraud Detection MLOps

Fechamento em **2026-10-31 (America/Belem)**; os registros anteriores de boot
usam 2026-11-01 em UTC. A reprodução foi priorizada antes de Terraform para
revelar dependências do ambiente do autor. O percurso manual integral foi agora
aceito numa segunda VM vazia, com dados adquiridos e modelos/recibos próprios,
portal/SHAP/Grafana aprovados visualmente e recuperação após reboot.

O PR #22 (`c6045f4`) integrou o [roteiro único](../operations/CLEAN_INSTALL_RUNBOOK.md),
setup por fases e [túneis com portas livres](../operations/VM_ACCESS.md). O autor
aceitou nativamente abertura/reutilização, recusa de porta ocupada e encerramento
somente dos túneis. O marco funcional está encerrado no escopo demonstrado;
este fechamento documental não repete treino ou sessão já aceitos. As fases de
instalação do script de setup não receberam nova execução nativa.
Os [aceites e limites](../operations/VM_REPRODUCTION_PROGRESS.md) distinguem as
duas VMs; o [histórico](ROADMAP_HISTORY.md) preserva a evolução da decisão.

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
| Integração | PRs #11 a #14 integrados; PR #13 em `66e2bbf`, CI #70; supervisão no PR #14 em `9d9c8f2`, CI aprovado informado pelo autor |
| Fundação e tracking na VM | KinD criado, PostgreSQL/RustFS prontos, PVCs Bound; artefato S3 e reload HTTP verificados após recriar o pod MLflow |
| Dados na VM | Bronze adquirido da origem, 183/183 arquivos; Silver/Gold produzidos por Jobs, publicados e restaurados com verificação independente |
| Sessão e Grafana na VM | 200 decisões/13 alertas; cinco dashboards, métricas e preservação de recibos/PVCs conferidos; [acompanhamento](../operations/VM_REPRODUCTION_PROGRESS.md) |
| Supervisão no host da VM | Observador e acesso Grafana ativos via systemd de usuário; recuperação após término dos processos, logout completo e reboot planejado verificadas, sem novas inferências; [runbook](../operations/VM_USER_SUPERVISION.md) |
| Rodada canônica na VM | `repro-v1`: três candidatos, gate, alias champion versão 1, API com os pesos esperados, troca blue/green, falha 502 e rollback; `canonical_round_complete` |

O estado original permanece vinculado à baseline `baseline-arrivals-day-v1.json`,
conforme o guia operacional. Essa baseline pertence ao ambiente original e não
deve ser copiada para assumir ownership da VM. Consulte o
[estado da reprodução na VM](../operations/VM_REPRODUCTION_PROGRESS.md).
A evidência da VM é a saída de terminal fornecida pelo autor; os recibos completos
continuam no guest. Não atribuímos hashes a arquivos que não foram recebidos.

## Reprodução limpa: estado atual

| Gate na segunda VM `fraud-clean-install` | Estado |
| --- | --- |
| Ubuntu/ferramentas/checkout identificado | Executados sem acervo privado anterior; revisão `c734cf3` |
| Fundação, tracking e persistência de pods | Aceitos com ownership, credenciais e volumes próprios |
| Bronze e Jobs Silver/Gold | Adquiridos/produzidos e verificados na VM nova |
| Rodada `clean-install-v1` | Novo treino de três candidatos; HGB versão 1; Registry/API/rollback verificados |
| Fonte e sessão próprias | 288172 eventos na fonte; 269334 na sessão, 200 decisões HTTP e 13 alertas, feedback e reconciliação |
| SHAP, portal e desempenho | Cinco casos, 67255 linhas de validação, verificações e revisão visual aceitas |
| Monitoramento | Mesma sessão, métricas e quatro componentes; expiração/retomada e visual aceitos |
| Supervisão e reboot | Recuperação de processos e após reboot aceitas, mesmos dados e hashes; 32 s da partida das unidades à observação |
| Logout completo na VM nova | Confirmação explícita de encerramento de todas as conexões ainda ausente |
| Túneis SSH com portas automáticas | Aceitos nativamente: abertura, reutilização, conflito e stop preservando serviços/sessão |
| Scripts de setup por fases | Validação local; instalação manual já aceita, sem reexecução das novas fases automatizadas |
| Backup/restore integral e perda física | Fora do aceite; tracking limitado já tem evidência própria anterior |

## Backlog ordenado

| Ordem | Recorte | Critério de conclusão |
| --- | --- | --- |
| 1 — aceite funcional concluído | Reprodução limpa e acesso operacional | Percurso completo, visual/reboot e túneis aceitos; finalizar integração deste registro documental, sem novos ensaios |
| 2 — escopo qualificado | Continuidade operacional | Reboot e processos aceitos na VM nova; confirmar logout completo dela. Medição por container, HA e restore integral são limites separados |
| 3 — aceite nativo concluído | Coordenação da cadeia limitada | Mesmo Job retomado, duplicidade recusada e resultado reutilizado; manter controladores atuais nesse escopo, com revisão futura por requisitos concretos |
| 4 — próximo após integração | Terraform local | Declarar ambiente estabilizado e ownership; providers/locks/state protegidos, apply real e segundo plan sem mudanças; preservação/restauração dos dados explícitas |
| Opcional, após consolidação local | Aprendizado e POC temporária em nuvem | Confirmar elegibilidade/plano, serviços disponíveis e limite financeiro; escopo mínimo, execução verificável e remoção conferida; sem hospedagem permanente obrigatória |
| Condicionado | Sucessor do champion | Candidato qualificado, autorização, ativação/rollback com pesos diferentes e recuperação coerente; gates preservados |
| Projeto separado, posteriormente | Retomar portfólio de energia | Revisar pipeline de treinamento e reprodução em VM; usar o resultado para rever o esboço Terraform daquele repositório |

## Próxima entrega concreta

Integrar por PR/CI a implementação e o [aceite nativo da retomada](../operations/PIPELINE_RECOVERY.md#aceite-nativo-da-retomada)
executado pelo autor em `87c672f`. O teste preservou o mesmo Job Silver após término
do controlador, recusou outra invocação, retomou as etapas, reutilizou o resultado
completo e removeu os Jobs temporários, com sessão e champion preservados.
Não repetir esse ensaio para documentar; os recibos privados ficam preservados.

A decisão é manter os controladores no escopo atual de uma VM e cadeia limitada.
Agendamento/eventos, múltiplos hosts e visibilidade central são condições para
reavaliar a adoção de um orquestrador, sem obrigação de instalar outro serviço.
O incremento não demonstra coordenação distribuída ou retomada de treino parcial.

Após merge e sincronização, planejar **Terraform local**: definir fronteira de
ownership com Helm/KinD já existentes, proteger state/segredos e preservar volumes.
A preparação deve explicitar como evitar adoção ou destruição acidental do ambiente
aceito. Nenhum apply ou recurso pago faz parte deste fechamento documental.

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
