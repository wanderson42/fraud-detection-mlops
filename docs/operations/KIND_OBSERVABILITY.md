# Observabilidade no KinD

**Estado: aceite nativo e conferência visual aprovados em 2026-10-21.** Exporter, Prometheus
e Grafana usam uma release Helm separada, `fraud-observability`, no cluster e
namespace `fraud-platform`. Não altera Compose, fundação, MLflow, Jobs, replay
ou serving existentes. A observabilidade tem aceite próprio de 2026-10-21; a
parada/subida do produto canônico, incluindo o proxy após reinício, foi aceita
em 2026-10-23. O reboot desse estado canônico não foi executado neste aceite.

## Aceite nativo e visual — 2026-10-21

O autor aprovou o gate nativo e confirmou a conferência visual em 2026-10-21.
A cópia somente de leitura foi conciliada com a origem: **556.824 eventos,
143.945 decisões e 1.453 alertas**. Scrape Prometheus, oito regras saudáveis,
proxy autenticado do Grafana e quatro dashboards provisionados foram conferidos.
O exporter ganhou novo pod, manteve Deployment/PVC e repetiu as verificações.
Prometheus, Grafana, fundação, tracking, serving e PVC retido do replay
preservaram suas identidades; arquivos da origem permaneceram iguais.

| Verificação | Resultado observado |
| --- | --- |
| Teste nativo | 1 passou em 62,64 s; tox OK em 73,91 s |
| Fonte e cópia | Três arquivos; 778.943.767 bytes na cópia; metadata, checkpoint e telemetria identificados por hashes próprios |
| Leitura | Bytes no mount conferidos e summary igual ao preparo; mount do exporter somente leitura |
| Coleta | Target exato `fraud-observability-exporter:19100`; contagens, conclusão e quatro divergências conciliadas |
| Regras | Oito regras saudáveis, expressões e metadados conferidos; firing não testado neste gate |
| Grafana | Datasource e consulta pelo proxy autenticado, quatro dashboards provisionados e conferência visual confirmada pelo autor |
| Recuperação | Novo pod exporter, mesmo Deployment e mesmos três PVCs; coleta e consultas aprovadas novamente |
| Preservação | Mesmas identidades dos demais recursos; origem histórica inalterada |

A coleta é atual, mas os eventos, feedback e latências pertencem ao **replay
concluído**. As quatro divergências de features permanecem abaixo de 0,1 entre
as janelas operacionais de 1.000 decisões; isso não é nova avaliação do modelo,
baseline de treino ou comprovação de concept drift. A cópia SQLite usa hashes
próprios de backup e igualdade dos agregados; não exige igualdade binária com o
arquivo de banco original.

O recibo automatizado mantém `visual_dashboard_review_validated=false`, pois
o gate não renderiza o browser. A confirmação humana posterior foi registrada
separadamente na [evidência](../../references/evidence/kind_observability_author_validation_2026-10-21.json),
sem editar o recibo. Não foi feita inspeção independente do browser pelo assistente.
Os port-forwards temporários terminaram; stack e PVCs ficaram instalados.

Este aceite encerra o recorte de observabilidade. O marco de infraestrutura
continua aberto para parada/subida, reboot e sequência integrada. Não comprova
backup/restore dos PVCs, recuperação por perda da máquina ou firing de alertas
no KinD. As evidências anteriores de Compose mantêm seu escopo próprio.

## Refinamento visual posterior — aprovado em 2026-10-21

A política de cores foi revista após o aceite funcional/visual acima: informações
azuis; condições válidas verdes; investigação amarela; violações vermelhas;
indisponibilidade cinza, com labels/legenda. Consultas e valores não mudam.
O recibo anterior conserva os hashes da configuração testada naquela execução;
o gate e a revisão visual das novas cores foram aprovados em aceite separado.
[Política, limites e aplicação sem rebuild](DASHBOARD_COLORS.md).

## Qual estado os painéis mostram

O checkpoint do ensaio de recuperação sintético no KinD não contém o diário
`telemetry.sqlite` exigido pelo collector. Por isso este recorte recebe, por
opção explícita do operador, uma **cópia consistente do replay nativo concluído**
`data/replay/paced-native-v1`. O collector e os três dashboards existentes são
reutilizados sem mudar suas métricas ou contratos. Não há reexecução de eventos,
recálculo de features, carregamento de modelo ou treino.

