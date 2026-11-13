# IaC local: primeiro incremento de implementação

Escopo definido em 2026-11-04 sobre main `4f6a9f6`; primeiro incremento aceito
nativamente em 2026-11-12, conforme saídas fornecidas pelo autor. O CI #112 foi
o da integração documental anterior, não o aceite da nova implementação.
As seções de preparação abaixo preservam seu estado à época; o aceite atual
está na seção seguinte.

## Aceite nativo — 2026-11-12

**Marco concluído:** fundação PostgreSQL/RustFS administrada por Terraform em
namespace próprio, no cluster KinD já existente. Implementação `d4fefa0`,
correção delimitada de labels `2ea1ff5`. O autor confirmou merge do PR #26 em
`e26ee20` e main sincronizada no cliente e na VM. Nenhum resultado específico
de CI é inferido do registro de sincronização.

| Gate | Resultado fornecido pelo autor |
| --- | --- |
| Providers/configuração | Terraform 1.17.1, Kubernetes 3.4.0 e Helm 3.3.0 validados na VM |
| Recursos novos | Dois StatefulSets 1/1, dois PVCs Bound e Services ClusterIP no namespace `fraud-iac-foundation-v1` |
| Plano após correção | Zero creates e três no-ops; equivalência de labels vazios confirmada pelo guard |
| Escrita/leitura SQL e S3 | Readbacks verificados no ambiente próprio |
| Recriação dos pods novos | PostgreSQL e RustFS com UID trocado no recibo do ensaio |
| Persistência | Identidades dos PVCs preservadas |
| Segundo plan | Sem mudanças propostas nos recursos |
| Sessão aceita | Preservada; sem fit ou novas inferências da sessão |

Recibo privado do ensaio:
`data/storage/terraform/local-foundation/v1/verification-385eb4701b6249ef93a3050e8cf1758f.json`.
Recebemos o resumo sanitizado desse recibo, não seus bytes completos; nenhum
SHA256 do arquivo foi informado. A [evidência pública do autor](../../references/evidence/local_iac_author_validation_2026-11-12.json)
registra os identificadores e os limites sem copiar state ou segredos.

A verificação posterior `iac-verify` retornou `pod_recreation_validated=false`
porque não recria pods. Isso não desfaz o recibo anterior do ensaio, que trouxe
`pod_recreation_validated=true`, troca dos dois UIDs e PVCs preservados. O hash
`67ab24e16402c8e099eb0cf523462fd51be025a1eac522cbec0d75a97e11d526`
pertence ao plano sem mudanças dessa verificação posterior, não ao arquivo do
recibo de recriação.

Os PVCs preservados foram PostgreSQL
`b4034a3c-f606-44e0-8f2b-ad23e212cdaa` (2 GiB) e RustFS
`8698b057-0fad-4105-a07f-4fc51ead73cd` (4 GiB), StorageClass `standard`.
O ownership manteve o digest
`810a2e390ae42d297ed049346bf3ccd869e2a9e266d8d1d8d4cdc0c34dad5270`.
Persistência de dados após troca de pods não equivale a backup/restore, recuperação
de host ou HA. O root consome a VM/cluster existentes e não valida AWS ou gestão
integral da plataforma por Terraform.

Não repetir apply, init, treino ou ensaio apenas para publicar este registro.
Preservar state, entradas privadas, ownership e recibos; publicar somente código
e documentação. Para acompanhamento posterior, usar `iac-preflight`/`iac-verify`
conforme o procedimento, sem interpretar um check de leitura como novo ensaio.

## Correção delimitada de labels do Secret — 2026-11-11

Após reboot, a fundação nova foi observada pronta, com dois StatefulSets 1/1 e
PVCs Bound. O diagnóstico do plano mostrou três no-ops, mas resource_drift no
Secret, somente em metadata.labels. UID, nome, namespace, ownership e credenciais
permaneceram iguais. A diferença concreta entre os valores dos labels ainda não
foi recebida; não assumir que qualquer alteração de labels seja inofensiva.

A correção aceita apenas labels ausentes/null/mapa vazio como representações
equivalentes, em plano de três no-ops do ambiente exato. Labels reais, outra
metadata, credenciais, UID, recurso, spec ou alterações desejadas continuam
recusados. O resumo do plano identifica esse caso com
`empty_secret_labels_refresh=true`. O plano e o state não são editados para
suprimir a diferença.

