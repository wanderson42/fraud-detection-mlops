# Roadmap — Fraud Detection MLOps

Revisão em **2026-11-13**, após [PR #27](https://github.com/wanderson42/fraud-detection-mlops/pull/27)
integrado em `b75fa3ad963fba019ec643db19fd5240db7d4238`. CI #117 (PR) e #118
(main) aprovados, conferidos no GitHub. O autor confirmou main sincronizada no
Alienware; a VM do ensaio conserva `e26ee20` como revisão de execução.

## Estado atual

O produto, a reprodução limpa e os incrementos operacionais abaixo estão
concluídos **nos escopos demonstrados**. A integração da criação/setup de VM
não permanece no backlog. Esta revisão organiza a apresentação e os limites;
não reexecuta ciência, infraestrutura, sessões ou aceites.

| Marco concluído | Evidência e fronteira |
| --- | --- |
| Produto e rodada canônica | Dados identificados, três candidatos rastreados, champion/API e rollback; [rodada](../operations/OPERATIONAL_ROUND.md) |
| Sessão HTTP e investigação | Features causais, decisões/triagem/feedback reconciliados, portal e SHAP; [sessão](../operations/PRODUCT_SESSION.md) e [portal](../operations/UNIFIED_PORTAL.md) |
| Monitoramento e recuperação | Mesma sessão no Grafana/Prometheus, recuperação de processos e reboot no escopo de cada VM; [aceites](../operations/VM_REPRODUCTION_PROGRESS.md) |
| Reprodução limpa | Produto integral na segunda VM com dados, modelos e ownership próprios; [runbook](../operations/CLEAN_INSTALL_RUNBOOK.md) |
| Retomada Silver/Gold | Jobs identificados e resultados reutilizados; PR #24 `730e2d4`; [contrato](../operations/PIPELINE_RECOVERY.md) |
| Fundação Terraform isolada | Namespace/Secret/release próprios, SQL/S3, pods trocados, PVCs preservados e plano sem mudanças; PR #26 `e26ee20`; [IaC](../operations/LOCAL_IAC.md) |
| VM e setup | Prévia/criação desligada, Ubuntu/OpenSSH/LVM manuais, fases de setup na terceira VM; PR #27 `b75fa3a`; [aceite](../operations/VM_PROVISIONING.md) |
| Acesso portátil | Túneis com portas livres, conflito recusado, reutilização e encerramento só do acesso; [guia](../operations/VM_ACCESS.md) |
| Backup limitado de tracking | Captura, cópia externa e restauração isolada delimitadas; [escopo](../operations/VM_TRACKING_BACKUP.md) |

Os mesmos aceites não se aplicam automaticamente a todas as máquinas. A terceira
VM não recebeu novo cluster/produto; não repetir treinamento para fechar seu
setup. O perfil LTS foi ajustado manualmente na VM criada. O PATH foi resolvido
manualmente e confirmado em nova sessão; a persistência automática posterior
foi testada offline, sem nova execução nativa do setup corrigido.

## Próximos passos proporcionais ao portfólio

| Prioridade | Recorte | Critério para iniciar ou concluir |
| --- | --- | --- |
| Revisão atual | Consolidar leitura e CI proporcional | Leitura aponta aos mesmos aceites; Markdown e células Markdown recebem checks leves. Mudança do workflow mantém validação completa; CI ainda precisa de aceite no GitHub |
| Uso contínuo | Estudar, apresentar e manter | Demonstrar uma instalação aceita, explicar decisões e registrar falhas reais; corrigir defeitos ou divergências observados |
| Condicionado | Ativar sucessor com pesos diferentes | Candidato passa gates, aprovação explícita e ensaio de ativação/rollback; retenção atual não é pendência artificial |
| Sob demanda | Qualificações adicionais | Logout completo da segunda VM, execução nativa do setup corrigido, consumo por container, HA ou restore integral só entram por objetivo próprio |
| Opcional | Estudo ou POC temporária AWS | Arquitetura/custo/região e limite financeiro definidos; nenhum recurso é contratado por este roadmap |
| Projeto separado | Retomar portfólio de energia | Revisar aquele pipeline e sua reprodução; sem reabrir marcos de fraude já aceitos |

A [política de CI proporcional](../operations/TESTING.md#ci-proporcional-ao-tipo-de-mudança--preparação-em-2026-11-13)
está preparada na mesma revisão: PR/main/manual, sem rodada duplicada de push
em branch de trabalho. Código, contratos e workflow continuam com os gates
completos; nenhuma execução científica ou de infraestrutura foi repetida localmente
para essa preparação. O CI dessa mudança executável ainda precisa ser conferido.

Não há obrigação de migrar hipervisor, adotar provider VirtualBox, Packer,
Ansible, broker, feature store, GPU ou outro orquestrador. A prioridade profissional
é saber reproduzir, avaliar e explicar o sistema. Cada novo componente precisa
resolver uma necessidade concreta e justificar seu custo de manutenção.

## Correspondência com AWS: estudo sem implantação

| Papel local | Referência AWS | Lacunas para uma implantação real |
| --- | --- | --- |
| VM Ubuntu | EC2 como computação de nós | Capacidade, imagem, bootstrap e custo; não copiar a VM inteira |
| KinD | Control plane EKS e grupo de nós | Rede, IAM, acesso e disponibilidade; cluster e workloads têm etapas distintas |
| PostgreSQL | RDS PostgreSQL ou banco operado no cluster | Conexões, consistência, backup e restauração |
| RustFS com API S3 | Amazon S3 | Identidade, políticas, endpoints e integridade |
| Workloads e observabilidade | Containers/Jobs e serviços escolhidos | Imagens, saúde, acesso autenticado, persistência e retenção |

São hipóteses de arquitetura; ECS/Fargate é alternativa de estudo, não decisão
implementada. Portabilidade exige adaptar identidade, rede, armazenamento,
coordenação e operação; trocar um provider não faz isso. Terraform administra
somente recursos com ownership definido. Persistência após recriar pod não é
backup; guard de destruição não é recuperação de desastres. O projeto local
continua utilizável sem conta paga. [Plano de aprendizado cloud](../operations/CLOUD_LEARNING.md).

## Critérios de evidência e encerramento

Preservamos dados, modelos, protocolos, recibos e células anteriores. Teste
sintético, execução nativa, revisão visual e CI são verificações distintas.
Repetir uma janela observada não confirma qualidade independente nem autoriza
abrir outra reserva de avaliação. Não foram demonstrados produção bancária,
HA, SLA, restore integral ou perda física do host.

Use o [roteiro de demonstração](../operations/PORTFOLIO_WALKTHROUGH.md), a
[arquitetura](../ARCHITECTURE.md) e o [histórico de decisões](ROADMAP_HISTORY.md)
para apresentar e aprofundar o trabalho. Novas implementações dependem de lacuna
concreta; o encerramento de um marco não exige inventar outro requisito.

## Registros históricos preservados

As notas abaixo conservam o texto anterior à revisão de 2026-11-13. Datas,
pendências e próximos passos nelas pertencem aos respectivos recortes; para o
estado atual, use a parte principal deste documento e o roadmap.

<details>
<summary>Abrir registros anteriores à consolidação do portfólio</summary>

# Roadmap — Fraud Detection MLOps

Revisão em **2026-11-12**. O fechamento funcional ocorreu em
2026-10-31 (America/Belem); os registros anteriores de boot
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

## Marcos aceitos no ambiente original e na primeira VM

As linhas com “na VM” desta seção referem-se à primeira VM `fraud-repro`.
Os aceites da segunda VM estão na tabela seguinte; não transferir identidades
ou a confirmação de logout entre ambientes.

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
| Logout completo na VM nova | Não validado: o autor confirmou que havia túnel aberto; limite deste ensaio, sem invalidar o reboot |
| Túneis SSH com portas automáticas | Aceitos nativamente: abertura, reutilização, conflito e stop preservando serviços/sessão |
| Scripts de setup por fases | A segunda VM conserva o aceite manual. Na terceira VM, fases executadas em `e26ee20`, com ajuste manual de PATH; correção automática posterior qualificada apenas offline |
| Backup/restore integral e perda física | Fora do aceite; tracking limitado já tem evidência própria anterior |
| Retomada identificada Silver/Gold | Aceite nativo concluído; PR #24 integrado em `730e2d4` e main sincronizada nas duas máquinas, conforme informado pelo autor |
| Plano inicial de IaC e correspondência cloud | Integrado no PR #25 em `4f6a9f6`; separa computação EC2, control plane EKS e alternativas não provisionadas |
| Primeira fundação Terraform local | Aceite nativo confirmado: namespace/Secret/release próprios, SQL/S3, pods trocados, PVCs preservados, plano sem mudanças e sessão aceita preservada; [aceite](../operations/LOCAL_IAC.md#aceite-nativo--2026-11-12) |

## Backlog ordenado

| Ordem | Recorte | Critério de conclusão |
| --- | --- | --- |
| Concluído | Integrar a fundação Terraform local | PR #26 integrado em `e26ee20`; main sincronizada no cliente e na VM, conforme terminal do autor. Não inferir resultado específico de CI deste registro |
| Fechamento atual | Integrar criação de VM e setup aceitos | Prévia/criação, Ubuntu manual, SSH e fases do setup aceitos na terceira VM; registrar e publicar a revisão final, conferir CI/PR/merge. Correção automática de PATH tem testes offline, sem nova execução nativa |
| Sob demanda, fora do fechamento | Qualificações operacionais adicionais | Logout completo da segunda VM exigiria outro ensaio sem conexões abertas; setup automatizado, medição por container, HA e restore integral têm critérios próprios e não reabrem a instalação manual aceita |
| Opcional, após consolidação local | Aprendizado e POC temporária em nuvem | Confirmar elegibilidade/plano, serviços disponíveis e limite financeiro; escopo mínimo, execução verificável e remoção conferida; sem hospedagem permanente obrigatória |
| Condicionado | Sucessor do champion | Candidato qualificado, autorização, ativação/rollback com pesos diferentes e recuperação coerente; gates preservados |
| Projeto separado, posteriormente | Retomar portfólio de energia | Revisar pipeline de treinamento e reprodução em VM; usar o resultado para rever o esboço Terraform daquele repositório |

## Próxima entrega concreta: integrar criação de VM e setup aceitos

A [primeira fundação](../operations/LOCAL_IAC.md#aceite-nativo--2026-11-12)
está aceita nativamente sobre a implementação `d4fefa0` e a correção `2ea1ff5`.
A evidência confirma SQL/S3, troca dos dois pods, os mesmos PVCs, plano sem
mudanças e preservação da sessão aceita. O plano revisado após a correção tinha
zero creates e três no-ops; a normalização de labels vazios foi qualificada pelo
guard, sem reescrever ownership ou state.

O autor confirmou merge do PR #26 em `e26ee20` e main sincronizada nas duas
máquinas. A fundação está integrada; não repetir os ensaios aceitos. Este registro
não afirma um resultado de CI que não foi fornecido.

A [criação de VM e o setup](../operations/VM_PROVISIONING.md#aceite-do-autor--2026-11-12)
foram aceitos pelo autor na terceira VM `fraud-bootstrap-v1`. A criação desligada
veio de `21bbe96`; o perfil LTS foi corrigido em `39d97e3` e ajustado na VM
existente. Ubuntu, OpenSSH e expansão LVM permaneceram manuais. As fases do
setup rodaram em `e26ee20`. O PATH inicialmente apontou ao Poetry 1.8.2 do
sistema; ajuste manual e nova sessão confirmaram Poetry 2.4.3 e uv 0.13.8.
A correção posterior persiste PATH em `.profile` e tem testes offline; não
atribuir a ela nova execução nativa. Scripts de setup não ficam mais totalmente
pendentes, mas essa ressalva precisa permanecer explícita.

Falta publicar e integrar a revisão final, com CI e PR conferidos. Não repetir
produto/treino/sessão nesta VM apenas para fechar este recorte. Após integração,
consolidar a leitura do portfólio e só abrir outra implementação diante de uma
lacuna concreta. Provider VirtualBox, outro hipervisor, novo orquestrador e
implantação AWS não são obrigações deste marco.

O escopo é namespace, Secret e release Helm novos no cluster existente. VM,
cluster, API, tracking e observabilidade aceitos ficam fora desse state. O
namespace compartilha capacidade e falhas do cluster; persistência após troca
de pods não comprova backup/restore ou recuperação integral. Após integração,
definir o próximo recorte de IaC por objetivo e custo, sem ampliar o escopo
implicitamente nem transformar a demonstração AWS em obrigação financeira.

### Plano de IaC e portabilidade preservado

O PR #24 foi integrado em `730e2d432f8322098c9b0f6243780f0e327774ae`.
O autor informou sincronização da main no Alienware e na VM. A
[retomada identificada](../operations/PIPELINE_RECOVERY.md#aceite-nativo-da-retomada)
está concluída no escopo aceito; não permanece como desenvolvimento pendente.
Nenhum novo resultado de CI é inferido deste registro de merge.

O critério desta etapa é demonstrar boas práticas de MLOps com evidências,
responsabilidades claras e custo proporcional ao portfólio. A IaC local deve
reproduzir uma infraestrutura delimitada e explicitar um caminho viável para a
nuvem. Um apply local, isoladamente, não valida IAM, rede, disponibilidade,
desempenho ou custo de uma implantação AWS.

### 1. Decisões antes da implementação

A próxima entrega deve resolver e registrar:

- Quais recursos Terraform criará/administrará e quais continuarão sob os
  controladores existentes. Cada recurso terá um único responsável por alterá-lo;
  uma release Helm não poderá ser administrada simultaneamente por Terraform e
  por um controlador imperativo.
- Se o primeiro ambiente IaC será novo e isolado, com nomes, ownership e state
  próprios. Este é o caminho preferido para preservar o laboratório aceito.
  Qualquer adoção de recurso existente exige plano explícito; não se presume
  importação automática do cluster, releases, volumes ou recibos atuais.
- Como providers e suas versões serão fixados, como o state será armazenado e
  protegido e qual será o limite de concorrência. State pode conter dados
  sensíveis: não entra no Git; a configuração local não equivale a backend
  compartilhado com lock para uma equipe.
- Como separar configurações de ambiente dos contratos científicos, mantendo
  dados, modelos, imagens e recibos identificados. Planos local e cloud podem
  compartilhar contratos sem compartilhar state ou declarar recursos idênticos.
- Quais dados precisam ser preservados antes de mudanças e como recuperar o
  ambiente. Destruição não será usada como prova de reprodução sobre a instalação
  aceita; eventuais testes de remoção ficam limitados ao ambiente descartável.

### 2. Divisão de responsabilidades a consolidar

| Camada | Responsabilidade prevista | Fronteira a respeitar |
| --- | --- | --- |
| Terraform | Declarar infraestrutura e dependências do ambiente escolhido | Não treina modelos nem executa sessões; o plano definirá os recursos sob seu controle |
| Helm e Kubernetes | Configuração dos workloads, Services e armazenamento do cluster | Definir quem aplica cada release; supervisão dos pods continua com Kubernetes |
| Controladores de dados/modelos/sessão | Produção, verificação, linhagem, retomada e decisões operacionais | Operação atual qualificada numa VM/checkout; exclusão local não é coordenação distribuída |
| Host e máquina cliente | Setup, supervisão local e acesso do operador | systemd e túneis SSH atendem ao laboratório; acesso e identidade cloud exigem desenho próprio |

A decisão atual é manter os controladores para a cadeia limitada. Agendamento,
múltiplos hosts ou visibilidade central justificariam nova avaliação de
orquestração. Terraform não cria essa capacidade por si só; não há obrigação de
instalar Prefect, Airflow ou outro serviço para concluir a IaC local.

### 3. Correspondência com nuvem: hipóteses de arquitetura

As opções abaixo orientam a análise; não representam arquitetura contratada,
implementada ou validada. A escolha final deverá considerar demonstração
temporária e operação contínua como cenários diferentes.

| Componente atual | Opção AWS a avaliar | O que precisa ser adaptado/verificado |
| --- | --- | --- |
| Computação: VM Ubuntu no VirtualBox | Instâncias EC2 como nós de um grupo gerenciado do EKS | Dimensionar CPU/memória, tipo de instância, capacidade e custo; correspondência de computação, sem copiar a VM inteira ou concentrar todos os serviços numa instância |
| Kubernetes: KinD dentro da VM | Control plane administrado pelo EKS | Rede, identidade, acesso ao cluster e configurações específicas; separar gestão do cluster da computação dos nós |
| Pods, Jobs e workloads Helm nos nós KinD | Pods e Jobs executados nos nós EC2 do EKS | Publicação das imagens, configuração Helm e armazenamento adequados; preservação dos contratos e testes de execução |
| API, portal e servidor MLflow em containers | EKS ou ECS, conforme o desenho | Registro de imagens, execução, saúde, identidade e entrada autenticada; ECS exigiria outra definição de implantação |
| PostgreSQL em StatefulSet | RDS PostgreSQL ou banco operado no cluster | Conexões, migração, permissões, backups e restauração; compatibilidade deve ser testada |
| RustFS com interface S3 | Amazon S3 | Endpoints, IAM, política de buckets e integridade dos objetos; compatibilidade de API não substitui o ensaio |
| PVCs locais hostPath | Armazenamento persistente adequado aos workloads escolhidos | Classes de armazenamento, durabilidade, zona, snapshots e recuperação; não transportar hostPath como estratégia de persistência cloud |
| Prometheus/Grafana | Instalação própria ou serviços gerenciados | Coleta, retenção, alertas, autenticação e custo; gerenciado não é requisito automático |
| Recibos/checkpoints e locks locais | Persistência e coordenação compatíveis com o executor escolhido | Evitar perda do estado em tarefas efêmeras; uma execução distribuída exige novo contrato e aceite |
| Túneis SSH de demonstração | Acesso privado ou entrada HTTPS autenticada | Rede, certificados e controle de acesso; não expor automaticamente todos os serviços |

Para a comparação local × AWS, a referência adotada é **VM → computação EC2**
e **KinD → Kubernetes administrado pelo EKS**. Os grupos de nós gerenciados do
EKS fornecem instâncias EC2 para executar os pods; o control plane é separado.
[Referência oficial dos nós gerenciados](https://docs.aws.amazon.com/eks/latest/userguide/managed-node-groups.html).
Essa referência orienta o estudo e não comprova implantação, capacidade, HA ou
viabilidade financeira. ECS/Fargate continua como alternativa a avaliar, sem
exigir administrar instâncias EC2 diretamente nesse modelo.

O MLflow documenta uma arquitetura AWS com ECS, RDS PostgreSQL e S3;
ela demonstra uma correspondência possível, sem obrigar essa combinação no
projeto. [Referência oficial](https://mlflow.org/docs/latest/self-hosting/deploy-to-cloud/aws/).
Terraform usa recursos específicos de cada provider; portar o ambiente não é
apenas trocar o provider. [Contrato de providers](https://developer.hashicorp.com/terraform/language/providers).

### 4. Critérios de aceite e limites

| Etapa | Evidência exigida para concluir |
| --- | --- |
| Plano de arquitetura | Inventário de recursos, responsável único por recurso, decisão sobre isolamento/adoção, proteção do state/segredos, preservação dos dados e alternativas cloud com lacunas explícitas |
| IaC local implementada | Configuração versionada e validada, versões/lock, plan revisado, apply real no escopo escolhido, segundo plan sem mudanças e verificações funcionais dos recursos administrados |
| Viabilidade cloud documentada | Cenário e região definidos para estimativa, serviços necessários, permissões/rede/armazenamento, custos recorrentes e por uso, estratégia de remoção e limitações dos controladores; sem alegar implantação |
| Eventual ensaio cloud | Escopo e limite financeiro explicitamente autorizados, implantação real temporária, verificações de acesso/persistência/funcionalidade e remoção conferida; somente então registrar aceite cloud |

O plano inicial desta seção antecedeu a fundação Terraform posteriormente
implementada e aceita acima. A comparação cloud continua documental; Não transfere ownership dos recursos existentes, não
altera modelos/sessões e não reabre os ensaios já aceitos. A reprodução local
continua utilizável sem conta paga; HA, restore integral e operação distribuída
permanecem qualificações separadas, não promessas implícitas da IaC.

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

</details>
