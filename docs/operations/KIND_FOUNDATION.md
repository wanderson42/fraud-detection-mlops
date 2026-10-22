# Fundação local em KinD + Helm

<!-- kind-foundation-acceptance-2026-10-20:start -->
**Estado em 2026-10-20 — fundação KinD/Helm aprovada.** PostgreSQL e RustFS
prontos; marcadores SQL/S3 preservados após recriar os pods, com os mesmos PVCs.
Regressão nativa: 45 testes aprovados. Bootstrap corrigido após ajuste de
inotify no host; persistência desse ajuste após reboot ainda não confirmada.
A fundação foi integrada ao MLflow; o aceite e o próximo recorte estão registrados abaixo.
[Aceite, pré-requisito e limites](KIND_FOUNDATION_ACCEPTANCE.md). As notas anteriores mantêm seu contexto histórico.
<!-- kind-foundation-acceptance-2026-10-20:end -->


Primeiro incremento de 2026-10-20, preparado a partir do merge `4abee42`.
Templates/testes controlados validados; criação do cluster, permissões dos
volumes, readiness e persistência nativas aguardam execução do autor.

## O que vamos provisionar primeiro

Um cluster chamado `fraud-platform`, com um node, namespace `fraud-platform`
e release Helm de mesmo nome. O chart `infra/helm/fraud-platform` contém
RustFS e PostgreSQL, uma réplica de cada, Services internos e dois PVCs novos.
É a fundação do fluxo completo; os consumidores ainda não mudam de destino.

| Componente | Destino neste incremento | Limite do aceite |
| --- | --- | --- |
| RustFS | PVC `fraud-platform-rustfs-data`, 4 GiB solicitados, `/data` | Pequeno objeto de teste em bucket exclusivo por execução |
| PostgreSQL | PVC `fraud-platform-postgres-data`, 2 GiB solicitados, `/var/lib/postgresql/data` | Banco novo `fraud_platform` e tabela de marcadores |
| Configuração local | `data/storage/kind-platform/` | Credenciais privadas, kubeconfig próprio, identidade do cluster, caches e recibos |
| Estado dentro do node | PVs locais provisionados pelo StorageClass `standard` do KinD | Sobrevivência à recriação dos pods; não à exclusão do node/cluster |

Os 6 GiB são pedidos de PVC, não reserva física ou limite de disco garantido
do host pelo provisionador local. Os limites dos dois serviços somam 1,5 GiB
de RAM e duas CPUs; control plane, rede, provisionador e ferramentas consomem
recursos adicionais. Um node não oferece alta disponibilidade.

O controlador não monta os volumes Compose nem lê Bronze, Silver, Gold,
checkpoints, modelos ou tracking históricos. O projeto de energia, outros
clusters e a stack atual continuam sob seus próprios controles.

## Versões e escolha explícita do node

O autor informou Docker 29.1.3, KinD `v0.34.0-alpha+aa74c7f3e55dba`, kubectl
1.35.9 e Helm 4.3.1. Não usar o node padrão implicitamente: um server 1.37
ficaria duas versões minor à frente do cliente informado. A política do
Kubernetes admite diferença de uma minor entre kubectl e API server.

Este recorte fixa `kindest/node:v1.35.8` com digest publicado nas notas do KinD
0.33.0. Helm 4.3.x suporta Kubernetes 1.34–1.37; o chart limita este primeiro
aceite a 1.35.x. O controlador confere a versão real do node e registra as
versões dos clientes. A instalação alpha do autor pode ser ensaiada com esse
node explícito; isso não é promessa de compatibilidade universal. Para uma
receita futura em CI, preferir release estável de KinD fixada, conforme upstream.
Nenhuma instalação global é trocada automaticamente.

As imagens RustFS 1.0.1 e PostgreSQL 17.11 são as mesmas já fixadas no projeto,
com digest; a ferramenta de teste S3 também usa o pin existente. O arquivo
[images.lock.json](../../infra/kind/images.lock.json) registra as referências.
Testes de Helm não comprovam a execução dessas imagens dentro de Kubernetes.

Fontes oficiais:

