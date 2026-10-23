# Ciclo operacional do produto no KinD

O ponto de entrada diário é Make. O mesmo controlador para, retoma e verifica a
fundação PostgreSQL/RustFS, tracking, observabilidade e a API do champion atual.
Helm continua descrevendo a instalação; o ciclo não faz upgrade, build, treino,
promoção, alteração de rota/alias ou criação de cluster.

## Baseline canônico v2

O controlador lê `model-updates/current.json`, sem escolher algoritmo ou rodada.
Confere o champion pelo mesmo gate de `platform-api`: Registry, rota, pesos e
imagens exatos, contrato HTTP, resposta de controle e orçamento de latência.
O baseline grava a identidade desse estado ativo e os recursos presentes.
Latência deve passar pelo orçamento em cada gate; não precisa ser numericamente
idêntica entre execuções. Scores, release e identidades devem permanecer iguais.

| Recurso | Plano capturado e restaurado |
| --- | --- |
| `fraud-detector-<slot ativo>` e gateway | Uma réplica |
| Slot inativo, quando instalado | Zero; não é iniciado por uma retomada |
| PostgreSQL, RustFS, MLflow e observabilidade | Uma réplica por controller |
| `fraud-serving-*`, se ainda instalado | Exatamente as réplicas salvas, após conferir ownership no recibo legado |
| Outros controllers ou Jobs ativos | Recusados; revisar antes de interromper |

Uma única lista determina a subida; a parada usa a ordem inversa. Primeiro sobem
RustFS/PostgreSQL, tracking e observabilidade; em seguida o laboratório legado,
quando presente, e os backends/gateway canônicos. Gateways param antes de seus
backends. O serving sintético não é fallback nem evidência da API ativa.
O slot inativo pode estar ausente; não se instala nada durante a retomada.

Antes de parar, confere a identidade Registry/HTTP e revalida recursos. Cada escala
usa UID, spec, resourceVersion e réplicas atuais como precondições. Aguarda os pods
terminarem antes de parar o container exato do node. Na retomada restaura cada
contagem salva, incluindo zeros. PVCs/PVs, Services, configurações e hashes dos
Secrets são comparados; conteúdo secreto não vai para os recibos. Pods podem
receber novos UIDs, mas controllers e volumes devem permanecer os mesmos.

Baseline v1 fica preservado como evidência do laboratório anterior. A rotina atual
exige novo baseline v2 e não converte nem edita JSON antigo. Mudança do estado ativo,
de código pinado ou configuração exige um novo baseline validado com serviços
prontos, nunca a alteração de um recibo anterior. Uma candidata encenada com ambos
os slots vivos deve ser finalizada antes de preparar o ciclo diário.

## Preparar e verificar sem interromper

O kubeconfig privado, contexto `kind-fraud-platform` e namespace `fraud-platform`
limitam os comandos ao cluster próprio. Não se adota outro node/container/engine.
Os outros clusters e a stack Compose ficam fora do ciclo.

```bash
make platform-host-check &&
make platform-status
```

O host exige inotify efetivo de pelo menos 512 instâncias e 524288 watches, valores
identificados no diagnóstico deste ambiente. Para persistir a configuração, revise
`infra/kind/operations/90-fraud-platform.conf` e o guia de bootstrap. O controlador
não altera sysctl ou reinicia a máquina.

Feche writers, Jobs, replay, publicações e ensaios de promoção. Feche também os
acessos locais (`make access-stop-all` e port-forwards abertos em outros terminais).
A declaração de ausência de writers é explícita: o lock local não bloqueia clientes
externos. Um portal de snapshot aberto não escreve transações, mas os acessos
precisarão ser reabertos depois do ciclo.

Use o tracking report já aceito para conferir o marcador SQL/S3 existente. Esse
marcador não seleciona o modelo atendido: a API é conferida pelo champion atual.

```bash
export PLATFORM_TRACKING_REPORT="data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json"
export PLATFORM_BASELINE="data/storage/kind-platform/operations/baseline-canonical-v2.json"
make platform-baseline
```

O caminho é privado e novo; se já existir, escolha outro nome. Preparar só lê
serviços, faz inferências de controle e grava o baseline. Não interrompe o node.
Se houver divergência, preserve o estado e consulte `platform-status`/logs.

## Parada e retomada planejadas

Com o baseline novo conferido e sem outros writers:

```bash
make platform-stop PLATFORM_NO_OTHER_WRITERS=1 &&
make platform-start PLATFORM_NO_OTHER_WRITERS=1 &&
make platform-verify &&
make access-up &&
make access-status
```