Os dois módulos alterados têm uma transição exata de hashes em
`references/local_iac_labels_source_transition_v1.json`. Para usar o workspace
já inicializado, a transição e os dois módulos precisam estar commitados com os
mesmos bytes revisados. A compatibilidade é calculada em memória: não se regrava
owner.json, credenciais, state, planos anteriores ou a fundação existente.
Qualquer outro source continua protegido pelos pins originais.

Sequência da correção: aplicar os arquivos, executar regressão offline, commit
na mesma branch, depois `make iac-preflight` e `make iac-plan`. Não repetir init
ou o apply original, que foi gerado com state vazio. Após novo plano aceito, o
gate nativo SQL/S3/pods ainda será necessário para concluir o incremento.
Se houver labels materiais, preservar o plano e inspecionar sua origem; não
ampliar a exceção, remover labels ou sobrescrever state para passar no guard.

## Implementação preparada — execução nativa pendente

A configuração está em `infra/terraform/local-foundation/`; os comandos ficam
em `scripts/platform/terraform_foundation/` e `infra/make/terraform.mk`.
Esta revisão fixa Terraform 1.17.1, provider Kubernetes 3.4.0 e Helm 3.3.0,
com lock de providers obtido por init e conferência dos pacotes assinados.
Os nomes da versão inicial são fixos e separados dos nomes da plataforma aceita;
a variável de conexão exige kubeconfig privado absoluto, com contexto explícito.
A implementação não generaliza os controladores existentes.

O wrapper verifica identidade, pins de configuração/chart, state, sessão aceita,
namespace/release e plano exato antes de apply. Na versão inicial, só aceita
creates ou no-ops dos três recursos conhecidos; atualização, drift/importação,
substituição ou exclusão exigem inspeção fora deste fluxo. Planos/credenciais,
state e diagnósticos de falha ficam privados, nunca no stdout do wrapper.
A validação de providers não equivale à autorização de apply.

### Sequência operacional

Primeiro executar os testes e commit da preparação entregue. Na VM, instalar
somente a CLI fixada (não executa Kubernetes ou Terraform apply):

```bash
bash scripts/install_terraform.sh
export PATH="$HOME/.local/bin:$PATH"
terraform version
bash scripts/check_terraform.sh
```

`check_terraform.sh` usa cópia temporária e init sem backend, sem acesso ao
cluster. Exige saída de rede para baixar providers. Um job próprio do CI faz
essa validação. Não usar o root versionado diretamente para apply: ele não contém
credenciais nem a cópia privada do chart; o wrapper prepara ambos.

Em seguida, inicializar apenas arquivos privados, validar/preflight e gerar plano:

```bash
make iac-init IAC_SESSION_ID=clean-install-arrival-v1 &&
make iac-validate &&
make iac-preflight &&
make iac-plan
```

A saída de `iac-plan` é sanitizada: IDs, namespace/release, contagens e digest.
O primeiro plano esperado tem três creates e nenhum update/delete. Conferir esse
resultado antes de aplicar; usar os valores reais emitidos nos argumentos abaixo:

```bash
make iac-apply IAC_PLAN_ID=<ID> IAC_PLAN_SHA256=<SHA256> IAC_NO_OTHER_WRITERS=1
```

Os placeholders devem ser substituídos, não colados literalmente. Aplicar plano
salvo exige a declaração de ausência de outros writers e hash exato; se o state,
os sources ou o destino mudarem, o plano será recusado. Não compartilhar
`private.auto.tfvars.json`, JSON de plan completo, state ou failure-*.json:
eles podem conter senhas e respostas dos providers.

Após apply, executar o gate nativo explícito:

```bash
FRAUD_IAC_NO_OTHER_WRITERS=1 .venv/bin/python -m tox -e local-iac
make iac-verify
```

O tox cria marcadores próprios e recria somente os dois pods da fundação nova.
`iac-verify` posterior lê os marcadores existentes, verifica volumes/sessão e gera
um novo plan sem alterações; ele não recria os pods. Ambos usam pods auxiliares
próprios para o readback S3. Recibos privados ficam no workspace IaC. Uma falha
preserva state e evidências; não apagar diretórios ou gerar outra instalação para
contornar o diagnóstico. A versão inicial não oferece target destroy.

Nesta preparação local, fmt, parsing HCL, init dos providers e testes sem serviços
foram executados. O validate completo dos providers ficou bloqueado por proibição
de sockets de plugins no executor do assistente; precisa passar na VM/CI.
Não houve apply no cluster nem aceite nativo Terraform nesta entrega.

## Objetivo e fronteira