O preparo exige `--no-other-writers`, replay completo, sessão parada, nenhum
pending uncommitted e ausência de sidecars SQLite. Cria backups SQLite por
conexões somente de leitura, confere `integrity_check`, igualdade dos agregados
e hashes dos arquivos de origem antes/depois. A declaração do operador não é
um lock do escritor externo: se ele estiver ativo, finalizar sua execução antes.
Não tentar capturar uma fonte viva com este comando.

Os três arquivos são transferidos por stdin para um pod temporário restrito,
com whitelist, orçamento, tamanhos e hashes externos ao stream. Somente após
receber e verificar todos os bytes o diretório ganha o nome `snapshot`.
Transferência incompleta fica preservada como `incoming`, sem publicação ou
sobrescrita automática. A origem histórica nunca é montada dentro do cluster.

O exporter lê o PVC separado em modo **somente leitura**, com a mesma identidade
de metadata/checkpoint/telemetria. A coleta ocorre agora; transações, feedback
e latências continuam pertencendo ao replay concluído. O timestamp de coleta
não transforma esses registros em ingestão ao vivo. A comparação de features
continua entre as primeiras e últimas 1.000 decisões do mesmo checkpoint,
sem baseline de treino, prova de concept drift ou nova avaliação do modelo.

## Instalar

Pré-requisito: fundação pronta, serving aceito e fonte concluída ainda preservada.
Os comandos usam kubeconfig/contexto privados e lock compartilhado das operações
no cluster. Não usar `kubectl` com contexto implícito.

```bash
poetry run python -m scripts.platform.kind_observability validate &&
poetry run python -m scripts.platform.kind_observability init &&
poetry run python -m scripts.platform.kind_observability prepare \
  --state data/replay/paced-native-v1 --no-other-writers &&
poetry run python -m scripts.platform.kind_observability build &&
poetry run python -m scripts.platform.kind_observability up &&
poetry run python -m scripts.platform.kind_observability status
```

`prepare` não sobrescreve snapshot existente. Não repetir esse passo se o
preparo anterior terminou; continuar de `build` ou `up`. Imagens existentes com
recibo completo são reutilizadas após validar fontes/node/identidades.
Falhas parciais preservam recursos e arquivos; não apagar recibos para forçar
adoção ou aplicar `down -v`/prune. Consultar o diagnóstico antes de retomar.

São reservados três PVCs locais: cópia do estado (2 GiB), Prometheus (1 GiB) e
Grafana (512 MiB), todos com política Helm `keep`. A cópia aceita até 1,5 GiB;
a fonte conhecida ocupa aproximadamente 780 MB. O host precisa também de espaço
para a cópia privada e exportações temporárias de imagens. A primeira instalação
não espera PVCs sem consumidor: o pod de transferência permite o binding do
estado e só depois são ativados os três Deployments.

Somente a imagem stdlib do exporter é construída. Prometheus e Grafana reutilizam
os digests já registrados em `infra/observability/images.lock.json`. As três
imagens são exportadas para `linux/amd64`, importadas como arquivos no KinD e
conferidas pela identidade CRI. Os arquivos temporários de imagem são removidos.
Os digests importados localmente não representam publicação em registry.

## Gate nativo

```bash
.venv/bin/python -m tox -e kind-observability
```

O gate confere bytes do estado dentro do mount do exporter; igualdade do summary
com a cópia preparada; contagens, suporte e divergências de features no Prometheus;
target exato e oito regras saudáveis; datasource, consultas pelo proxy autenticado
do Grafana e identidade/configuração dos quatro dashboards provisionados.
Depois recria somente o pod exporter, verifica novo UID, mesmo Deployment/PVCs
e repete a leitura/consulta. Prometheus/Grafana, fundação e demais recursos do
namespace devem manter suas identidades; arquivos de origem devem manter hashes.

Esse gate não comprova firing/recuperação de alerta, renderização visual no browser,
backup/restore dos novos PVCs, reboot, processamento ao vivo ou recuperação por
perda da máquina. A recuperação de alertas já demonstrada em Compose mantém
seu escopo próprio; não é declarada novamente como aceite no KinD.
Recibos ficam em `data/storage/kind-platform/observability/rehearsal-<id>.json`.
Instalações também deixam recibo e logs limitados em caso de falha. A stack fica
instalada após o gate; somente os port-forwards e o pod de transferência terminam.

## Abrir os painéis

```bash
poetry run python -m scripts.platform.kind_observability open \
  --component grafana --port 13001
```

Acessar `http://127.0.0.1:13001`, usuário `admin`. Consultar localmente a senha
em `data/storage/kind-platform/observability/credentials.json` (modo 600).
Não compartilhar esse arquivo. A senha não aparece em valores Helm, imagens
ou recibos e é entregue ao Grafana por Secret próprio.
Ctrl+C encerra somente o acesso local, sem parar os pods.