`platform-start` também retoma uma parada parcial quando a identidade ainda bate.
O journal registra falhas; não apaga recursos para tentar novamente. Verificação
exige recibos de parada e retomada do mesmo baseline e os mesmos readbacks.
Uma escala externa, sozinha, não substitui o recibo de retomada. Uma retomada após
mudança de modelo/manutenção é recusada antes de iniciar o node; preserve recibos
e reveja a operação, sem reescrever o ponteiro ativo.

## Ensaio nativo e reboot

Alternativa para criar baseline de nome único e ensaiar o ciclo completo:

```bash
make access-stop-all &&
FRAUD_KIND_TRACKING_REPORT="data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json" \
FRAUD_KIND_NO_OTHER_WRITERS=1 \
  .venv/bin/python -m tox -e kind-operations
```

Este comando interrompe temporariamente o cluster próprio. Em falha, o ensaio
tenta restaurar o plano salvo e continua marcado como falho. Use o baseline
impresso para inspecionar/retomar; não crie outro para contornar divergências.
Após sucesso, `make access-up` reabre interfaces; acessos adicionais são opcionais.
Logs dos acessos continuam privados e fora de Git/builds.

Para reboot planejado, prepare um baseline novo, execute `platform-stop`, reinicie
o host manualmente, restaure `PLATFORM_BASELINE` e execute `platform-start` seguido
de `platform-reboot-verify`. Esse gate exige mudança de boot_id e parada anterior
ao reboot. Não execute outro ensaio entre essa parada e a verificação. Nenhum
comando reinicia a máquina automaticamente.

## Estado do aceite

A parada/subida e o reboot do laboratório inicial foram aceitos em 2026-10-21 e
continuam documentados na [evidência original](../../references/evidence/kind_operations_author_validation_2026-10-21.json). O ciclo canônico v2 foi aceito em 2026-10-23 pelo ensaio do autor: uma execução
aprovada em 276,71 s, com mesmo node/volumes, réplicas salvas e proxy do Grafana
conferidos depois da retomada. Tem evidência própria do produto ativo. O gate não comprova restauração de backup, perda do host ou
implantação/rollback de sucessor com pesos diferentes. Esse último depende de
candidato qualificado e da política de aprovação já estabelecida.


### Diagnóstico de plugin do Grafana em 2026-10-23

O ciclo `rehearsal-bf1ca3b552324834b5bd3ded6c860bfb` concluiu parada e retomada,
restaurando green para zero, mas falhou no proxy Grafana com HTTP 404. Registrou
tentativa de recuperação concluída e continua falho. O diagnóstico posterior
mostrou datasource presente e `plugin.notRegistered`. Isso não fecha o aceite.

O [guia de observabilidade](KIND_OBSERVABILITY.md#plugins-embarcados-e-retomada-do-grafana)
explica a configuração do preinstaller e a checagem de saúde do plugin. Após
reconciliar e conferir o proxy, execute o ensaio com baseline novo. Preserve
`baseline-362ba798d04246399a4a2f3365da2604.json` e os recibos do ciclo anterior.


## Aceite canônico — 2026-10-23

| Conferência | Resultado |
| --- | --- |
| Ciclo nativo | 1 teste aprovado em 276,71 s; tox em 279,39 s |
| Contagens restauradas | 11 controllers: dez com uma réplica, green com zero |
| Identidade | Mesmo node/engine/volumes; plano original de réplicas restaurado |
| API ativa | Registry versão 1, blue; score 0,4178630794049542 e entrada inválida HTTP 422 |
| MLflow/S3 | Histórico de dois pontos e bytes do modelo de controle verificados |
| Observabilidade | Scrape, oito regras, quatro dashboards e proxy Grafana aprovados após reinício |
| Acessos reabertos | API, MLflow, Grafana e portal prontos |

O baseline `baseline-4fb6e8ecc8b14ad79564a84086a6c170.json` está vinculado aos
recibos de parada, retomada e verificação pelo digest
`656d636d8fa4ff11981c75ab61d5947769410c3295e2dc3ab5a2ed7279f23f39`.
A [evidência do autor](../../references/evidence/canonical_lifecycle_author_validation_2026-10-23.json)
conserva a transcrição estruturada completa do ciclo e a proveniência do anexo.
Não houve reboot neste aceite. O ciclo anterior e seu baseline continuam falhos e
preservados; o aceite novo não reescreve aquela execução.