Demonstrar gestão declarativa e persistência de uma fundação PostgreSQL/RustFS
nova, usando o chart existente. O primeiro root Terraform consumirá o cluster
KinD já disponível na VM como pré-requisito. Não provisionará a VM, Docker ou o
cluster e não adotará a instalação aceita. O laboratório completo ainda não será
administrado por Terraform: esse limite faz parte do aceite.

O novo namespace será `fraud-iac-foundation-v1`, a release `fraud-iac-foundation`
e o Secret `fraud-iac-foundation-credentials`. O namespace atual `fraud-platform`,
suas releases e seus dados permanecem fora do state novo. Os nomes propostos
serão recusados no primeiro apply se já existirem sem ownership registrado;
não haverá importação ou adoção automática.

O isolamento é de nomes, ownership e volumes, no mesmo cluster. Ele não implica
isolamento de CPU, memória, rede, falhas ou host. Não se cria outra VM ou outro
cluster para este incremento. O preflight terá de medir capacidade disponível
antes de autorizar novos pods; requests declarados não medem consumo real.

## Recursos e responsáveis

| Recurso | Quem declara/altera | Observação |
| --- | --- | --- |
| VM, Docker, KinD e StorageClass existente | Setup/controlador da fundação atual | Somente pré-requisitos lidos pelo root Terraform |
| Novo namespace | Provider Kubernetes via Terraform | Nome próprio e identidade registrada |
| Novo Secret com três chaves do chart | Provider Kubernetes via Terraform | Credenciais próprias, sem copiar as do ambiente aceito |
| Nova release do chart `infra/helm/fraud-platform` | Provider Helm via Terraform | Terraform é a entrada de gestão da release; não executar helm upgrade manual nela |
| StatefulSets, Services e PVCs novos | Helm como parte dessa release | Não declarar os mesmos objetos como recursos Kubernetes separados em Terraform |
| Pods e reconciliação dos StatefulSets | Kubernetes | Terraform não substitui a supervisão do cluster |
| Dados de verificação SQL/S3 | Ensaio nativo específico da IaC | Marcadores sintéticos, sem modelos, datasets ou tabelas da instalação aceita |
| API, MLflow, monitoramento e sessão aceitos | Controladores atuais, fora deste incremento | Não apontar esses controladores para a fundação nova |

A revisão do código encontrou `NAMESPACE`/`RELEASE` fixos em
`scripts/platform/kind_foundation.py` e o endpoint
`fraud-platform-postgres` em `scripts/platform/kind_tracking.py`. Portanto, a nova
fundação não será apresentada como substituta pronta do backend do produto.
Generalizar todos os controladores não faz parte deste primeiro incremento.

O chart usa `.Release.Name` e `existingSecret`; todos os templates atuais são
recursos com escopo de namespace. Preservar suas imagens por digest, versões,
probes e contratos. Os valores atuais reservam 2 GiB de PVC para PostgreSQL e
4 GiB para RustFS; requests de memória somam 384 MiB e limites somam 1,5 GiB.
Esses números são declarações do chart, não tamanho físico ou consumo medido.

## Código a implementar

- Um root em `infra/terraform/local-foundation/`, com providers Kubernetes e
  Helm fixados e `.terraform.lock.hcl` versionado. Fixar também a versão CLI
  utilizada no ensaio; não resolver versões implicitamente em cada máquina.
- Conexão por kubeconfig privado e contexto explícitos da instalação local,
  conferindo a identidade do cluster/node antes de operações. Não usar o contexto
  global como fallback nem modificar `~/.kube/config`.
- Nomes fixos de namespace/release na versão inicial e workspace privado validado,
  recusando adoção da instalação aceita e troca de destino após inicialização.
- Entradas Make para validate, preflight, plan, apply e verify, com descrições
  objetivas. Nenhum apply implícito durante instalação de dependências ou testes.
- Um ensaio opt-in via pytest/tox para os gates abaixo. CI comum validará formato,
  sintaxe e recusas sem criar cluster ou exigir credenciais cloud.

O chart precisa ser fixado pelo conteúdo/commit consumido. Alterar um chart local
sem alterar seu número de versão não pode passar despercebido no workflow.
A implementação verificará compatibilidade das versões dos providers com o
cluster Kubernetes e com o chart antes do aceite.

## Credenciais, state e plano

As chaves exigidas são `postgres-password`, `rustfs-access-key` e
`rustfs-secret-key`. Gerar novas credenciais uma única vez para o ambiente IaC;
não rotacionar senha automaticamente a cada execução, pois PVCs preservam dados.
Não passar senhas em argumentos do shell nem publicar valores em outputs/logs.

