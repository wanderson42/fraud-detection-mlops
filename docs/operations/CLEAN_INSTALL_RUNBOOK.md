# Instalação limpa: do Ubuntu ao produto demonstrável

Este é o percurso para **outro usuário**, com ownership, credenciais, modelos e
recibos próprios. A execução nativa de referência usou Ubuntu 24.04.5 amd64,
6 vCPUs, 8 GiB RAM, 4 GiB swap e disco de 80 GiB, sem copiar dados privados,
modelos ou checkpoints do laboratório anterior. As etapas foram executadas pelo
autor sobre `c734cf3a38a66beae57a1b6671e68043cfe20e94`; a automação de setup e
acesso é posterior. Os túneis já têm [aceite nativo próprio](VM_ACCESS.md#aceite-nativo-dos-acessos);
as fases de instalação do setup não foram reexecutadas.

## 1. Máquina e checkout

Use uma VM nova com OpenSSH e rede de saída. No VirtualBox, encaminhe uma porta
**livre do cliente**, ligada a `127.0.0.1`, para a porta 22 do guest. O ensaio usou
2223; qualquer outra porta serve. Não copie o disco/ownership da VM anterior.
A VM anterior pode ficar desligada ou com estado salvo para liberar memória.
Outros hipervisores e máquinas físicas podem usar o mesmo percurso Linux, mas
não receberam aceite nativo nesta evidência.

Antes de instalar, confira `free -h`, `df -h /`, `nproc`, `lsblk` e `sudo vgs`.
O instalador Ubuntu pode reservar apenas metade do VG para `/`. **Somente se o
volume informado for o root correto e houver espaço livre no VG**, o operador
pode expandi-lo com `sudo lvextend --resizefs --extents +100%FREE
/dev/ubuntu-vg/ubuntu-lv`. Essa operação não faz parte do script automático.
O ensaio ficou com root de aproximadamente 77 GiB. Não confundir memória livre
com disponível nem capacidade declarada com consumo dos containers.

```bash
mkdir -p ~/projetos
cd ~/projetos
git clone https://github.com/wanderson42/fraud-detection-mlops.git
cd fraud-detection-mlops
# Escolha uma revisão integrada e anote-a antes de criar recibos.
git rev-parse HEAD
git status --short --branch
```

Mantenha essa revisão durante o percurso. `c734cf3` documenta a execução já
aceita; para usar os novos scripts, escolha a revisão integrada que os contém.
Não use `git pull` entre gates de uma sessão, nem edite contratos para contornar
pins. Acesso ao repositório público não exige copiar credenciais Git do autor.

## 2. Ferramentas e Python

O script tem fases explícitas; não executa provisioning, treinamento ou reboot.
Execute **na VM nova**, no checkout. Não rode a fase Docker se já estiver instalado.

```bash
bash scripts/vm_setup.sh packages
bash scripts/vm_setup.sh docker
# Saia e reconecte ao SSH para aplicar a associação ao grupo docker.
```

Após reconectar:

```bash
cd ~/projetos/fraud-detection-mlops
bash scripts/vm_setup.sh python
bash scripts/vm_setup.sh tools
bash scripts/vm_setup.sh inotify
make vm-setup-check
poetry run python --version
poetry env info --path
```

O percurso fixa Python 3.14.4 via uv, Poetry 2.4.3 via pipx, Docker 29.1.3,
KinD 0.33.0, kubectl 1.35.9 e Helm 4.3.1. O Python de `/usr/bin` é preservado.
`poetry sync --only main,pipeline,dev` instala pelo lock. uv é apenas o instalador
do interpretador; sua versão deve ser coletada com `uv --version`. containerd,
buildx/compose e pacotes do Ubuntu vêm do repositório apt e suas versões devem ser
registradas; não afirmamos congelamento integral do sistema operacional.
Downloads dos três binários Kubernetes/Helm são conferidos por SHA256 publicado.
Ferramentas existentes são preservadas: compare suas versões antes de continuar.

O setup suporta Ubuntu 24.04 amd64. O diagnóstico `check` é somente leitura.
A fase `inotify` persiste mínimos de 512 instâncias e 524288 watches, preservando
valores correntes maiores. O grupo docker concede administração do host; use uma
VM dedicada. A fase Python exige que o checkout ainda não tenha outra `.venv`
ativa para um projeto diferente. Não copie `.venv` entre máquinas.

## 3. Fundação e tracking próprios

```bash
make platform-foundation
make platform-tracking
.venv/bin/python -m tox -e kind-tracking
```

O teste publica uma fixture pequena para comprovar SQL/S3/HTTP e recarga após
recriação do pod MLflow. Não é o treinamento do champion. Anote o caminho
`tracking/rehearsal-<ID>.json` emitido. Kubeconfig/segredos ficam privados sob
`data/storage/kind-platform/`; o kubeconfig global não é alterado. Não reutilize
ownership ou PVCs de outra instalação.

O ensaio também executou a recriação dos pods da fundação:

```bash
poetry run python -m scripts.platform.kind_foundation rehearse
```

É um teste de continuidade dos mesmos volumes, não backup/restore.

## 4. Bronze e rodada completa

```bash
make data
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download verify --require-complete
poetry run python -m scripts.storage.bronze_archive prepare
poetry run python -m scripts.platform.kind_datasets build
```

Anote o `bundle_id` emitido pela preparação; **não use o UUID/hash do autor**:

```bash
read -rp 'bundle_id Bronze desta instalação: ' bronze_bundle
poetry run python -m scripts.platform.kind_datasets publish --layer bronze --bundle-id "$bronze_bundle"
poetry run python -m scripts.platform.kind_datasets restore --layer bronze --bundle-id "$bronze_bundle"
```

Defina IDs próprios e mantenha-os entre os terminais. O exemplo abaixo é para
uma instalação ainda sem rodada/Registry `fraud-detector`:

```bash
export ROUND_ID=clean-install-v1
export SESSION_EVENT_SOURCE_ID=clean-install-events-v1
export SESSION_ID=clean-install-arrival-v1
export PRODUCT_FLOW_ID="session-$SESSION_ID"
export PRODUCT_EXPLANATION_ID=clean-install-shap-v1
export PORTAL_ID=clean-install-portal-v1
export PORTAL_PERFORMANCE_ID=clean-install-validation-v1
export PORTAL_EVALUATION_ROUND="$ROUND_ID"
export PORTAL_SESSION_ID="$SESSION_ID"
export SESSION_MONITOR_ID="$SESSION_ID"
export MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=true MLFLOW_DISABLE_AGENT_HINT=true

make round-validate && make round-init && make round-data && make round-build && make round-train
```

`round-data` produz Silver/Gold por Jobs e verifica restores independentes.
Apenas confirme as declarações abaixo após encerrar outros processos que escrevam
no Registry/rodada/sessão. O flag declara exclusividade; não é um bloqueio
universal contra todo cliente externo.

```bash
make round-promote ROUND_NO_OTHER_WRITERS=1 &&
make round-deploy && make round-rollout && make round-verify
```

A troca/falha/rollback é deliberada. O aceite exige `canonical_round_complete`,
Inputs/artefatos conferidos e modelo da API igual ao promovido. Novos UUIDs e
hashes são esperados. Validação não é holdout novo; promoção é de laboratório.

## 5. Fonte sem replay anterior e sessão

**Dependência descoberta na instalação limpa:** antes da primeira sessão,
inicialize o ponteiro operacional a partir da rodada aceita. Não fabrique
`model-updates/current.json` nem execute uma nova busca/entrega:

```bash
make update-init UPDATE_ID=clean-install-bootstrap-v1 UPDATE_BASELINE_ROUND="$ROUND_ID"
```

Leia o caminho do Silver do recibo **desta rodada**, sem procurar um restore
qualquer pela data de modificação:

```bash
export SESSION_SILVER_ROOT="$(poetry run python - <<'PY'
import json
import os
from pathlib import Path
path = Path('data/storage/kind-platform/rounds') / os.environ['ROUND_ID'] / 'data.json'
record = json.loads(path.read_text())
restore = Path(record['stages']['silver']['independent_restore']['restored_directory'])
root = restore / 'data/interim/handbook'
assert root.is_dir(), 'Confira a estrutura do restore registrado; não escolha outro bundle'
print(root)
PY
)"
make session-source-plan && make session-source-prepare
```

Copie o SHA emitido e verifique antes de preparar a sessão:

```bash
read -rp 'manifest_sha256 da fonte recém-preparada: ' SESSION_EVENT_SOURCE_SHA256
export SESSION_EVENT_SOURCE_SHA256
make session-source-verify &&
make session-prepare SESSION_SOURCE_STATE="data/replay/session-sources/$SESSION_EVENT_SOURCE_ID" SESSION_NO_OTHER_WRITERS=1 &&
make session-run SESSION_INTERVAL=0.05 SESSION_NO_OTHER_WRITERS=1 &&
make session-feedback SESSION_NO_OTHER_WRITERS=1 && make session-verify
```

A referência usou 288172 eventos na fonte e selecionou 269334 eventos de contexto,
200 decisões HTTP e 13 alertas. Warmup levou cerca de **29 minutos**; chegadas,
20,47 s. Não interprete o warmup como latência por requisição: p95 HTTP observado
foi 42,88 ms para 200 requisições. Custos/tempos variam por máquina. `session-run`
retoma a mesma sessão; não crie outro ID para ocultar falha de retomada.
Preparações podem fazer controles HTTP da API, mas não executar as chegadas da
sessão. Feedback posterior não entra nas features disponíveis na decisão.

## 6. SHAP, portal e desempenho

```bash
make product-explain PRODUCT_CASES_LIMIT=5 PRODUCT_NO_OTHER_WRITERS=1
read -rp 'manifest_sha256 do relatório SHAP: ' PRODUCT_EXPLANATION_SHA256
export PRODUCT_EXPLANATION_SHA256
make product-explanations-verify &&
make portal-prepare PRODUCT_NO_OTHER_WRITERS=1 &&
make portal-session-link &&
make portal-performance-prepare PRODUCT_NO_OTHER_WRITERS=1 &&
make portal-verify && make portal-session-verify && make portal-performance-verify &&
make portal-install &&
make access-start ACCESS_SERVICE=portal && make portal-session-smoke
```

SHAP é limitado aos cinco casos preparados. Os demais devem informar ausência
da explicação. Desempenho global do portal usa 67255 linhas de validação;
qualidade da sessão é outro recorte. `.portal-venv` preserva o ambiente científico.
Revisão visual é um gate humano separado da saúde HTTP.

## 7. Monitoramento, quatro componentes e supervisão

```bash
poetry run python -m scripts.platform.kind_observability validate
poetry run python -m scripts.platform.kind_observability build
make session-monitor-bootstrap && make session-monitor-rehearse
poetry run python -m scripts.session_monitoring.components prepare \
  --id clean-install-components-v1 --session-id "$SESSION_ID" --no-other-writers
read -rp 'approval_sha256 emitido pela preparação dos componentes: ' components_approval
poetry run python -m scripts.session_monitoring.components apply \
  --id clean-install-components-v1 --approval-sha256 "$components_approval" --no-other-writers
```

Confira os UIDs `fraud-kind-overview`, `fraud-replay-ops`, `fraud-feature-health`
e `fraud-triage-quality`, além do dashboard da sessão. Comparação de drift pode
ficar indisponível por falta de janelas suficientes; não preencher com zero.
O ensaio verifica expiração/retomada de coleção sobre sessão concluída; não
comprova chegadas simultâneas, disparo de alerta ou SLA.

Sem watcher manual ou acesso Grafana manual ainda rodando:

```bash
poetry run python -m scripts.platform.host_supervision install --session-id "$SESSION_ID"
poetry run python -m scripts.platform.host_supervision preflight --session-id "$SESSION_ID"
sudo loginctl enable-linger "$USER"
systemctl --user enable --now fraud-session-observer.service fraud-grafana-access.service
poetry run python -m scripts.platform.host_supervision check --session-id "$SESSION_ID"
poetry run python -m scripts.platform.host_supervision rehearse --session-id "$SESSION_ID"
```

O `rehearse` termina deliberadamente os dois processos supervisionados e verifica
sua recuperação. Não faça isso fora da janela de teste. Portal é um processo
separado e não está incluído nessas duas unidades. Não execute `access-start`
Grafana em paralelo à unidade que já atende a porta 13001 na VM.

## 8. Acesso do cliente e encerramento

Use [VM_ACCESS.md](VM_ACCESS.md) para túneis com portas locais automáticas.
Confira visualmente portal/SHAP/desempenho e os quatro dashboards; a evidência
humana não pode ser inferida do smoke HTTP. A senha do Grafana fica no guest em
`data/storage/kind-platform/observability/credentials.json`, usuário `admin`.
Não coloque senha em comando, screenshot ou recibo público.

Para testar logout, anote boot ID/PIDs/contadores/Linger, feche **todas** as
conexões e túneis, aguarde e reconecte. Compare PIDs, contadores, hashes e saúde.
Para o teste de reboot do host dedicado, anote boot ID antes, execute manualmente
`sudo reboot`, reconecte e rode `host_supervision check --session-id ...` e
`journalctl --user -b -u fraud-session-observer.service -u fraud-grafana-access.service`.
O ensaio novo observou duas tentativas adicionais no observador e uma no acesso
Grafana durante a prontidão do Kubernetes. Os serviços se recuperaram sozinhos.
Isso não é aceite de `platform-reboot-verify` nem de um ciclo `platform-stop/start`;
esses gates exigem baseline própria e o [procedimento planejado](KIND_OPERATIONS.md).

Para voltar a mostrar o portal após reboot, inicie novamente seu acesso com o ID
correto e reabra os túneis. Não execute `session-run` para exibir uma sessão aceita.
Nunca use `kind delete cluster`, exclusão de PVC ou `docker system prune` como
procedimento de encerramento. Guarde recibos privados e backups fora da VM.

## Aceite e limites

A [evidência pública](../../references/evidence/clean_install_author_validation_2026-11-01.json)
registra os resultados enviados pelo autor, incluindo revisão visual e reboot.
Logout completo na VM nova ainda depende de confirmação explícita de que todas
as conexões foram encerradas; não foi inferido de PIDs iguais. A recuperação
integral da mesma instalação e a perda física do host continuam fora do aceite.

Esta entrega não exige conta paga, Terraform, broker, feature store ou novo
orquestrador. A disponibilidade futura de repositórios apt/PyPI/imagens e da
origem dos dados é uma dependência externa. O percurso torna essas entradas
explícitas e verificáveis; não promete reprodução universal em qualquer SO.

## Fechamento e operação cotidiana

O acesso automático, reutilização, recusa de porta ocupada e encerramento dos
túneis preservando serviços remotos foram aceitos pelo autor. O PR #22 integrou
a implementação em `c6045f4`; consulte a [evidência dos acessos](../../references/evidence/vm_access_author_validation_2026-10-31.json). O marco funcional está encerrado no escopo demonstrado,
com as limitações acima. Não repita treino, sessão, reboot ou restore para abrir
os relatórios aceitos. Use `vm-access-up/status/stop` no cliente e o vínculo
correto de portal/sessão. Cada nova abertura pode informar outras portas locais.

A VM nova não precisa de token GitHub: o autor importou a branch por SSH no
computador cliente e publicou de lá. Configure `git config --local user.name`
e `user.email` na máquina que cria commits; identidade de autoria não é senha.
Para chamadas remotas não interativas, use `.venv/bin/python` do checkout.
O próximo recorte do roadmap é decidir a coordenação dos pipelines, começando
pelo executor existente; uma nova plataforma só entra se reduzir complexidade.

## Evolução da coordenação dos Jobs

Em revisões que contêm [PIPELINE_RECOVERY.md](PIPELINE_RECOVERY.md), rodadas novas
vinculam `round-data` a uma tentativa persistida, com retomada dos mesmos Jobs.
O percurso manual acima continua sendo a evidência histórica aceita. A nova
coordenação tem preparação/testes próprios e não exige refazer rodadas concluídas.