O painel inicial **Fraud — KinD / replay concluído** explica origem e limites,
mostra contagem de eventos e estado da coleta, e aponta aos três painéis existentes:
operação, qualidade madura da triagem, dados/features. A conferência visual dos
valores contra o recibo foi confirmada pelo autor no fechamento deste recorte. O acesso é somente loopback;
Services permanecem ClusterIP. Não há autenticação pública, TLS, NetworkPolicy,
Alertmanager ou isolamento multiusuário implementado por este recorte.

## Depois deste recorte

Conferir a operação após parada/subida e reboot: Docker, node existente, API
Kubernetes, dependências, aplicações, PVCs e limites inotify persistentes.
Então consolidar os comandos e a sequência integrada. Streamlit permanece uma
possibilidade da simplificação, caso uma jornada de investigação acrescente valor;
não é requisito para concluir a observabilidade.

Referências: [provisionamento Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/),
[API Prometheus](https://prometheus.io/docs/prometheus/latest/querying/api/),
[volumes Kubernetes](https://kubernetes.io/docs/concepts/storage/volumes/).

## Correção de compatibilidade Helm 4 — 2026-10-21

A primeira tentativa do autor preparou a cópia (556.824 eventos, 143.945 decisões,
1.453 alertas) e importou as três imagens, mas parou antes dos Deployments.
A consulta usava `helm list --all`, opção removida no Helm 4.3.1. Sem filtro de
status, `helm list` já inclui todos os estados. A correção retira somente essa
opção e passa a exibir stdout/stderr limitados nas falhas deste fluxo Helm.

A correção inicial reconhecia uma única versão anterior do controlador. O
refinamento de cores separa depois os pins de runtime dos pins de implantação:
[detalhes em DASHBOARD_COLORS.md](DASHBOARD_COLORS.md). Recibos antigos precisam
coincidir integralmente com um perfil conhecido e manter exatas as entradas
reais das imagens. Node, owner, tags e identidades CRI continuam conferidos.
Nenhum recibo é reescrito. Cópia, credenciais, imports e tentativa falha ficam
preservados; atualizar um painel não exige rebuild da imagem.

Após aplicar, continuar de `up`, `status` e `tox -e kind-observability`.
Não repetir `init`, `prepare` ou build para contornar esta falha. O aceite nativo foi aprovado após essa correção. Se outra chamada Helm falhar, o novo erro e o recibo de
instalação fornecerão o diagnóstico nativo.


## Plugins embarcados e retomada do Grafana

O chart usa a imagem por identidade e mantém o filesystem raiz somente de leitura.
`GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false` desativa a verificação informativa;
não desativa a atualização do preinstaller. O chart também define
`GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false`, para conservar os plugins embarcados
na imagem durante inicializações e retomadas. Atualizações serão revisadas com a
imagem/configuração, sem depender do catálogo externo no instante do boot.

O gate exige saúde do datasource Prometheus e consulta autenticada pelo proxy,
além da existência de sua configuração. HTTP 200 em `/api/health` não garante que
o plugin esteja registrado. `plugin.notRegistered` ou `Unable to find datasource
plugin` devem produzir falha, sem contornar o proxy consultando somente Prometheus.

O diagnóstico nativo de 2026-10-23 mostrou Grafana 13.2.4 saudável e datasource
existente, mas plugin sem registro. Há um bug upstream compatível: a atualização
pode descarregar o plugin antes de falhar na instalação. A associação com esse
mecanismo é uma hipótese sustentada pelo chart e pelo relato upstream; os logs de
inicialização do autor ainda são necessários para atribuir a causa exata.

Referências: [opções de plugins](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#preinstall_auto_update)
e [bug do atualizador](https://github.com/grafana/grafana/issues/132528).

Após aplicar o código, reconcilie pelo controlador existente:

```bash
poetry run python -m scripts.platform.kind_observability validate &&
poetry run python -m scripts.platform.kind_observability up
```

Isso atualiza a release Helm de observabilidade e recria Grafana com o novo env,
preservando imagens e PVCs. Depois confira `verify_http()` por leitura. O aceite
nativo da correção e da parada/subida foi concluído em 2026-10-23: proxy, regras
e dashboards aprovados depois do reinício no mesmo ciclo canônico. Baseline anterior à mudança
de código/spec fica preservado; o próximo ensaio cria outro. Não edite o JSON.

[Evidência do ciclo canônico e proxy após reinício](../../references/evidence/canonical_lifecycle_author_validation_2026-10-23.json).