- [KinD quick start e kubeconfig separado](https://kind.sigs.k8s.io/docs/user/quick-start/).
- [Imagens publicadas no KinD 0.33.0](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0).
- [Política de versões Kubernetes](https://kubernetes.io/releases/version-skew-policy/).
- [Compatibilidade Helm 4](https://helm.sh/docs/topics/version_skew/).

## Identidade, credenciais e permissões

O kubeconfig deste fluxo fica em `data/storage/kind-platform/kubeconfig`.
Todos os comandos Kubernetes/Helm do controlador usam esse arquivo, contexto
e namespace explícitos. Não muda o contexto de `~/.kube/config`. Os caches
Helm e de descoberta Kubernetes também apontam para o workspace gerenciado.
Imagens e dados internos dos nodes continuam no armazenamento do Docker;
essa fronteira externa é declarada no catálogo, não varrida como arquivo local.

`init` gera três credenciais aleatórias em arquivo modo 600, dentro de diretório
modo 700; uma segunda chamada preserva seus bytes. Não usa os `.env` do Compose.
O Secret é criado pela API com entrada por stdin; senhas não entram em argumentos
de shell, `values.yaml`, templates ou histórico das releases Helm. O controlador
recusa rotação implícita e a adoção de cluster/namespace com identidade diferente.
Não versionar, publicar como artifact nem incluir credenciais/kubeconfig em
bundles de dados. São credenciais administrativas desta bancada, não contas
restritas de aplicações; o papel do MLflow será tratado no incremento próprio.

Secrets Kubernetes e arquivo local não são um cofre: o operador e administradores
do cluster podem acessá-los. Esta bancada usa a rede local interna sem TLS entre
pods. Services não expõem portas no host; o API server liga em loopback.

Os pods rodam sem root, sem token de ServiceAccount, sem capabilities, com
filesystem raiz somente leitura, `seccomp` padrão, probes e recursos definidos.
Volumes de dados usam `fsGroup`; `/tmp`, logs e socket do PostgreSQL têm
volumes efêmeros próprios. A aceitação das permissões depende do ensaio nativo.

## Executar na branch `feat/kind-helm-serving`

Na raiz do checkout atualizado:

```bash
.venv/bin/python -m tox -e py314 -- tests/infra

poetry run python -m scripts.platform.kind_foundation validate

poetry run python -m scripts.platform.kind_foundation init

poetry run python -m scripts.platform.kind_foundation create

poetry run python -m scripts.platform.kind_foundation up

poetry run python -m scripts.platform.kind_foundation status
```

O comando `create` pode baixar a imagem do node e aguardar o control plane;
`up` pode baixar os serviços. Erros interrompem a sequência. Não pular comandos
que falharam. As operações seguintes usam lock local exclusivo. Não executar
outros comandos mutantes nesse cluster enquanto o ensaio estiver ativo.

Sem Helm 4.3.x, os testes que renderizam templates são ignorados. Por isso o
comando `validate` é um gate obrigatório no host; ele não ignora Helm ausente.
O restante dos testes não precisa de cluster e não inicia Docker.

Se `status` mostrar ambos os pods prontos e PVCs Bound, executar:

```bash
poetry run python -m scripts.platform.kind_foundation rehearse
```

O ensaio grava um marcador SQL e um objeto S3 exclusivo; confere a leitura,
recria somente os dois pods próprios, espera readiness e relê os marcadores.
Exige UIDs novos para os pods e os mesmos UIDs/PVs para os PVCs. O Pod auxiliar
S3 usa credenciais por Secret e é removido depois de cada verificação; marcador
SQL e bucket sintético ficam preservados como evidência identificável no recibo.
Não altera workloads de outros namespaces nem remove PVC, release ou cluster.

O resultado é salvo como `data/storage/kind-platform/foundation-<id>.json`,
inclusive em falhas do ensaio. `success` só é emitido após as duas releituras e
as verificações de identidade. Preserve o recibo e envie a saída para o aceite.

## Diagnóstico sem apagar os volumes

O controlador não imprime manifests de Secrets nem dump de ambiente. Para
readiness ou download de imagem, `status` apresenta fases/estados dos containers.
Também podem ser usadas estas leituras explícitas:

```bash
kubectl --kubeconfig data/storage/kind-platform/kubeconfig \
  --context kind-fraud-platform -n fraud-platform get pods,pvc,svc

kubectl --kubeconfig data/storage/kind-platform/kubeconfig \
  --context kind-fraud-platform -n fraud-platform get events \
  --sort-by=.lastTimestamp

kubectl --kubeconfig data/storage/kind-platform/kubeconfig \
  --context kind-fraud-platform -n fraud-platform logs \
  statefulset/fraud-platform-rustfs --tail=80
```

Se a criação interromper antes de gravar `owner.json`, o controlador recusa
adoção automática. Preserve workspace e cluster e envie a falha; não remova o
arquivo de identidade nem exclua o cluster para contornar o bloqueio.

## Persistência e sequência do fluxo completo

Os PVCs possuem `helm.sh/resource-policy: keep`. Isso reduz exclusão automática
por Helm, mas **não é backup**. Apagar namespace, PVC, node ou cluster pode
destruir dados. Esta entrega não inclui comandos de remoção, restore automático
nem prova de recuperação após perda de disco/máquina. Não substituir volumes
atuais por estes até validar um procedimento próprio de transferência/restore.

| Próxima integração | Gate antes de trocar a origem primária |
| --- | --- |
| Objetos Bronze–Silver–Gold e referências | Transferir bundles verificados para o novo S3 e conferir integridade/contratos no destino |
| Tracking | Aplicação com papel PostgreSQL restrito, artifacts S3, recarga pelo proxy e recuperação própria |
| Jobs de dados/modelagem | Entradas e destinos explícitos, versões, orçamento e recibos preservados |
| Replay e estado | Leitor/escritor único, checkpoints identificados, recuperação e paridade causal |
| Serving | Consumir a imagem qualificada, release identificada, probes, troca blue-green e rollback |
| Observabilidade | Coleta do estado suportado, regras/painéis e persistência conferidos no cluster |

Nenhuma linha acima está concluída por provisionar apenas os datastores.
CT, feature store e broker continuam condicionais. Simplificação e fechamento
funcional precedem Terraform, que gerenciará a infraestrutura estabilizada.

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](KIND_TRACKING.md#aceite-nativo--2026-10-20).

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.
