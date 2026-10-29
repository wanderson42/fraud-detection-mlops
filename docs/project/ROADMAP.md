# Roadmap — Fraud Detection MLOps

**Inventário operacional da VM coletado em 2026-10-29:** workloads, volumes,
acessos e requests/limits conferidos; consumo por container indisponível. A
preparação da baseline encontrou incompatibilidade de retorno entre os perfis de
observabilidade. Correção commitada e validada nativamente; baseline própria e
ciclo planejado de parada/subida aceitos, com mesma sessão e volumes. CI/PR da
correção e backup/restauração continuam pendentes.
[Registro](../operations/VM_REPRODUCTION_PROGRESS.md).

**Incremento do portal da VM:** snapshot, vínculo de sessão, desempenho (67.255
linhas), saúde HTTP e revisão visual foram confirmados pelo autor. Foram 200
decisões, 199 clientes, 13 alertas e 200 feedbacks, sem explicações nesse snapshot.
SHAP da sessão foi publicado e verificado para cinco casos, com navegação nativa
aprovada e captura de caso explicado revisada. O recorte de portal da sessão
concluída foi aceito e integrado no PR #16. A correção do ciclo ainda requer CI/PR. Instalação
integral e backup/restauração continuam abertos. O ciclo planejado foi aceito em
ensaio posterior próprio, descrito no registro da VM.

Atualizado em **2026-10-29**. A reprodução em outro ambiente entrou antes de
Terraform para identificar dependências da máquina original antes de declará-las
como infraestrutura como código (IaC). A VM revelou dependências de estado,
recibos, perfis e processos que não estavam cobertas pela suíte Python.
Esse trabalho desdobrou-se em fonte de eventos, sessão HTTP, observabilidade
identificada e supervisão dos processos do host.

O [PR #13](https://github.com/wanderson42/fraud-detection-mlops/pull/13) foi integrado
em `66e2bbf`, com os três jobs do CI #70 aprovados. A supervisão foi integrada depois no PR #14, em `9d9c8f2`, com CI aprovado
informado pelo autor. Logout completo e reboot planejado da VM foram ensaiados
com preservação da mesma sessão. A reprodução integral continua aberta. O
[acompanhamento da VM](../operations/VM_REPRODUCTION_PROGRESS.md) é a referência
dos aceites e limites; o [histórico](ROADMAP_HISTORY.md) registra a decisão.
Os aceites do ambiente original e da VM permanecem separados.

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

| Etapa na VM `fraud-repro` | Estado |
| --- | --- |
| Ubuntu, Docker, Python/Poetry, KinD, kubectl, Helm e inotify | Instalados e verificados |
| Fundação PostgreSQL/RustFS e tracking | Verificados nativamente |
| Aquisição Bronze e construção Silver/Gold | Verificadas nativamente |
| Treinamento, Registry, API e rollback da rodada `repro-v1` | Verificados nativamente |
| Fonte de eventos da sessão aceita | Sessão reconciliada com 269.334 eventos; o ledger de contexto não representa inferências |
| Sessão HTTP na VM | Verificada: 200 decisões, 13 alertas, 199 clientes e 200 feedbacks nas decisões |
| Grafana da VM | Cinco dashboards; métricas verificadas, expiração/retomada ensaiadas e revisão visual recebida |
| Portal da VM | Sessão/snapshot, cinco explicações verificadas, desempenho de 67.255 linhas, smoke e navegação nativa aceitos; captura SHAP revisada; integrado no PR #16 |
| Processos de observação/acesso no host | Unidades ativas e recuperação automática de seus processos ensaiada; check posterior manteve PIDs e hashes |
| Logout controlado | Validado por confirmação do autor de encerramento de todas as sessões/túneis, Linger=yes e check posterior com mesmos PIDs/contadores/hashes |
| Reboot planejado da VM | Boot ID mudou; observador e acesso Grafana ativos, HTTP saudável e mesma sessão; uma nova tentativa automática por unidade |
| Ciclo planejado da plataforma na VM | stop/start/verify aceitos, mesmo nó e volumes, green em zero; serviços do host e portal retomados |
| Backup/restauração na VM | Pendente; parada/subida e reboot não substituem recuperação de backup |
| Reprodução completa do produto | Pendente |

## Backlog ordenado

| Ordem | Recorte | Critério de conclusão |
| --- | --- | --- |
| 1 — em andamento | Concluir reprodução limpa | Consolidar os marcos já aceitos, portal aceito no PR #16; integrar o ciclo e qualificar instalação do zero; recibos próprios e limitações explícitas |
| 2 — parcialmente validado | Consolidar operação da VM | Observador/acesso Grafana supervisionados e recuperação ensaiada; logout completo e reboot planejado aceitos; concluir inventário restante, acessos SSH e limites de recursos |
| 3 | Decidir coordenação dos pipelines | Testar fluxo limitado com falha/retomada e invocação duplicada; comparar executor existente e uma alternativa; adotar orquestrador somente se reduzir complexidade total |
| 4 | Terraform local | Declarar ambiente estabilizado e ownership; providers/locks/state protegidos, apply real e segundo plan sem mudanças; preservação/restauração dos dados explícitas |
| Opcional, após consolidação local | Aprendizado e POC temporária em nuvem | Confirmar elegibilidade/plano, serviços disponíveis e limite financeiro; escopo mínimo, execução verificável e remoção conferida; sem hospedagem permanente obrigatória |
| Condicionado | Sucessor do champion | Candidato qualificado, autorização, ativação/rollback com pesos diferentes e recuperação coerente; gates preservados |
| Projeto separado, posteriormente | Retomar portfólio de energia | Revisar pipeline de treinamento e reprodução em VM; usar o resultado para rever o esboço Terraform daquele repositório |

## Próxima entrega concreta

Conferir os processos e acessos restantes após integrar a supervisão no PR #14
e aceitar logout completo e reboot planejado da VM. O observador e o encaminhamento Grafana já têm unidades de
usuário, status e journal, com recuperação automática verificada. O túnel SSH
continua separado, sem reconexão automática neste recorte.

Publicar/integrar o aceite do portal e qualificar o percurso de instalação
integral a partir de checkout vazio. Publicar o aceite do ciclo planejado e
preparar backup/restauração como ensaio próprio. Novas chegadas e firing de
alertas de monitoramento na VM permanecem fora do aceite da sessão concluída.
Nenhum desses resultados cria um novo holdout ou autoriza substituir o champion.

Terraform permanece posterior à estabilização desse percurso. A ampliação da
reprodução tem entregas e gates delimitados; não exige broker, feature store ou
orquestrador adicional para ser encerrada.

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
