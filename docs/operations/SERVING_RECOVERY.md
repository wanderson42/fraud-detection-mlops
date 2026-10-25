# Retomada idempotente da release do serving

O serving instala uma release identificada no volume `/model` antes de iniciar
a API. O instalador agora trata a repetição como verificação: os três arquivos
existentes precisam corresponder exatamente ao manifest, modelo e smoke pinados.
Nesse caso retorna `canonical_release_reused` sem regravar arquivos. Conteúdo
divergente, links e arquivos inesperados são recusados e preservados.

Uma cópia interrompida é publicada por arquivo temporário, fsync e criação
atômica sem sobrescrita do destino. A retomada completa arquivos ausentes e limpa
somente os temporários reservados para aquele manifest, depois de validar o
conteúdo existente. O diretório é bloqueado durante a instalação. Isso cobre
o caso de um init container repetir a instalação sobre o mesmo `emptyDir`.

A documentação do [Kubernetes 1.35 sobre init containers](https://v1-35.docs.kubernetes.io/docs/concepts/workloads/pods/init-containers/)
recomenda essa idempotência porque a inicialização pode ser repetida e arquivos
em `emptyDir` podem já existir.

## Entrega da correção

Não basta editar o script no checkout: a imagem portadora precisa conter o
instalador corrigido. O fluxo pelo Make constrói essa imagem com as mesmas
releases, carrega-a no mesmo node e a testa antes de trocar o instalador ativo.
Runtime da API, pesos, manifest, smoke, versão do Registry, alias e rota são
conferidos e permanecem iguais. Não há treino ou promoção de modelo.

| Comando | Efeito |
| --- | --- |
| `make serving-recovery-prepare SERVING_NO_OTHER_WRITERS=1` | Build da portadora, carga no node e ensaio isolado de reinício do init container; imprime digest para revisão |
| `make serving-recovery-apply SERVING_NO_OTHER_WRITERS=1 SERVING_APPROVAL_SHA256=...` | Troca a portadora do modelo atual pela versão revisada e confere HTTP/Registry |
| `make serving-recovery-verify` | Confere a manutenção concluída, modelo, imagens, rota e resposta HTTP |

O ID padrão é `seed-restart-v1`; `SERVING_MAINTENANCE_ID` permite outro ID para uma
nova manutenção explicitamente planejada. Não crie outro ID para contornar falha.

## Testar e commitar

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/serving_recovery tests/product_flow tests/model_updates tests/operational_round \
  tests/streaming/replay/test_durable_triage_replay.py \
  tests/streaming/decisions/test_customer_day_alert_policy.py \
  tests/serving tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py tests/infra/test_platform_entrypoints.py
```

O ZIP inclui o comando completo de commit na branch `feat/kind-helm-serving`,
sem incluir `.vscode/settings.json` ou `mlruns/`. A preparação exige o código e a
declaração de transição no HEAD. Não é preciso repetir os oito minutos do replay.

## Preparar a imagem e provar a retomada no KinD

Deixe a API atual saudável e os serviços prontos. Suspenda outros escritores
de serving/Registry e execute:

```bash
make serving-recovery-prepare SERVING_NO_OTHER_WRITERS=1
```

O Job isolado começa com volume vazio, instala a release e encerra seu init
container deliberadamente com exit 17. O Kubernetes o reinicia no mesmo pod e
volume; a segunda execução precisa retornar `canonical_release_reused` e exit 0.
O contêiner de verificação confirma a reutilização mais uma vez. São conferidos
especificação do Job/pod, vínculo pelo UID, exatamente um reinício, imagem efetiva
e os dois resultados de reutilização (modelo exato, zero escritas, três arquivos).
O comando revisado só encerra com 17 depois da instalação; a prova combina esse
comando, o reinício e a reutilização no mesmo volume. Não depende de `logs --previous`,
que pode estar indisponível após a conclusão. O recibo identifica essa base de
comprovação e não inventa o conteúdo do log anterior. O Job permanece para inspeção;
uma repetição do preparo reutiliza o mesmo Job, sem criar outro.
Esse ensaio não interrompe a API ativa nem reinicia a máquina.

A saída esperada é `prepared_awaiting_approval`, com `native_restart_check` igual
a `seed_restart_verified`. Revise a imagem, o hash do modelo e `approval_sha256`.

## Aplicar a manutenção aprovada

```bash
make serving-recovery-apply SERVING_NO_OTHER_WRITERS=1 \
  SERVING_APPROVAL_SHA256=COLE_O_DIGEST_EXATO_DA_PREPARACAO &&
make serving-recovery-verify &&
make product-cases
```

**Pode haver breve interrupção da API:** o slot utiliza `Recreate`. Essa manutenção
do instalador não é uma promoção blue/green e não reivindica zero downtime.
O gateway e o alias continuam apontando para o mesmo slot/versão. A operação
reconfere pesos, contrato e score antes de atualizar o ponteiro operacional.

## Recibos, compatibilidade e recuperação

Arquivos em `data/storage/kind-platform/serving-maintenance/seed-restart-v1/`:

| Arquivo | Finalidade |
| --- | --- |
| `carrier/` | Mesmas releases, instalador corrigido e Dockerfile de build |
| `restart-job.json`, `restart-check.json` | Plano e resultado do reinício isolado |
| `prepared.json` | Estados anterior/posterior, imagem, gates e aprovação |
| `applying.json` | Registro da autorização antes da mutação |
| `applied.json` | Manutenção concluída com vínculo aos estados e HTTP |
| `failure-*.json` | Falha preservada e instrução de retomada |

Somente `model-updates/current.json`, que já é um ponteiro operacional mutável,
é atualizado. Recibos de treino, avaliação, rodada, eventos e checkpoint concluído
não são reescritos. A leitura segue o recibo de manutenção para resolver apenas
uma troca de portadora do mesmo modelo; uma versão, peso, runtime, rota ou
Registry diferentes exigem o fluxo de atualização de modelos.

`references/serving_seed_source_transition_v1.json` autoriza hashes exatos de
uma revisão operacional: instalador, leitores/entrada de staging e os dois
helpers de manutenção. A declaração precisa estar commitada. Não libera
mudança de features, treino, contratos ou políticas. Assim, o replay aceito
continua verificável sem trocar sua identidade ou seus dados.

Depois de interrupção na aplicação, resolva a causa e repita o mesmo comando
`serving-recovery-apply` com o mesmo digest. O estado só pode ser o anterior ou
o posterior já declarado; alterações de terceiros são recusadas. A manutenção
não altera aliases e não apaga releases antigas para tentar concluir.

Nas futuras preparações de candidatos, a portadora usa o mesmo instalador
corrigido. Staging resolve a manutenção do incumbent mantendo versão/pesos;
o gate de qualidade, a aprovação de tráfego e o rollback permanecem separados.

## Operação diária

Use `make platform-status` para inspecionar o ambiente e
`make serving-recovery-verify` para conferir este serving após a manutenção.
`serving-recovery-apply` é uma manutenção revisada, não um comando diário de
reinstalação. Não volte a uma release sintética nem escolha um baseline antigo
apenas para fazer um comando de startup passar. Os comandos `platform-start`
e `platform-stop` usam o baseline explícito da operação que os criou; ele não
deve ser confundido com o ponteiro atual `fraud-detector@champion`.

O aceite desta correção será registrado depois de executar prepare/apply/verify
no ambiente nativo. A correção não declara um novo reboot do host. A ativação e
rollback nativos de um sucessor com pesos diferentes continuam pendentes.


## Retomar uma preparação interrompida na coleta de evidência

Após aplicar, testar e commitar a correção do coletor, repita o mesmo comando
`make serving-recovery-prepare SERVING_NO_OTHER_WRITERS=1`. Preserve o diretório
`seed-restart-v1` e seu Job. A retomada verifica os arquivos do contexto contra as
releases ativas, o instalador e o Dockerfile atuais, consulta a identidade da imagem
já carregada e confere o plano e o pod do Job retido. Não reconstrói a imagem nem
troca a API. Conteúdo divergente, outro node ou baseline diferente são recusados.

`preparing.json` fixa a baseline e o node; `image.json` fixa a identidade carregada.
Na primeira retomada de uma tentativa anterior à criação desses recibos, eles são
estabelecidos somente após conferir o contexto existente. Uma tentativa de build
incompleta que ainda não chegou a criar `restart-job.json` permanece bloqueada para
diagnóstico. O preparo concluído continua exigindo revisão e aprovação do digest
antes de aplicar a manutenção.


## Aceite operacional — 2026-10-22

A manutenção foi aplicada e verificada no KinD com aprovação vinculada ao digest
`87e33df468833c22ede18fb2899052bc2f8e7af2e2dea0c4cf4b1df6fb25ad72`.
O preparo retomou o Job existente sem rebuild. A API permaneceu associada ao
champion versão 1, slot blue, modelo
`6dad9a78a5da0bda34cae6957b0ddbe391ecd848fbbc6604ae930bea3501370b`
e release `e8350e0a841f4136983e0d905068a743`.

A verificação HTTP reproduziu score `0.4178630794049542`, recusou entrada inválida
com 422 e mediu p95 de 91,33 ms em 20 requisições locais. A consulta posterior
retornou os cinco casos existentes com o mesmo modelo/release; suas explicações
SHAP continuam como próximo recorte. O recibo nativo está em
`data/storage/kind-platform/serving-maintenance/seed-restart-v1/applied.json`.

A [evidência versionada](../../references/evidence/serving_seed_recovery_author_validation_2026-10-22.json)
registra o aceite a partir das saídas fornecidas pelo autor. Este passo valida
o instalador e a manutenção do mesmo modelo; não inclui outro reboot do host
nem a promoção de um sucessor com pesos diferentes. O fluxo de eventos concluído
foi reutilizado, sem treino ou replay completo adicional.


## Manutenção do runtime da API com os mesmos pesos

Preparação de 2026-10-25, sobre o commit do autor
`595dff4a13ab2451a233d71d5e9f60522df96810`. Os gates Docker já aceitaram a
imagem `sha256:1215b5ec0bc53a9163e5154b783a7cf03822a8a44b9bae70b11a20d612b03e5e`,
com redução de 2,38%. A implantação e a reversão em KinD **ainda precisam de
aceite nativo**. Este fluxo não promove um modelo sucessor.

O operador continua usando Make. A preparação verifica o baseline vigente,
confere o recibo Docker contra a release ativa e o checker commitado, carrega a
imagem pelo export de plataforma já existente e executa uma API isolada no KinD.
Essa API usa o mesmo carrier e manifesto, em novos emptyDirs, sem labels que
permitam ao Service canônico selecionar o pod. As imagens efetivas e o HTTP são
verificados. Só o pod de prova, com UID e spec conferidos novamente, é removido
após sucesso. Uma falha preserva o pod e seus recibos para diagnóstico; ele tem
prazo máximo de execução de 240 segundos. Não são alterados dados históricos,
pesos, alias, tráfego ou workloads canônicos nessa preparação.

Antes de usar: commit da implementação revisada; node e serviços disponíveis;
nenhum outro writer de serving/Registry durante a operação. Se houver um
`session-monitor-watch` aberto com a versão anterior do código, encerre somente
essa coleta com Ctrl+C e reabra após o commit. O monitor aceita a transição exata
de fontes operacionais, sem rebuild da imagem receiver, rebind ou reexecução de
transações. A coleta retomada restabelece a atualização dos agregados.

### Preparar e revisar

```bash
make serving-runtime-prepare \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-session-monitoring-20261024.json \
  SERVING_NO_OTHER_WRITERS=1
```

Valores padrão: `SERVING_RUNTIME_ID=api-trim-v1` e
`SERVING_RUNTIME_DOCKER_REPORT=data/serving/image-footprint/candidate-http-v1.json`.
A saída mostra `runtime_prepared_awaiting_approval`, imagem local e identidades
carregadas, gate isolado, `approval_sha256` e `rollback_approval_sha256`.
Confira que modelo, release e versão do Registry continuam os do champion.
Repetir prepare com o mesmo ID retorna o preparo íntegro. Não edite os recibos
nem use outro ID apenas para contornar divergência de identidade.

### Aplicar, conferir e renovar o baseline

O chart usa **Recreate**. A manutenção troca o runtime no mesmo slot, podendo
interromper brevemente a API; não promete zero downtime. O slot, o Service ativo,
o carrier, os pesos e o alias permanecem os mesmos. Os templates do slot inativo
também recebem a imagem revisada, conservando zero réplicas. A reversão retém as
imagens e os caminhos anteriores. Não remova essas imagens durante o aceite.

```bash
make serving-runtime-apply SERVING_NO_OTHER_WRITERS=1 \
  SERVING_APPROVAL_SHA256=COLE_O_APPROVAL_SHA256_REVISADO &&
make serving-runtime-verify &&
make portal-verify &&
make portal-performance-verify &&
make access-up
```

`verify` confere Registry, imagens, HTTP, ownership, PVCs e os readbacks
funcionais anteriores de tracking/S3/Prometheus/Grafana. A preservação de versões
históricas de Secrets do Helm segue a política de retenção do próprio Helm;
apenas seu histórico de revisões pode variar. Outros Secrets/configurações e
UIDs dos recursos existentes não são adotados nem substituídos. O fluxo não
reescreve os recibos antigos da rodada, sessão, portal ou baseline.

Depois do verify aprovado, crie **outro** baseline com o comando canônico:

```bash
make platform-baseline \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-runtime-trim-v1.json \
  PLATFORM_TRACKING_REPORT=data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json &&
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-runtime-trim-v1.json
```

O baseline anterior é histórico, não é atualizado. O novo baseline passa a ser
usado nos próximos check/stop/start. Não há novo aceite de reboot ou recuperação
de perda do host somente por criar esse baseline. A prova de chegadas simultâneas
com monitoramento, a qualidade global do modelo e a promoção de sucessor com
pesos diferentes continuam aceites independentes.

### Retomar ou reverter uma tentativa

Os recibos ficam em
`data/storage/kind-platform/serving-maintenance/<SERVING_RUNTIME_ID>/`.
A autorização é persistida antes do Helm, o HTTP é verificado antes de publicar
`current.json`, e o resultado concluído é ligado à cadeia de manutenções.
Uma interrupção durante apply pode ser retomada com o **mesmo** ID e digest.
Se outro modelo/slot/alias, recurso ou template estranho aparecer, a operação
recusa sobrescrever o estado. Os comandos não aceitam uma alteração arbitrária
no YAML como justificativa para "adotar" o recurso.

Para voltar à imagem anterior, inclusive se a tentativa falhou antes de publicar
o novo `current.json`, use o digest específico de rollback impresso no prepare:

```bash
make serving-runtime-rollback SERVING_NO_OTHER_WRITERS=1 \
  SERVING_APPROVAL_SHA256=COLE_O_ROLLBACK_APPROVAL_SHA256 &&
make serving-runtime-verify &&
make access-up
```

A reversão registra outro elo da cadeia, restaura somente as imagens runtime
anteriores, conserva pesos/Registry e não apaga o recibo da tentativa. Repetir a
mesma reversão concluída é idempotente. Para aplicar de novo após rollback, faça
uma nova manutenção revisada: não reutilize o apply antigo. Se a reversão for
aceita, gere um baseline de rollback em caminho novo pelo mesmo procedimento
canônico. A disponibilidade continua sujeita ao Recreate.

A transição `references/runtime_maintenance_source_transition_v1.json` declara
os hashes exatos da limpeza de build e da manutenção operacional. A aceitação é
em memória e exige o arquivo commitado; não altera políticas de qualidade,
fitter, contratos Gold/Silver, pesos, recibos científicos ou modelos registrados.


### Retomada do prepare após a correção do encaminhamento isolado

A correção de 2026-10-25 mantém o helper de encaminhamento canônico intacto.
O pod `fraud-runtime-check-*` usa um encaminhamento próprio, limitado ao
namespace/contexto da plataforma e ao loopback; nome, UID e spec são conferidos
antes do processo, antes de entregar a URL e depois do gate HTTP. Labels de
seleção do Service canônico não são aceitas. O processo de encaminhamento é
encerrado também quando o HTTP falha.

Depois de aplicar, testar e commitar a correção, retome **o mesmo ID**:

```bash
make serving-runtime-prepare \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-session-monitoring-20261024.json \
  SERVING_RUNTIME_ID=api-trim-v1 \
  SERVING_NO_OTHER_WRITERS=1
```

`runtime-preparing.json`, o baseline e a qualificação Docker não são reescritos.
O leitor aceita apenas os hashes declarados e commitados da correção de runtime
e de seu leitor de compatibilidade. Código científico, pesos, node e estado
ativo continuam fixados. O preparo concluído registra o perfil de fontes atual.

Se o pod de prova já tiver expirado com `DeadlineExceeded`, ou tiver sido
removido após seu UID ser registrado, a retomada arquiva a tentativa em
`runtime-probe-attempts/`, registra a renovação e cria outra prova com a mesma
spec/imagens. Os ponteiros de **rascunho** do probe são atualizados de forma
retomável; o pod expirado é conservado para diagnóstico. Um UID/spec estranho,
um pod ainda ativo ou uma prova já concluída não autoriza renovação. Outras
falhas são preservadas para investigação. Não apague a pasta de manutenção e
não edite os hashes para passar pelo gate. A API canônica permanece intacta.

### Pré-validação do chart e nova tentativa após rollback

A tentativa `api-trim-v1` foi revertida com sucesso em 2026-10-25. O gate HTTP
confirmou o mesmo modelo, release e score de controle, e os readbacks funcionais
passaram. A imagem candidata ainda não está aceita como runtime ativo.
Os recibos desse ID devem ser preservados; as instruções de retomada acima
referem-se ao preparo anterior à aplicação e não reabrem uma tentativa revertida.

O gerador passa a usar `fraud-kind-serving-runtime:<32 hex>`, como exige o
schema existente do chart. A tag anterior `fraud-runtime-maintenance:...` não
era aceita pelo Helm. O schema não foi flexibilizado. O preparo executa
`helm template` com os mesmos valores do rollout antes de emitir uma aprovação;
a aplicação e o rollback também verificam o chart antes de alterar workloads.
Uma falha de schema traz o diagnóstico na saída. A renderização valida o chart;
a disponibilidade e o contrato HTTP continuam exigindo os gates nativos.

Depois de aplicar, testar e commitar a correção, crie um baseline do estado
restaurado e prepare outro ID:

```bash
make platform-baseline \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-runtime-rollback-v1.json \
  PLATFORM_TRACKING_REPORT=data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json &&
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-runtime-rollback-v1.json &&
make serving-runtime-prepare \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-runtime-rollback-v1.json \
  SERVING_RUNTIME_ID=api-trim-v2 \
  SERVING_NO_OTHER_WRITERS=1
```

Não sobrescreva um baseline existente. O baseline anterior continua histórico;
o rollback alterou o recibo do estado ativo e não gerou um baseline novo.
A qualificação Docker existente é reaproveitada: atribuir uma tag válida não
reconstrói a imagem. A próxima aprovação terá outro digest. Não reutilize os
digests de `api-trim-v1`. Esta manutenção conserva pesos e alias e usa rollout
no mesmo slot; não comprova promoção de sucessor nem ausência de interrupção.
