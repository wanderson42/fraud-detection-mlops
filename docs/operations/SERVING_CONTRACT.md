# Serving de laboratório: contrato e execução

`precomputed_features_v1` recebe features prontas em `POST /score`. O autor exportou
a referência congelada e conferiu HTTP/wheel fora do checkout no Alienware.
O [relato nativo](../../references/evidence/laboratory_serving_native_validation_2026-10-09.json)
preserva identidade e paridade. O autor também concluiu a validação Docker;
o [resultado](#resultado-nativo-docker--2026-10-09) registra paridade, recuperação,
proteções, três falhas de carga e medições locais.
O gate offline autoriza revisão de laboratório; não promove o modelo em produção.

O [escopo estatístico exploratório](../modeling/EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento)
foi fechado após revisar a reamostragem nativa em 2026-10-09, mantendo explícita
a ausência de cobertura de generalização demonstrada. A exportação abaixo já foi
relatada com sucesso; preservar a release e os recibos de wheel e Docker.

## Entradas, saída e responsabilidade

| Elemento | Contrato implementado |
| --- | --- |
| Versão | `schema_version: 1` e `feature_contract_version: gold_v1` |
| Identidade do evento | `transaction_id`, `customer_id` e `terminal_id`: inteiros não negativos, metadados de correlação; não são preditores |
| Relógio | `tx_datetime` e `feature_as_of` iguais, em ISO 8601 sem offset, preservando o relógio simulado sem timezone; rejeitar conversões silenciosas |
| Features | Objeto `features` com exatamente as 19 chaves de `FEATURE_COLUMNS`; construir a matriz na ordem canônica, independentemente da ordem do JSON |
| Validação | Rejeitar ausências, extras, strings numéricas, booleanos, nulos e valores não finitos; manter tipos, domínios e coerências do contrato Gold |
| Fronteira dos rótulos | `TX_FRAUD`, `TX_FRAUD_SCENARIO` e `LABEL_AVAILABLE_AT` não pertencem à requisição de pontuação |
| Saída | `schema_version`, `transaction_id`, `score`, `score_semantics: uncalibrated_ranking`, `model_id`, `model_sha256`, `feature_contract_version` e identificador da release de serving |
| Score | Valor finito entre 0 e 1; sem classe, limiar automático, decisão de bloqueio ou seleção dos 100 clientes |
| Estado | Pontuação sem estado; históricos e rótulos atrasados são responsabilidade do produtor de features |

Domínios e relações seguem o [contrato da Gold](../data/GOLD_CONTRACT.md) e o
[dicionário](../data/DATA_DICTIONARY.md). Preservamos valores monetários zero; hora vai de
0 a 23 e dia ISO de 1 a 7. Contagens são inteiras não negativas, flags são 0/1 e
razões/taxas usam a mesma convenção de denominador zero e tolerâncias da Gold.
Não reinterpretamos unidades simuladas como moeda real.

O produtor deve usar eventos anteriores a `t` e rótulos com disponibilidade
estritamente anterior a `t`, incluindo o atraso de sete dias. Declarar
`feature_as_of = tx_datetime` não comprova causalidade: os testes de paridade do
produtor terão de demonstrá-la. Receber features prontas por HTTP demonstra o
serviço de pontuação; não demonstra ainda um pipeline de features online.

A fila de investigação diária é outra responsabilidade. Não incorporamos uma
política retrospectiva de dia completo ao endpoint como se fosse autorização
instantânea de pagamento. A futura fila por eventos terá contrato próprio.

IDs e contagens respeitam os inteiros físicos da Gold. Os relógios admitem até
nove dígitos fracionários; hora e dia da semana devem concordar com a transação.
Erro de entrada retorna HTTP 422 com campos/tipos de erro, sem ecoar os valores.

## Exportar a referência existente

Execute na raiz do projeto, depois de `poetry install` e `make validate`.
Preserve Gold, predições de validação, avaliação final e tracking originais.
O caminho abaixo identifica a avaliação já informada pelo autor:

```bash
EVALUATION_PATH="data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/final_evaluation_v1/6281337ac9ad872947470d94de0307d7c1934365538877376559d3dee2e57120"
poetry run python -m fraud_detection_mlops.integrations.serving_release_export export \
  "$EVALUATION_PATH" --project-root . --output data/serving/reference-v1 \
  > data/serving-export.json
cat data/serving-export.json
```

O exportador verifica os outputs **já salvos** da avaliação, os hashes congelados
do MLflow e a assinatura de features. Compara todas as linhas da validação fixada
após ida e volta por JSON, com tolerâncias absoluta/relativa de `1e-12` nos scores.
Preserva os bytes skops; não ajusta, não cria runs nem pontua novamente o teste.
Não consulta a reserva de setembro. Os hashes históricos de código no freeze
permanecem históricos: não executamos `verify-freeze` na árvore refatorada para
validar bytes antigos, nem alteramos o recibo para contornar essa diferença.

A distribuição contém `pipeline.skops`, `smoke.json` (um caso da validação,
sem rótulos) e `manifest.json`: identidade da release, origem, hashes, contrato,
quantidade de linhas comparadas e versões exatas do runtime. Esses arquivos são
operacionais, em `data/`, fora do Git. Guarde o JSON de exportação junto ao modelo;
o SHA-256 do manifesto será a identidade fixada para a carga.

O diretório de destino deve ser novo. Uma falha não autoriza refit: confira o erro,
restaure artefatos/versões originais e use outro destino após revisar uma exportação
incompleta. Nunca sobrescreva uma release utilizada. Uma cópia byte a byte, com
seu manifesto e SHA-256 fixado, pode ser transportada para outro caminho.

## Iniciar e conferir HTTP

```bash
export FRAUD_SERVING_RELEASE="$(poetry run python -c 'import json; print(json.load(open("data/serving-export.json"))["release_path"])')"
export FRAUD_SERVING_MANIFEST_SHA256="$(poetry run python -c 'import json; print(json.load(open("data/serving-export.json"))["manifest_sha256"])')"
poetry run uvicorn fraud_detection_mlops.serving.http_service:create_app \
  --factory --host 127.0.0.1 --port 8000 --workers 1
```

Em outro terminal, com as mesmas variáveis:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8000/info
poetry run python -c 'import json, os; from pathlib import Path; print(json.dumps(json.loads((Path(os.environ["FRAUD_SERVING_RELEASE"]) / "smoke.json").read_text())["request"]))' \
  | curl --fail --json @- http://127.0.0.1:8000/score
```

O modelo carrega uma vez no lifespan do serviço; hashes dos bytes, tipos aprovados,
ordem das features, versões e score de controle são conferidos antes de ficar
pronto. SHA incorreto, modelo ausente/alterado ou ambiente incompatível abortam a
inicialização. `/health` indica processo vivo; `/ready` exige modelo carregado;
`/info` expõe identidade e escopo. Antes da carga, pontuação retorna 503.
Uma falha do contrato de saída também retorna 503. Há quatro threads numéricas
e uma predição por vez por processo. Use um worker nesta etapa.

## Wheel e runtime de inferência

Poetry conserva a instalação padrão completa. `main` contém inferência;
`pipeline` contém ferramentas offline e `dev`, checks. Nenhuma versão previamente
fixada foi alterada pela divisão. O ambiente de exportação mantém MLflow; o
runtime HTTP não depende dele nem dos arquivos de `references/`.

```bash
make validate-serving-wheel \
  RELEASE_PATH="$FRAUD_SERVING_RELEASE" \
  MANIFEST_SHA256="$FRAUD_SERVING_MANIFEST_SHA256"
```

O check constrói o wheel, cria um ambiente temporário separado, confirma o alvo
Poetry **antes** de sincronizar apenas `main`, instala o wheel sem dependências
adicionais e inicia Uvicorn fora do checkout. Confere paridade HTTP e rejeição de
ID inválido, com MLflow, SHAP, DuckDB, Matplotlib, Optuna e Typer ausentes.
Isso não empacota os protocolos externos dos workflows offline.

Para exercitar apenas a infraestrutura sem artefatos reais:

```bash
poetry run python examples/serving_smoke.py --output data/serving/synthetic-v1
```

Esse exemplo ajusta um HGB em 160 registros gerados em memória e marca a release
como `synthetic_smoke`. Não é a referência; preserve essa identificação no relato.

## Resultado nativo da referência

Em 2026-10-09, o autor relatou exportação com **67.255 linhas de paridade**, sem
refit, e wheel funcionando fora do checkout com apenas as dependências `main`.
O score HTTP do evento `335910` foi `0.4178630794049542`, com tolerância `1e-12`.
Modelo: `9728f8dddc4daa9e5478db1a8a173adecb71e41c5992965c117d2b46a27625d9`;
release: `b3deb7e7bb6d4189bc7671453f8643d2`;
manifesto: `1dd762f7e25fde17ea83a00df5ab489e6885b85f0acd10a1a47da8804bed3263`.
MLflow, SHAP, DuckDB, Matplotlib, Optuna e Typer estavam ausentes.
O [relato](../../references/evidence/laboratory_serving_native_validation_2026-10-09.json)
registra saídas de terminal: o assistente não recebeu os bytes nativos nem o
SHA do wheel. O check mede paridade de um caso HTTP; a paridade das 67.255 linhas
pertence ao exportador. São verificações com alcances diferentes.

## Construir e verificar Docker

O Dockerfile fica em `docker/serving/`; o verificador operacional em
`scripts/serving/`. O contexto de build exclui dados, tracking, notebooks e testes.
O builder usa Poetry 2.4.3 e o lockfile; o runtime contém o wheel e as dependências
`main`. A imagem não contém modelo, Poetry ou checkout de desenvolvimento.
O pacote Python conserva seus módulos offline, cujas dependências não são instaladas.

Use Docker Linux com engine local acessível pelo usuário (socket Unix). Confira
`docker info` antes do build. O container usa UID/GID `10001:10001`, um worker e
quatro threads numéricas. A base `python:3.14.4-slim-trixie` conserva a versão exata
exigida pelo manifesto. A tag não é um pin de bytes: para reprodução da base,
forneça `PYTHON_IMAGE=python:3.14.4-slim-trixie@sha256:<digest verificado>`.
O recibo fixa o ID da imagem construída e registra os digests disponíveis.

O `docker build` atual usa Buildx/BuildKit. O comando explícito `docker buildx build`
permite selecionar builders e configurar outras saídas; a construção local
`linux/amd64` foi suficiente para esta validação. A documentação oficial de
[Docker Build](https://docs.docker.com/build/concepts/overview/) explica essa relação.

### Permissões da release no host

O container lê arquivos do host com UID/GID `10001:10001`. A gravação atômica dos
JSONs usa arquivos temporários privados; no diagnóstico nativo, o manifesto não
podia ser lido pelo serviço. Liberar leitura nos três arquivos e acesso ao
diretório resolveu a inicialização, conservando todos os hashes.

Para a release identificada, confira o manifesto e preserve um recibo dos hashes
antes de ajustar permissões. Execute na raiz do projeto:

```bash
(
  set -e
  serving_checksums_file="$(mktemp)"
  trap 'rm -f -- "$serving_checksums_file"' EXIT
  cd data/serving/reference-v1
  printf '%s  manifest.json\n' \
    "1dd762f7e25fde17ea83a00df5ab489e6885b85f0acd10a1a47da8804bed3263" \
    | sha256sum --check
  sha256sum manifest.json pipeline.skops smoke.json > "$serving_checksums_file"
  chmod a+rx .
  chmod a+r manifest.json pipeline.skops smoke.json
  sha256sum --check "$serving_checksums_file"
)
```

Esses comandos acrescentam leitura e acesso ao diretório, preservando os demais
bits existentes. A montagem permanece somente leitura no container. Esse ajuste
se aplica à release destinada ao laboratório; não muda a política de gravação
dos artefatos de dados e tracking. O recibo nativo reportou diretório `775`, JSONs
`644` e modelo `664`; as provas de escrita no container foram rejeitadas.

### Construção e execução

Na raiz do projeto, depois de aplicar o incremento e validar o software:

```bash
make build-serving-image
make validate-serving-docker \
  RELEASE_PATH="$PWD/data/serving/reference-v1" \
  MANIFEST_SHA256="1dd762f7e25fde17ea83a00df5ab489e6885b85f0acd10a1a47da8804bed3263"
```

O destino padrão é `data/serving/docker-check.json`, fora da release. O recibo deve
ser novo: em uma repetição, use `DOCKER_REPORT=data/serving/docker-check-02.json`.
Pode também fornecer `SERVING_IMAGE=<tag-ou-ID>` aos dois comandos. O verificador
resolve a tag para ID uma vez, sem puxar outra imagem durante os cenários.

| Check | Critério registrado |
| --- | --- |
| Inicialização | `/health`, `/ready`, `/info` e HEALTHCHECK aprovados; identidade da release correta |
| Runtime | UID/GID `10001`, módulo instalado em `/opt/runtime`, dependências offline ausentes |
| Proteções | Raiz read-only, montagem `/model` read-only, capabilities removidas e no-new-privileges; tentativas de escrita rejeitadas |
| Paridade HTTP | Caso de `smoke.json` com erro absoluto ≤ `1e-12`; entrada inválida retorna 422 |
| Recuperação | Reinício controlado, readiness recuperado e mesma resposta de controle |
| Falhas de carga | SHA do manifesto incorreto, modelo alterado e modelo ausente abortam por erro do contrato; OOM/ImportError não contam como aprovação |
| Preservação | Arquivos originais com os mesmos hashes; corrupção ocorre em cópias temporárias |
| Recursos | Tamanho da imagem, snapshot de memória/CPU e latências p50/p95/p99 após dez requisições de aquecimento |

São 100 requisições sequenciais ao mesmo caso, incluindo transporte HTTP local;
não são um teste de carga ou SLA. `docker stats` é um snapshot, não pico de RSS.
Os limites declarados são 2 GiB, quatro CPUs, 128 processos e tmpfs de 64 MiB.
O engine precisa suportar os limites de memória, swap e CPU. A porta publicada
usa apenas `127.0.0.1`, com alocação dinâmica, sem disputar a porta de um serviço
existente. Não há política de reinício automático nesta medição.

Os comandos Docker têm timeout de 30 s; readiness e falhas de carga têm deadline
de 60 s por cenário. Falhas encerram o check sem emitir recibo de sucesso. O
cleanup remove apenas containers com nomes únicos criados nesta invocação;
não executa prune. Se houver encerramento externo abrupto, identifique o container
`fraud-serving-check-...` daquele check antes de removê-lo. Se o modelo não puder
ser lido por UID 10001, revise permissões de leitura da release; não execute como
root nem altere seus bytes para contornar a falha.

Uma divergência de ambiente exige restaurar a versão fixada, sem atualizar o
manifesto para fazer o check passar. A execução não abre Gold/tracking, não ajusta
o modelo e não consome a janela de replay. O recibo nativo foi revisado;
O PR #3 integrou Docker. O primeiro fluxo Prefect está descrito abaixo.

Referências oficiais: [build em estágios](https://docs.docker.com/build/building/multi-stage/),
[opções de execução](https://docs.docker.com/reference/cli/docker/container/run/) e
[Dockerfile/HEALTHCHECK](https://docs.docker.com/reference/dockerfile/).

## Evidências e limites

[Preparação](../../references/evidence/laboratory_serving_preparation_2026-10-09.json):
paridade HTTP sintética e exportação nativa MLflow/skops em 160 linhas de validação,
com fit bloqueado e partições de treino/teste removidas antes da exportação.
O wheel pontuou num runtime isolado. A avaliação final original continua preservada.

A etapa estatística foi integrada pelo [PR #2](https://github.com/wanderson42/fraud-detection-mlops/pull/2),
com 379 testes nativos aprovados e CI da revisão final e de main aprovadas. Este
incremento Docker usa essa base. A preparação não dispõe de engine; o autor
executou o build/run no Alienware e forneceu o recibo de sucesso. A
[etapa 16](../../notebooks/stages/16_laboratory_docker.ipynb) acompanha a entrega. O serviço não define autenticação, TLS, fila de investigação
ou objetivo de disponibilidade para produção.

## Resultado nativo Docker — 2026-10-09

O autor forneceu a saída de `data/serving/docker-check.json`, registrada às
23:42:29 UTC de 2026-10-09 (20:42:29 em Belém). O procedimento de fechamento
arquiva esse arquivo após conferir sua identidade e a integridade da release:
[recibo versionado](../../references/evidence/laboratory_serving_docker_native_validation_2026-10-09.json).
O resultado é uma execução nativa do autor; o assistente não executou Docker
nem recebeu os bytes do modelo.

| Item | Resultado |
| --- | --- |
| Imagem | `sha256:db2e2ed60c2b3102041d55a93ca5dba0b0d9c5624c14bd7581947c1de0b600ab`, Linux amd64 |
| Tamanho reportado pelo engine | 174.861.582 bytes, aproximadamente 166,8 MiB |
| Runtime | Python 3.14.4, UID/GID 10001; ferramentas offline ausentes |
| Paridade HTTP | Evento 335910, score 0,4178630794049542, tolerância absoluta 1e-12 |
| Entrada inválida | HTTP 422 |
| Recuperação | Reinício aprovado e mesma resposta de controle |
| Integridade | Manifesto incorreto, modelo corrompido e modelo ausente abortaram com exit code 3 e motivo esperado |
| Proteções | Provas de escrita rejeitadas; arquivos originais conservaram os hashes |
| Recursos | Limites de 2 GiB e quatro CPUs; snapshot de 178,5 MiB, sem pico medido |
| Latência HTTP | Média 4,37 ms; p50 4,32 ms; p95 4,74 ms; p99 5,09 ms |

São dez requisições de aquecimento e 100 sequenciais ao mesmo caso. Os percentis
incluem HTTP local; não validam carga concorrente ou SLA. O tamanho é o valor
reportado pelo engine, não uma medição do tamanho comprimido em registry.
Não houve refit, promoção em produção ou abertura da janela de replay.

O fechamento preserva três correções operacionais: chave JSON `CpuCfsQuota` do
engine, captura de stdout/stderr e estado de saída, e permissões de leitura da
release. O aviso do TestClient é tratado com `httpx2` no grupo `dev` e um check
que converte a depreciação do Starlette em erro. A suíte final e a CI da revisão
publicada são verificações próprias, necessárias antes da integração.


## Primeiro fluxo Prefect

Base: `main` após o PR #3 (`42f23a6`). Prefect **3.8.8** fica no grupo
`orchestration`; a imagem continua instalando somente `main`. A adição ao lockfile
conservou todas as versões anteriores. Não é necessário reconstruir a imagem
validada para executar este fluxo no host.

`operations/serving_validation.py` pode ser chamado diretamente, sem Prefect.
O adaptador em `orchestration/serving_validation_flow.py` fornece histórico de
flow/tasks e retries. Ele inspeciona a release sem carregar o modelo, consulta
`/health`, `/ready`, `/info` e pontua **apenas o caso de controle** via `/score`.
O recibo vincula ID de operação, hashes do manifesto e controle, identidade da
release, URL, hash do código da operação, resultado e horário UTC. Não lê datasets,
não ajusta o modelo, não cria runs MLflow e não abre 16–30/09.

### Iniciar os três processos locais

Use três terminais na raiz do projeto. Instale os grupos necessários com
`poetry sync --only main,pipeline,orchestration,dev`. Mantenha as portas 4200 e 8000
livres. Não interrompa outros serviços para liberá-las automaticamente.

**Terminal 1 — servidor Prefect:**

```bash
export PREFECT_HOME="$PWD/data/orchestration/prefect"
export PREFECT_SERVER_ANALYTICS_ENABLED=false
poetry run prefect server start --host 127.0.0.1 --port 4200
```

UI: `http://127.0.0.1:4200`. O SQLite do laboratório fica no `PREFECT_HOME` local;
interromper esse processo não apaga o histórico. Não há worker ou agendamento
neste incremento: o comando do fluxo inicia a execução diretamente.

**Terminal 2 — serviço existente:** use o ID da imagem registrado no check nativo.
O bind mount é somente para leitura e os arquivos devem ser legíveis pelo UID
10001, conforme o procedimento Docker anterior.

```bash
docker run --rm --name fraud-serving-prefect-lab --pull=never \
  --read-only --cap-drop=ALL --security-opt no-new-privileges \
  --memory=2g --memory-swap=2g --cpus=4 --pids-limit=128 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --publish 127.0.0.1:8000:8000 \
  --mount "type=bind,src=$PWD/data/serving/reference-v1,dst=/model,readonly" \
  --env FRAUD_SERVING_MANIFEST_SHA256=1dd762f7e25fde17ea83a00df5ab489e6885b85f0acd10a1a47da8804bed3263 \
  sha256:db2e2ed60c2b3102041d55a93ca5dba0b0d9c5624c14bd7581947c1de0b600ab
```

**Terminal 3 — fluxo:**

```bash
export PREFECT_API_URL=http://127.0.0.1:4200/api
export PREFECT_HOME="$PWD/data/orchestration/prefect"
export PREFECT_SERVER_ANALYTICS_ENABLED=false
poetry run python -m fraud_detection_mlops.orchestration.serving_validation_flow \
  --release "$PWD/data/serving/reference-v1" \
  --manifest-sha256 1dd762f7e25fde17ea83a00df5ab489e6885b85f0acd10a1a47da8804bed3263 \
  --service-url http://127.0.0.1:8000 \
  --operation-id reference-control-001 \
  --output-root "$PWD/data/orchestration/serving"
```

Sucesso: `status: success`, `reused: false` e um arquivo
`data/orchestration/serving/reference-control-001/receipt.json`. Repetir exatamente
o comando produz `reused: true` e conserva os mesmos bytes do recibo. **Isso retoma
uma verificação anterior; não comprova a saúde atual.** Para outra verificação,
use um ID novo, como `reference-control-002`.

Se o processo parar depois da publicação e antes de registrar sucesso no Prefect,
reexecutar o ID encontra o recibo íntegro. Um ID já existente com outra release,
URL ou código da operação falha sem sobrescrever evidência. Preserve o conflito
para revisão; não apague recibos para contornar a verificação.

### Falhas e alcance

| Situação | Comportamento |
| --- | --- |
| Timeout, conexão indisponível, HTTP 408/429/500/502/503/504 transitório | Só a task HTTP repete: até três tentativas, pausas de 1 e 2 segundos, timeout de 5 segundos por requisição |
| HTTP 503 com `model_contract_failure`, demais erros HTTP ou JSON inválido | Encerra sem retry e sem recibo de sucesso |
| Hash, features, readiness, `/info` ou score divergentes | Encerra sem retry; tolerância absoluta do controle `1e-12` |
| Release alterada entre inspeção e publicação | Nova checagem rejeita a publicação |
| Falha ao gravar o recibo | Encerra sem retry automático; uma nova execução pode retomar após resolver a causa |
| Publicadores concorrentes do mesmo ID | Um único JSON completo é publicado; o outro aceita somente um recibo compatível |

A publicação usa arquivo temporário e hard link sem sobrescrita no filesystem
local. Consultas HTTP de controle podem se repetir sob concorrência ou interrupção
antes da publicação: não há promessa de execução única distribuída ou durabilidade
contra falta de energia. Não use esse mecanismo como contrato de eventos ou
exatamente uma entrega do futuro streaming. Resultados Prefect não usam cache ou
persistência de payload; o recibo da operação é a referência da retomada.

A CLI exige API Prefect e serviço em HTTP loopback com porta explícita. Não segue
redirecionamentos nem usa proxy para consultar o serviço. A execução nativa com a
referência real ainda está pendente; a preparação usa releases sintéticas e backend
Prefect isolado. O fluxo não valida SLA, carga concorrente de inferência ou promoção.

Referências: [retries](https://docs.prefect.io/v3/how-to-guides/workflows/retries),
[testes de workflows](https://docs.prefect.io/v3/how-to-guides/workflows/test-workflows)
e [servidor local](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli).
