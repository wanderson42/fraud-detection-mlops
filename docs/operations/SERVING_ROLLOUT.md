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
A execução nativa/CI deste novo gate está pendente. Depois da aprovação, o próximo
recorte é consumir o artefato exportado em um alvo separado e definir a promoção
explícita de uma release aprovada. KinD + Helm permanece o destino de
provisionamento, com os mesmos controles de identidade e persistência. A política
permanente de retenção e os recursos Kubernetes ainda não estão implementados.

## Entrega versionada na CI — implementação de 2026-10-19

**Estado: implementada e testada com controles offline; build/gate Docker e
execução no GitHub Actions pendentes.** O aceite nativo anterior continua válido
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
3. Gerar uma release com 160 linhas sintéticas e pequeno HGB. Isso inclui fit
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

Não há `docker load` ou troca da referência neste incremento. Exportar a imagem
não comprova sua recarga em outro engine: `archive_reload_validated=false`. O
próximo gate de consumo deve carregar o arquivo verificado em alvo separado e
repetir identidade/HTTP antes de qualquer promoção explícita. Uma assinatura ou
attestation verificável também não é alegada por este descritor.

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
