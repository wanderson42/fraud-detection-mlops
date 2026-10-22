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
restart count, imagem efetiva e logs de instalação/reutilização. O Job é removido
apenas depois de gravar seu recibo; Jobs com falha permanecem para diagnóstico.
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