State, planos salvos, entradas privadas e backups do state ficarão sob um diretório
privado por ambiente em `data/storage/terraform/local-foundation/`, com diretório
0700, arquivos 0600 e umask restrita. A implementação deve registrar essa nova
fronteira no catálogo de persistência e nas exclusões Git. O plano poderá conter
segredos mesmo quando a tela os oculta; não publicar JSON de plan bruto como
artefato de CI. Relatórios públicos conterão identidades e resultados sanitizados.

Neste escopo local de operador único, aceitar state local como sensível. O
provider Kubernetes pode persistir credenciais do Secret no state; marcar um
valor como sensitive não o criptografa. Guardar cópia protegida do state e de suas
entradas antes de mudanças. Uma futura execução por equipe/CI exigirá backend,
controle de acesso, criptografia e locking apropriados, com nova qualificação.

Antes de apply: gerar plano salvo, conferir identidade/destino e recusar deleções,
substituições e operações fora do namespace/release permitidos. Aplicar exatamente
o plano revisado; preservar falhas parciais e state para inspeção, sem reiniciar
outro ambiente para esconder a falha.

Não oferecer destroy como encerramento cotidiano. Usar proteções de lifecycle
onde cabíveis, sabendo que prevent_destroy não protege contra remoção da própria
configuração nem contra exclusão externa. PVCs incluídos na release também não
recebem proteção individual de um recurso helm_release. Uma eventual remoção
exige procedimento separado, inventário e decisão explícita sobre dados novos;
ela não participa do teste inicial.

## Aceite nativo: conclusão finita do primeiro incremento

1. Antes: conferir espaço/memória, identidade do cluster e saúde da sessão
   `clean-install-arrival-v1`; registrar hashes e identidades de recursos aceitos.
2. Validar configuração e gerar plano: criar somente namespace, Secret e release
   novos; exibir resumo sem segredos e confirmar ausência de adoção/substituição.
3. Aplicar o plano revisado: PostgreSQL/RustFS prontos, PVCs Bound, endpoints
   privados e imagens esperadas. Registrar versões e identidade do novo ambiente.
4. Gravar marcador SQL e objeto S3 próprios; obter leituras verificadas.
5. Recriar somente os pods da fundação nova, um por vez, e conferir marcadores e
   os mesmos UIDs de PVC/volumes. Isso comprova persistência entre pods, não backup.
6. Executar novo plan com refresh: nenhuma mudança. Repetir verificação sem gerar
   outra release, novas credenciais ou novos volumes.
7. Depois: sessão/champion aceitos, seus hashes e recursos protegidos continuam
   preservados; nenhuma inferência ou treinamento novo. Não exigir PIDs iguais
   para serviços supervisionados que possam reiniciar legitimamente.

Ao concluir estes gates, registrar **fundação local gerida por Terraform aceita**.
Não condicionar o fechamento a migrar toda a plataforma, fazer outro treinamento,
restaurar todos os bancos, provar HA ou contratar nuvem. Uma falha nativa deixa o
aceite pendente e deve ser inspecionada, sem descartar state/recibos.

## Relação com a AWS e próximo limite

| Camada | Primeiro incremento local | Referência AWS do roadmap |
| --- | --- | --- |
| Computação | VM e nós KinD preexistentes, fora deste root | EC2 em grupo de nós gerenciado do EKS; futuro root de infraestrutura |
| Kubernetes | Cluster KinD consumido por contexto explícito | Control plane EKS, rede e permissões; ainda não implementados |
| Workloads/armazenamento | Fundação de estudo instalada por Terraform/Helm | PostgreSQL pode ir para RDS e objetos para S3; não copiar este root como equivalente desses serviços |

O ganho transferível é ownership único, grafo de dependências, state protegido,
plan revisado e verificação pós-apply. O primeiro incremento não valida compute
cloud. Rede/IAM, capacidade, custos e recuperação AWS permanecem no plano de
portabilidade do [roadmap](../project/ROADMAP.md). Não usar Terraform como executor
de treino/sessão nem embrulhar todos os scripts existentes em local-exec.

## Fontes e referências

Revisão local: `scripts/platform/kind_foundation.py`,
`scripts/platform/kind_tracking.py` e `infra/helm/fraud-platform/` em `4f6a9f6`.
Documentação oficial consultada em 2026-11-04:

- [Provider Helm e conexão Kubernetes](https://registry.terraform.io/providers/hashicorp/helm/latest/docs).
- [Dados sensíveis em state e planos](https://developer.hashicorp.com/terraform/language/manage-sensitive-data).
- [Limites de prevent_destroy](https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle).
