# Troca blue-green e rollback do serving

**Estado em 2026-10-19: ensaio Docker nativo aprovado pelo autor.**
Primeiro incremento do marco CD/rollback, após o fechamento da recuperação.
O código coordena um ensaio local explícito; não é ainda um pipeline de publicação
GitHub Actions nem uma implantação permanente em produção.

## O que o ensaio demonstra

A é uma cópia da release de referência já restaurada e aprovada. B recebe outra
identidade de release, com os **mesmos pesos, smoke e ambiente**. O manifesto B
registra `operational_repackage_of` e a finalidade do ensaio. Isso distingue as
respostas HTTP sem retreino, nova avaliação de qualidade ou promoção científica.
A e B usam o mesmo ID imutável da imagem de serving aceita em 2026-10-09.

O NGINX tem uma URL loopback própria para esta invocação. Começa em A, muda para B
e retorna a A. O ensaio não localiza nem substitui um serviço já existente.
A e B pertencem somente à rede Docker `--internal` exclusiva, sem portas
publicadas. Seus gates usam HTTP em `127.0.0.1:8000` dentro do próprio container,
via `docker exec`; o controller confere identidade, score e status das respostas.
O NGINX participa dessa rede e de uma segunda bridge de entrada. Somente ele
publica uma porta em `127.0.0.1`; a bridge de entrada tem prioridade de gateway 1.
Os probes HTTP ignoram proxies do ambiente. O cliente não usa MLflow, S3, datasets,
freeze/holdout ou credenciais de infraestrutura.

A sequência obrigatória é:

1. Conferir os pins externos do restore/modelo/imagem e os três arquivos da release.
2. Inicializar A com rootfs/modelo read-only, UID 10001 e limites já usados no checker.
3. Confirmar readiness, `/info`, score/identidade e rejeição HTTP 422; rotear A no gateway.
4. Corromper uma terceira cópia de teste. O preflight deve rejeitá-la antes de criar
   container ou trocar a rota; A continua respondendo.
5. Subir B e aprovar os mesmos gates diretamente antes da troca. Gerar configuração,
   executar `nginx -t`, enviar reload e comprovar B **pela URL do gateway**.
6. Parar somente o container B criado pelo ensaio. Observar HTTP 502 pelo gateway;
   não há failover implícito que oculte o teste.
7. O controle síncrono detecta a falha, confere A e recarrega a rota anterior.
   Readiness, identidade, score e 422 precisam passar pela mesma URL.
8. Remover somente os containers e as duas redes criados pela invocação e conferir que os
   bytes de entrada permaneceram iguais. Só então registrar `status=success`.

Falha no reload ou nos gates após a troca também tenta restaurar A e reprova o
ensaio. A rotina não é um watchdog permanente. A requisição de falha observada
é intencional; não há alegação de zero downtime, carga concorrente ou SLA.

## Reproduzir o gate explícito

Requer Docker Linux/amd64, a imagem de serving **já aceita** ainda presente no
engine local e a release restaurada do bundle
`a33bb2fbb3e9503b4bb96c92dd6950677127b77fbf5bedb4322007587176b154`.
A localização e os hashes são lidos da evidência versionada da recarga. Não
reexportar, retreinar ou reconstruir uma imagem diferente para contornar o pin.
Se o artefato fixado estiver ausente, interromper e preservar o diagnóstico.
Não é necessário subir MLflow ou RustFS. O gate já foi aprovado; estes comandos
servem para reprodução explícita e não precisam ser repetidos ao aplicar o
fechamento documental.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/serving \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py &&
docker pull nginx:1.30.6-alpine@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5 &&
.venv/bin/python -m tox -e serving-rollout
```

Alternativa ao último comando: `poetry run python -m
scripts.serving.blue_green_rehearsal rehearse`. Escolher uma alternativa, não ambas.
O runtime é iniciado por ID de imagem, com `--pull never`. O lock do gateway inclui
o digest do índice oficial e o manifesto amd64 consultados no registry.
O teste live é opt-in, fora de `env_list`; a CI comum não toca Docker.

## Resultados e persistência

`data/serving/rollouts/<uuid>/result.json` registra os IDs/digests das imagens,
identidades/hashes A/B, transições, falha 502, score após troca/rollback, duração
observada da recuperação e hashes do código. `candidate-manifest.json` permite
auditar o reempacotamento sem manter outra cópia permanente dos pesos.

O staging também fica dentro desse diretório gerenciado, é removido após o cleanup
e pode ser preservado ali se a remoção dos recursos falhar. O recibo permanece
com status failed nesse caso, com diagnóstico dos containers quando disponível.
Não usar comandos amplos como `docker system prune`; o controller conhece apenas
os nomes que criou. Diretório do job 0700, recibos 0600; somente as cópias montadas
read-only têm permissões de leitura para os UIDs dos containers. Originais não
recebem chmod/move/delete. Cada comando Docker tem timeout de 30 s, readiness
até 60 s por backend e gate HTTP de troca até 15 s; não há deadline total ou SLA
inferido da soma. SIGKILL/queda do daemon podem impedir o `finally`; inspecionar
somente os nomes da invocação caso sobrem recursos.

A preparação valida a máquina de estados, compensação, pins, caminhos e falhas de
cleanup sem daemon. Um teste opcional usa NGINX real e dois servidores HTTP com
releases geradas (`FRAUD_NGINX_BIN`); não substitui a prova Docker das imagens,
DNS, redes, montagens e permissões. O teste não instala nada no laptop.

## Limites do marco

Este primeiro gate prova troca e rollback de **empacotamento**, mantendo o mesmo
modelo/imagem. Não valida mudança de pesos, regressão de dependências entre duas
imagens distintas, canary sob carga, TLS/autenticação ou release permanente.
A continuação do marco usará esses controles no fluxo de entrega versionada.
KinD/Helm/PVCs seguem no roadmap e poderão substituir o provisionamento; a
identidade do modelo e os gates continuam separados do orquestrador.

Fontes primárias consultadas em 2026-10-19:
[NGINX reload e teste de configuração](https://nginx.org/en/docs/switches.html),
[comportamento da recarga](https://nginx.org/en/docs/beginners_guide.html),
[imagem oficial](https://hub.docker.com/_/nginx) e
[API do tag consultado](https://hub.docker.com/v2/repositories/library/nginx/tags/1.30.6-alpine).
A aprovação depende de HTTP/identidade/score observados, não apenas de um comando
reload ter retornado zero.

## Correção da conectividade do ensaio

Na primeira execução relatada, A estava saudável, mas a espera pelo HTTP do host
expirou antes do primeiro evento. Os recursos temporários foram removidos. O
desenho anterior exigia portas publicadas de backends ligados apenas à rede
`--internal`. A correção separa a entrada do gateway dos backends privados, sem
aumentar o timeout ou afrouxar os gates. A execução nativa posterior aprovou a
correção e o ensaio completo, conforme o aceite abaixo.

O gateway usa múltiplas opções `--network` e `gw-priority`, disponíveis no Docker
28 ou superior. Referência: [redes Docker](https://docs.docker.com/engine/network/).

## Aceite nativo — 2026-10-19

O autor aprovou `serving-rollout`: **1 teste em 21,42 s**, tox OK em 23,90 s.
A suíte `py314 -- tests/serving` aprovou **110 testes**, com **2 opt-in ignorados**,
em 4,28 s (tox 6,94 s); Ruff, formatação e lockfile também passaram.

| Verificação | Resultado informado |
| --- | --- |
| Rota inicial | A respondeu pela URL NGINX da invocação |
| Pacote inválido | Rejeitado antes de subir ou alterar a rota |
| Candidata | Gates diretos aprovados; B respondeu pela mesma URL |
| Falha injetada | B parada; HTTP 502 observado no gateway |
| Retorno | A restaurada pela mesma URL; `rollback_verified=true` |
| Score de B e após retorno | `0.4178630794049542` em ambos |
| Tempo observado do retorno | 3,100390515 s, nesta execução |
| Entrada e cleanup | Bytes da release intactos; recursos temporários removidos |

O recibo informado é
`data/serving/rollouts/b2aaa46392114d0c91a4b6459c858a52/result.json`. A URL
`http://127.0.0.1:32768` era temporária, não um endpoint persistente.
O [registro versionado](../../references/evidence/serving_rollout_author_validation_2026-10-19.json)
transcreve a saída do autor com o caminho do recibo relativo ao checkout.
Os hashes do controller/checker coincidem com o código entregue; os pins do
modelo, manifesto A, imagem e score coincidem com a evidência anterior. O
assistente não acessou os bytes do recibo ou o engine nativo independentemente.

O gate está encerrado no escopo de **reempacotamento com os mesmos pesos e
imagem**. Os 3,10 s não são percentil, SLA ou garantia geral de recuperação.
O ensaio não avalia nova qualidade estatística, outra revisão da imagem,
CD de produção, zero downtime ou implantação Kubernetes.

## Estratégia escolhida para o portfolio

Manter **blue-green**, com gates antes da troca e retorno verificável. O objetivo
é uma entrega reproduzível e explicável em um laboratório de baixo tráfego.
Quantidade de pods é apenas parte da decisão: um canary também pode usar poucas
réplicas quando há roteamento ponderado. Sem esse controle, a divisão aproximada
depende da proporção de réplicas.

| Aspecto | Decisão |
| --- | --- |
| Durante a operação | Planejar uma réplica ativa |
| Durante a entrega | Subir candidata, validar e promover explicitamente |
| Retorno rápido | Manter anterior disponível por uma janela a definir |
| Após essa janela | Permitir reduzir anterior a zero; novo retorno exige iniciá-la e validar sua saúde |
| Canary | Reavaliar se houver tráfego representativo e critérios de observação úteis |

Essa política de capacidade e retenção é **planejada**. O ensaio atual cria A, B e
gateway próprios e remove todos ao terminar; não mantém serviço ou release
anterior permanentemente disponíveis. Rótulos de fraude atrasados exigem uma
janela adequada para avaliar qualidade; saúde HTTP sozinha não comprova melhora
da detecção.

Referências para a comparação: [blue-green](https://argoproj.github.io/argo-rollouts/features/bluegreen/)
e [canary e divisão de tráfego](https://argoproj.github.io/argo-rollouts/features/canary/)
na documentação do Argo Rollouts, consultadas em 2026-10-19. São referências
conceituais; não implicam adoção de Argo Rollouts neste projeto.

## Próximo incremento

O primeiro incremento de entrega versionada está implementado na CI: build,
qualificação sintética e exportação da imagem exata, conforme a seção abaixo.
O gate local deste incremento foi aprovado; a execução na CI continua pendente.
O próximo recorte é consumir o artefato exportado em um alvo separado e definir a promoção
explícita de uma release aprovada. KinD + Helm permanece o destino de
provisionamento, com os mesmos controles de identidade e persistência. A política
permanente de retenção e os recursos Kubernetes ainda não estão implementados.

## Entrega versionada na CI — implementação de 2026-10-19

**Estado: build, gates e exportação aprovados nativamente pelo autor em
2026-10-19; execução no GitHub Actions pendente.** O aceite nativo anterior continua válido
para a imagem e referência fixadas nele; não aprova automaticamente uma imagem
construída de outro commit.

O novo job `serving-delivery` do workflow existente depende de `quality`. Usa
runner GitHub hospedado, sem conexão com o laptop, MLflow, RustFS ou datasets.
As permissões continuam `contents: read`; não há push de imagem, deploy remoto,
segredos de infraestrutura ou novo controlador de rollout.

A sequência é:

1. Exigir o SHA completo esperado igual ao HEAD e entradas de build/controle
   commitadas; guardar seus hashes. `.vscode/settings.json` fica fora desse recorte.
2. Puxar Python e NGINX pelos digests do lock e construir **uma única imagem**
   Linux/amd64 com o Dockerfile existente, rotulada com o commit. O build usa
   o lock científico; não altera dependências nem o pin histórico de serving.
3. Gerar uma release com 160 linhas sintéticas e pequeno HGB. Preparar somente
   essa fixture para o mount: diretório 0755 e três arquivos 0644, mantendo o
   diretório privado do job 0700 e os bytes/hashes intactos. Isso inclui fit
   da fixture; não lê ou retreina a referência histórica. A evidência é sempre
   `synthetic_smoke`, sem avaliação nova de fraude.
4. Aplicar o checker HTTP existente: identidade, score, 422, reinício, proteções
   do container e falhas de integridade. Reutilizar o controller blue-green
   aprovado para A → B → A com essa mesma imagem/release sintética.
5. Conferir novamente o ID antes de exportar `image.tar`, sem rebuild. O limite
   do arquivo é 1 GiB; o ID local não é apresentado como digest de registry.
6. Revalidar as entradas de origem, remover a tag exclusiva criada pelo job e
   publicar `delivery.json` somente após gates e cleanup. Em falha, guardar
   `failure.json` com a fase; nenhum descritor qualificado permanece.
7. Preservar a imagem exportada, release sintética, manifesto de origem, logs e
   recibos no artifact da execução. O nome inclui commit/run/tentativa e a
   retenção é **3 dias**, para inspeção deste laboratório; não é backup durável.

Os resultados ficam em `data/serving/deliveries/<uuid>/`. A CI também grava
`data/serving/delivery-summary.json` e apresenta o commit, ID local da imagem e
SHA-256 de `delivery.json` no resumo da execução. Imagens base e cache de build
podem permanecer no daemon; não há limpeza global do Docker. Se o cleanup dos
containers/redes falhar, o staging permanece no diretório do job para diagnóstico.
Em timeout abrupto do runner/daemon não há garantia de execução do cleanup.

### Verificação do pacote exportado

O comando `verify` compara hashes, presença dos arquivos, origem declarada e
coerência entre os recibos de HTTP/rollback. Não carrega `image.tar`, não
desserializa o modelo, não chama Docker e não verifica assinatura. O SHA esperado
do descritor deve vir do resumo da **execução escolhida no GitHub**, junto com o
commit completo; ler esse valor somente do próprio pacote não confirma origem.

Após baixar e extrair o artifact em um diretório novo dentro de
`data/serving/deliveries/`, usar os valores reais daquela execução:

```bash
poetry run python -m scripts.serving.delivery_gate verify \
  --directory "$PWD/data/serving/deliveries/from-actions-RUN_ID" \
  --expected-source-sha COMMIT_COMPLETO_DO_RESUMO \
  --expected-descriptor-sha256 SHA256_DO_DESCRITOR_NO_RESUMO
```

O comando `verify` continua somente offline. O descritor do produtor conserva
`archive_reload_validated=false`; o consumidor abaixo registra a recarga em um
recibo separado, depois de carregar e verificar o alvo. Nenhuma assinatura ou
attestation verificável é alegada por esse descritor.

### Gate local opt-in

A suíte padrão testa falhas e protocolo sem daemon. Para executar o mesmo build
e gate no host, primeiro revisar e commitar as entradas da entrega; depois:

```bash
.venv/bin/python -m tox -e serving-delivery
```

Esse opt-in constrói uma imagem, baixa bases fixadas e exporta um arquivo que
pode ter centenas de MiB. Não depende dos serviços de tracking e não substitui
o ensaio histórico da referência. Escolher uma primeira execução nativa **ou**
CI, sem necessidade de repetir ambos para cada alteração documental.

A CI roda nos eventos já existentes (push/PR/manual), após a suíte completa.
Nenhum arquivo é enviado ao GitHub pelo aplicador deste pacote; publicar a branch
é uma ação posterior do autor. Referências: [artifacts de workflow](https://docs.github.com/en/actions/tutorials/store-and-share-data),
[upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2) e
[build Docker](https://docs.docker.com/build/building/), consultadas em 2026-10-19.

### Correção das permissões da fixture — 2026-10-19

A primeira execução de `serving-delivery` construiu a imagem e chegou ao gate
HTTP, mas o container encerrou com código 3: UID 10001 recebeu PermissionError
ao ler `/model/manifest.json`. O gravador atômico publica os JSONs com modo
0600; o controller não havia normalizado a fixture para leitura por outro UID.
A suíte anterior controlava a resposta do checker e não verificava essa
pré-condição do mount. Não foi uma falha de rede ou falta de memória.

O controller agora valida hashes e `evidence_kind=synthetic_smoke` antes de
aplicar 0755 ao diretório da fixture recém-criada e 0644 aos três arquivos. Não
muda o gravador global, UID 10001, mount read-only, modelo histórico ou os bytes
do manifesto/modelo/controle. O job e os recibos mantêm suas permissões privadas.
A normalização ocorre antes do primeiro checker HTTP e tem fase própria no
recibo de falha. A nova execução cria outro job; o diretório da tentativa falha
é preservado. A correção precisa estar commitada antes do opt-in nativo.

Os testes de regressão partem de diretório 0700 e arquivos 0600, usam umasks
022 e 077 e exigem leitura/traversal para outro UID antes de chamar o checker.
Conferem hashes e modos da origem intactos e rejeitam uma release histórica
antes de chmod. São testes do contrato POSIX/controlador, não uma execução
Docker. O autor posteriormente aprovou o gate nativo completo após a correção,
conforme o aceite abaixo.

## Aceite nativo da entrega versionada — 2026-10-19

O autor executou `serving-delivery`: **1 teste aprovado em 62,04 s**, tox OK em
64,51 s. Após a correção de permissões, o build da imagem e os gates HTTP e
blue-green com fixture sintética foram concluídos. A própria execução verificou
o pacote exportado; rollback e remoção dos recursos temporários/tag exclusiva
foram confirmados. Não é necessário repetir o build para este fechamento.

| Identidade ou resultado | Valor informado |
| --- | --- |
| Commit de origem e rótulo da imagem | `0b95136a462583441044d50de0884a558777b260` |
| ID local da imagem | `sha256:bc2ba70a90de114613bd8679e79ccfbb7a17de048f72f16323ca26713e1dae8d` |
| SHA-256 do descritor | `aa0e79886800765c516d3ed776295dc46d657a7ea50eb7c88900241d8c0ff0d7` |
| Release sintética | `c298299b7d3540d3968b5e715937b923` |
| Arquivo exportado | 175.020.032 bytes; 11 arquivos inventariados no pacote |
| Rollback e cleanup | Ambos verificados; tag exclusiva removida |
| Fonte histórica e promoção | Não acessada/retreinada; sem promoção ou serviço permanente |

Diretório informado:
`data/serving/deliveries/1b85bede4ac24fc781491ef24eea0209`. O autor também
executou a leitura que compara o hash dos bytes de `delivery.json` com o valor
impresso pelo teste, antes de produzir o resumo publicado. O resumo omite a
lista `files`; o assistente não recebeu os bytes do descritor ou de `image.tar`
para conferência independente.

[Evidência do autor](../../references/evidence/serving_delivery_author_validation_2026-10-19.json).
O campo local `image.repo_digests` não comprova envio a um registry; o descritor
registra `registry_published=false` e `registry_digest=null`. A duração é uma
observação do ensaio, não SLA.

O aceite fecha o **gate local de build/qualificação/exportação**. A execução no
GitHub Actions e o upload/download do artifact remoto continuam sem aceite.
`archive_reload_validated=false`: verificar hashes não comprova recarga do tar.
O consumidor implementado abaixo usa esse arquivo em alvo separado, sem rebuild
ou fit, exigindo os pins do descritor, da imagem, do modelo e do HTTP. Seu aceite
nativo foi informado pelo autor abaixo; a execução remota da CI segue pendente. A referência histórica e o ensaio anterior ficam
preservados; não é uma nova avaliação estatística nem CD de produção.

## Consumo da imagem arquivada e CI entre jobs — 2026-10-19

**Gates nativo e GitHub Actions aprovados; recibos e limites nos aceites abaixo.**
O produtor já aprovado fica preservado. Este gate recebe o pacote qualificado,
carrega o arquivo exato e verifica um container novo, sem build nem fit.

| Job | Entrada e responsabilidade | Saída |
| --- | --- | --- |
| `quality` | Lint, formatação e suíte padrão | Aprovação do código |
| `serving-delivery` | Mesmo commit; build único e gates HTTP/blue-green sintéticos | Artifact com imagem/release/recibos; ID do artifact e hash do descritor nos outputs do job |
| `serving-consumer` | Outro runner; baixa somente o ID fornecido pelo produtor e compara SHA do commit e hash esperado | Recarga, identidade e HTTP verificados; recibo de consumo e diagnóstico |

O hash esperado vem do output do job produtor, separado dos bytes baixados;
não é recalculado do pacote para aceitar a própria entrada. As actions de
upload/download estão fixadas por SHA. A CI mantém push/PR/manual, permissão
`contents: read` e retenção de três dias. Não requer credenciais da infraestrutura
local nem contato com o laptop. O artifact é transporte temporário, não backup.

### Contrato do consumidor

1. Verificar descritor, inventário completo, tamanhos/hashes e coerência dos
   recibos, exigindo commit e hash esperados, **antes de chamar Docker**.
2. Exigir engine Linux 28+ local e que a tag exclusiva do arquivo esteja ausente.
   Se essa tag já existir, recusar sem sobrescrever ou apagar. O ID da imagem
   pode existir sob outras tags; registrar essa condição e preservá-las.
3. Executar `docker image load --platform linux/amd64 --input image.tar`;
   conferir ID exato, plataforma e rótulo OCI de revisão. O arquivo deve vir do
   produtor controlado e escolhido: este fluxo não aceita um tar arbitrário
   como código confiável apenas por possuir hashes internos.
4. Copiar a release sintética para o diretório privado do consumo. Normalizar
   somente essa cópia para leitura pelo UID 10001; bytes e permissões da entrega
   original permanecem intactos. Reutilizar o checker existente: HTTP, score,
   identidade, 422, reinício e rejeição de releases inválidas.
5. Revalidar os bytes originais e remover containers próprios e somente a tag
   importada, sem remoção por ID, `--force` ou limpeza global. Em falha, salvar
   fase e motivo; falha de cleanup impede sucesso. A cópia da fixture é removida
   após conclusão/cleanup do alvo; se o alvo falhar, fica no job para diagnóstico.

Resultados: `data/serving/consumptions/<uuid>/{result.json,runtime.json,load.log}`.
O recibo novo só marca `archive_reload_validated=true` após esses gates; não
reescreve o descritor do produtor. Camadas/cache e referências alheias podem
permanecer. Cancelamento abrupto ou perda do daemon pode impedir o cleanup.

No teste nativo, o container é novo no **mesmo engine**. Não é uma prova de cache
vazio ou engine independente. Na CI, os jobs são separados em runners hospedados;
a evidência de transferência entre eles depende do aceite remoto. Não se alega
assinatura, promoção da referência, zero downtime ou deploy permanente.

### Primeiro aceite nativo: consumir o pacote já aprovado

Depois de aplicar este incremento e passar a regressão:

```bash
.venv/bin/python -m tox -e serving-consumer
```

O teste lê os pins da evidência nativa anterior e consome
`data/serving/deliveries/1b85bede4ac24fc781491ef24eea0209` do commit
`0b95136a462583441044d50de0884a558777b260`. Não precisa repetir
`serving-delivery`, treinar, restaurar tracking ou iniciar MLflow/RustFS.
O código atual do consumidor tem hash registrado separadamente; não é necessário
que o HEAD atual seja o commit antigo da imagem para verificar essa entrega.

Para outra execução selecionada, fornecer seus pins externos:

```bash
poetry run python -m scripts.serving.delivery_consumer \
  --directory "$PWD/data/serving/deliveries/from-actions-RUN_ID" \
  --expected-source-sha COMMIT_COMPLETO_DA_EXECUCAO \
  --expected-descriptor-sha256 SHA256_DO_OUTPUT_DO_PRODUTOR
```

### Ativar o fluxo no GitHub

Após o aceite local, revisar e commitar o consumidor/workflow/testes e as
atualizações documentais pendentes, mantendo `.vscode/settings.json` separado.
O push da branch dispara a CI existente. Esse fluxo remoto cria sua própria
fixture e imagem do commit enviado; não transfere os dados locais do autor.
A sequência esperada é `quality` → `serving-delivery` → `serving-consumer`.
Aceitar somente os três jobs verdes, com artifact do produtor e recibo do
consumidor da mesma execução/commit. Uma aprovação local não marca Actions como
executado. O próximo recorte de CD será promoção explícita e provisionamento,
sem reabrir o histórico estatístico.

Referências consultadas em 2026-10-19:
[Docker image load](https://docs.docker.com/reference/cli/docker/image/load/),
[download-artifact v7](https://github.com/actions/download-artifact/releases/tag/v7.0.0),
[artifacts entre jobs](https://docs.github.com/en/actions/tutorials/store-and-share-data).

## Aceite nativo do consumo da imagem — 2026-10-19

O autor executou `serving-consumer`: **1 teste aprovado em 32,41 s**, tox OK em
44,93 s. O consumidor carregou o arquivo já qualificado, verificou identidade
por HTTP e removeu seus containers e a tag importada, sem build ou fit.
O hash do consumidor informado coincide com o código entregue.

| Verificação | Resultado informado |
| --- | --- |
| Origem | Commit `0b95136a462583441044d50de0884a558777b260`; mesmo descritor e imagem do aceite da entrega |
| Recarga | `archive_reload_validated=true`; ID da imagem ausente antes do load |
| Release / modelo | `c298299b7d3540d3968b5e715937b923`; SHA do modelo `33c6fbab88b0876fef76eb754ebe7c130294e6267d736452a38851e7c4e31106` |
| HTTP | Score sintético `0.03463335444035967`; entrada inválida retornou 422 |
| Preservação e cleanup | Bytes da entrega original intactos; tag importada e alvo próprios removidos |
| Escopo | Container novo no engine local; sem fit, referência histórica ou promoção |

Recibo informado:
`data/serving/consumptions/2e889985000047029e9dbabd8a477087/result.json`.
[Evidência do autor](../../references/evidence/serving_consumer_author_validation_2026-10-19.json).
O resultado foi recebido pelo chat; o assistente não acessou independentemente
o daemon, o arquivo de imagem, `runtime.json` ou `load.log`. O ID ausente antes
do load não prova inexistência de camadas em cache; `cold_cache_validated=false`.
A duração é observação deste teste, não SLA.

Os gates nativos de produção e consumo da entrega estão encerrados. Não repetir
build ou consumo para aplicar este fechamento documental. O próximo passo é
versionar as mudanças e publicar a branch para executar os três jobs da CI;
a transferência do artifact entre runners e seus recibos ainda requerem aceite.

## Aceite do fluxo no GitHub Actions — 2026-10-19

A [execução 39306262564, tentativa 1](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
foi acionada por push da branch `feat/storage-tracking-and-rollout` no commit
`6a3782b0ccb9d6c0a098251743483aed73e49079`. O assistente conferiu pela API os três
jobs concluídos com sucesso e os IDs distintos dos runners hospedados.

| Gate | Job / runner | Resultado |
| --- | --- | --- |
| Qualidade | 118244026514 / 1000000154 | Lint, formatação e testes aprovados |
| Produção da entrega | 118250132678 / 1000000155 | Build, HTTP/blue-green e exportação aprovados |
| Consumo da entrega | 118251573550 / 1000000156 | Download, recarga, identidade HTTP e cleanup aprovados |

| Identidade | Valor |
| --- | --- |
| Descritor SHA-256 | `cf53a5eeff7eb5b7f3cfc65f6465263815025873d991f0ee2363ae1275f98594` |
| ID local da imagem | `sha256:942aad4e1c3eb2865f64fa68a426b223f26a48cc1a5d8817f6851e43ea55a745` |
| Release sintética | `dee60d7d332e4521bc38245b6b4115e3` |
| Modelo SHA-256 | `9cc22e9bb439dc2a7c7811ae80ee461545e9ba241b0809a8e711cd3123a8c0e5` |
| Score HTTP sintético | `0.03463335444035967`; entrada inválida 422 |

O recibo registra arquivo original preservado, imagem ausente antes do load,
tag importada e alvo próprios removidos, sem rebuild ou fit no consumidor.
O produtor ajusta sua fixture sintética; esse resultado não reavalia fraude.

A API informou os artifacts:

| Artifact | ID | Bytes | Digest do ZIP |
| --- | --- | --- | --- |
| Entrega | 12214617466 | 556.756.319 | `sha256:2d9d3c2c70285a11a0965ec0f9dc16789fde48f6aa63981390d90fac3d93bd1a` |
| Consumo | 12213888677 | 3.047 | `sha256:bbbc16513b2245f591475f66236bd0e07aade4107c07dd65980b07e619ca8fb7` |

O assistente baixou somente o ZIP pequeno do consumo, conferiu seu digest e leu
`result.json` e `runtime.json`. As identidades e respostas coincidem; o hash do
controller corresponde ao código entregue. O tar de imagem e `delivery.json`
do produtor não foram baixados independentemente. O hash do descritor e o ID da
imagem do resumo enviado pelo autor coincidem com o recibo do consumidor.
Digest do artifact, hash de `delivery.json` e ID local da imagem são identidades
diferentes; nenhum deles comprova publicação em registry ou assinatura.

[Evidência estruturada](../../references/evidence/serving_actions_validation_2026-10-19.json).
Os artifacts têm expiração informada para 2026-10-22; a evidência versionada
preserva os metadados e recibos do consumidor sem incorporar a imagem ao Git.

Este aceite fecha `quality` → `serving-delivery` → `serving-consumer` entre
runners. Não é necessário repetir os gates locais para fechar a documentação.
Registry, promoção explícita, retenção da release anterior e provisionamento
permanente KinD/Helm permanecem etapas seguintes. Sem promoção da referência
histórica, migração de tracking ou CD de produção nesta execução.
