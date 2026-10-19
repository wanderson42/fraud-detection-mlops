# Armazenamento, persistência e recuperação

Atualizado em **2026-10-19**. Esta decisão define o destino de datasets, artefatos,
estado e métricas. Inventário e piloto limitado de upload/restauração estão implementados e testados
com arquivos gerados e S3 simulado. O autor também validou inventário completo
e upload/reenvio/restauração de documentos e da release completa em RustFS. Nenhuma camada
inteira está declarada migrada. A recarga do modelo restaurado passou no host
do autor. Também foram aprovados o build do job e o arquivamento/recuperação
completos da Bronze: 189 objetos, reenvio sem uploads e 183 partições verificadas.
A Silver também teve arquivamento/recuperação aprovados com contrato/schema/SQL:
188 objetos, 187 arquivos e 183 partições. `gold_v1` teve preparo/publicação e
recuperação aprovados: 49 objetos, 48 arquivos, 42 partições/19 features/402.877
linhas. O dataset de desenvolvimento também foi arquivado/recuperado pelo autor:
67 objetos, 66 arquivos e 63 partições/19 features/603.669 linhas. Adoção pelos
consumidores, cobertura adicional e migração operacional do tracking seguem abertas.
O uso local do histórico e da referência pelo SDK MLflow foi validado depois,
conforme o registro ao final. O estudo Optuna
concluído também teve recuperação S3 aprovada em 2026-10-16, conforme abaixo.

## Objetivo e estado atual

Cada conjunto precisa ter um responsável, endereço conhecido, identidade,
política de retenção e forma de recuperação. Arquivos em Parquet continuam sendo
uma forma válida de persistência quando têm manifesto e ciclo de vida gerenciado.
Colocar todas as tabelas em um servidor SQL não substitui esses controles.

Hoje, Bronze/Silver/Gold e resultados ficam em `data/`, ignorado pelo Git;
MLflow usa SQLite e artefatos locais; o replay usa dois bancos SQLite; Prometheus
e Grafana já têm volumes Docker nomeados. O RustFS tem perfil opcional fixado por
digest. O piloto está em `scripts/storage/archive.py`; integração real S3 validada pelo autor para documentos e quatro arquivos da release.
A recarga da release restaurada foi validada pelo autor via pytest/TestClient,
com identidade histórica preservada e erro absoluto zero no score de controle.
A suíte padrão não inicia Docker nem transfere dados nativos.

**Decisão:** RustFS local via API S3 para cópias versionadas de datasets e artefatos;
disco persistente para bancos ativos; DuckDB para consultar dados analíticos.
**PostgreSQL é o destino escolhido para o backend do MLflow**, com servidor e
backup próprios. A migração de runs/URIs será um incremento versionado separado.
SQLite continua adequado ao checkpoint transacional local do replay. A feature
store permanece condicional à necessidade demonstrada.
KinD + Helm é o destino final de provisionamento, após restauração e rollback.

## Responsabilidades por conjunto

| Conjunto | Persistência atual | Destino e recuperação previstos | Papel da ferramenta |
| --- | --- | --- | --- |
| Bronze | `data/raw/handbook/<source_commit>/` | Objetos imutáveis em RustFS com bytes originais, manifesto e auditorias; restauração por checksum | S3 guarda arquivos; não desserializar/regravar o pickle durante a cópia |
| Silver | `data/interim/handbook/<source_commit>/silver_v1/` | Parquets e metadados versionados em RustFS; leitura de volta, schema e contagens reconciliados | DuckDB consulta os Parquets; não exige duplicar todas as linhas em PostgreSQL |
| Gold e variantes de desenvolvimento | `data/processed/handbook/<source_commit>/` | Cada versão/run com suas features, splits, manifestos e proveniência preservados em RustFS | Dados derivados continuam com contrato causal e identidade própria |
| Modelos, scores e relatórios | Releases, experimentos e avaliações em `data/` | RustFS com IDs/hash de manifesto; modelo, transformações e evidências preservados juntos | Armazenamento de artefatos, separado dos metadados transacionais |
| Metadados MLflow | `data/tracking/mlflow.db` | PostgreSQL como destino da nova infraestrutura; preservar o SQLite atual até migrar e conferir runs, métricas, parâmetros e referências | Banco de runs, parâmetros e métricas; os arquivos grandes pertencem ao artifact store |
| Artefatos MLflow | `data/tracking/artifacts/` | Evolução para artifact store S3, com migração própria e teste de recarga | As URIs já registradas no banco não mudam só porque arquivos foram copiados |
| Estado e telemetria do replay | `checkpoint.sqlite`, `telemetry.sqlite`, `metadata.json` e avaliação | Bancos ativos em filesystem persistente; conjunto consistente arquivado/restaurado via RustFS | SQLite fornece transações/locking; S3 armazena o backup, não o banco ativo |
| Séries Prometheus | Volume Docker `prometheus-data` | Banco de séries temporais (TSDB) local com retenção atual de 48 h/512 MB; agregados importantes preservados nos relatórios de avaliação | Banco de séries temporais operacionais; não é catálogo de datasets nem evidência de longo prazo |
| Grafana | Provisioning/dashboards no Git; estado no volume `grafana-data` | Recriar configuração por arquivos versionados; backup do banco de estado se houver dados de UI que precisem ser mantidos | Visualização; não é fonte de verdade dos eventos ou scores |
| Feature store eventual | Não implantada | Offline poderá reutilizar Parquet/S3; online terá backend próprio escolhido no spike | Gestão de definições, reutilização, disponibilidade e materialização, não substituição de toda a persistência |

Os paths acima descrevem famílias. O comando `inventory` registra todos os
arquivos regulares sob `data/`, incluindo Gold de treino/desenvolvimento, estudos,
tracking, releases e ensaios; exclui apenas sua própria área `data/storage/`.
Famílias não reconhecidas aparecem como `other_review_required`, nunca somem da
cobertura. Volumes Docker não são inspecionados por esse inventário. Nenhum conjunto desaparece
do plano por não fazer parte do primeiro teste de restauração.

## Organização S3 proposta

Dois buckets locais separam os ciclos de vida de dados e resultados:

- `fraud-datasets`: Bronze, Silver, Gold e seus manifestos/auditorias.
- `fraud-artifacts`: releases, outputs de experimentos, avaliações e backups de estado.

Dentro de cada conjunto, preservar os caminhos relativos e bytes já verificados.
O arquivamento Bronze implementado usa
`s3://fraud-datasets/bronze/v1/<bundle_id>/`, mantendo dentro desse prefixo os
paths `data/raw/handbook/<source_commit>/` e `references/handbook_source.json`.
Silver/Gold usam prefixos por versão/conjunto com a mesma preservação de paths;
Silver v1 e Gold v1 foram arquivadas/recuperadas pelo autor. IDs derivados do manifesto identificam
cada conjunto, sem vincular sua identidade a um diretório absoluto do laptop.
Não usar uma chave mutável `latest` como única identificação de um artefato.

Um manifesto de arquivamento deve relacionar: origem local relativa, bucket/key,
versão/run de origem, tamanho, SHA-256 e identidade do manifesto original. Publicar
o manifesto de conclusão somente depois de conferir os objetos; um upload parcial
não deve aparecer como um conjunto completo. Reenvio dos mesmos bytes reutiliza a
cópia; conteúdo diferente na mesma identidade deve falhar, preservando ambos.
ETag não deve ser tratado como SHA-256 do arquivo.

A validação real deve confirmar PUT condicional/HEAD/GET/listagem e o endpoint/cliente
contra a imagem RustFS fixada. O piloto usa `fraud-artifacts/pilot/v1/<bundle_sha256>/`,
com os caminhos originais sob esse prefixo. O novo cliente Bronze prepara a
publicação em `fraud-datasets`; o autor validou seu round-trip completo para a Bronze. Recursos como versionamento do bucket, lifecycle
e object lock só serão usados depois de testados nessa versão. Credenciais
permanecem fora do Git. Os perfis continuam opcionais e nunca são iniciados pelo inventário.

## DuckDB, PostgreSQL e feature store

**DuckDB** já realiza consultas e reconciliações no projeto. Pode ler Parquet
local e, com a extensão/configuração necessárias, S3 compatível. Arquivos de
catálogo, cache e temporários terão diretório/limpeza explícitos; resultados
publicados não devem depender de um cache esquecido. A leitura S3 direta é uma
evolução posterior, sem modificar agora os leitores congelados.

**PostgreSQL** foi escolhido para o backend do futuro servidor MLflow: estabelece
um serviço independente dos jobs, com persistência e backup explícitos, e segue
a experiência operacional do autor. Isso não exige transferir Parquets ou o estado
do replay para SQL. O SQLite atual fica preservado até uma migração com reconciliação
de runs/IDs, métricas, parâmetros e URIs, teste de recarga e retorno ao backend anterior.
A integração atual força URI SQLite e artefatos locais: mudar uma variável de
ambiente ou executar `mlflow db upgrade` não transfere os registros entre bancos.

**Feature store** exige consumidor adicional, joins point-in-time corretos,
distinção entre instante de ocorrência e disponibilidade do feedback, frescor,
recuperação, paridade e custo. O offline pode reutilizar o armazenamento S3;
o backend online será escolhido se o spike demonstrar necessidade. Não pressupomos
Redis ou PostgreSQL apenas para completar a arquitetura.

## Piloto implementado — execução gradual

O inventário lê somente metadados (tamanho, caminho e mtime) de todo o `data/`,
sem teto de tempo ou quantidade de arquivos. Erro de leitura ou symlink tornam
`complete=false`.
É uma fotografia operacional, sem garantia de consistência de um escritor ativo.
Os relatórios ficam em `data/storage/inventory/`; a saída do terminal é resumida.

Primeiro, executar apenas isto na raiz do projeto:

```bash
poetry run python -m scripts.storage.archive inventory
```

Revisar bytes, famílias, espaço livre e eventuais lacunas antes de preparar dados.
Não inicia serviços, não lê pickle/Parquet/modelos e não recalcula features/scores.

Depois da revisão, preparar um conjunto pequeno **explicitamente selecionado**.
O primeiro teste pode usar apenas os dois arquivos de avaliação concluída:

```bash
poetry run python -m scripts.storage.archive prepare \
  --include data/replay/paced-native-v1/assessment/result.json \
  --include data/replay/paced-native-v1/assessment/receipt.json
```

Conferir os nomes no inventário antes do comando; a rotina recusa arquivos ausentes.
Para incluir uma release, adicionar seu diretório real com `--include`, preservando
todos os arquivos associados. Não selecionar `data/` inteiro. Limites fixos: **64
arquivos e 64 MiB**, cópia sequencial em blocos de 1 MiB; sem multiprocessing. Não
há opção para ampliar silenciosamente o orçamento. Fontes mutantes, symlinks,
bancos SQLite/DuckDB e arquivos de journal são recusados neste piloto. Backups
consistentes dos bancos e expansão para datasets serão incrementos posteriores.

O preparo não usa Docker: cria cópia e manifesto em
`data/storage/bundles/<bundle_id>/`. A identidade vem de paths, tamanhos e SHA-256;
repetir os mesmos bytes reutiliza o bundle. Não carregar/desserializar o modelo.
Uma cópia do conjunto e sua restauração consomem espaço adicional; originais não
são removidos. O comando faz uma verificação prévia de espaço disponível.

Somente quando for executar o piloto real, iniciar **apenas RustFS** e obter o ID
emitido por `prepare` (substituir `<bundle_id>` abaixo). Os comandos são etapas
separadas; conferir cada resultado antes do próximo:

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml --profile storage up -d --no-deps rustfs
```

Esperar RustFS saudável no `docker compose ... --profile storage ps rustfs`.
Não usar `local_stack up --storage` para este teste limitado: esse comando engloba
os serviços de monitoramento. O cliente usa imagem oficial AWS CLI fixada por
digest, perfil `storage-tools`, endpoint interno `http://rustfs:9000`, path-style,
UID/GID do autor e credenciais já existentes no `.env` ignorado. Não usa conta AWS,
credenciais do host ou pip install; dependências científicas congeladas não mudam.
Limites por cliente: 256 MiB/0,5 CPU; RustFS: 1 GiB/1 CPU. Há download inicial da
imagem e cada operação é sequencial, com timeouts/retries limitados.

```bash
poetry run python -m scripts.storage.archive publish --bundle-id <bundle_id>
```

PUT é condicional (`If-None-Match: *`), sem sobrescrever um objeto existente.
Cada objeto é conferido via HEAD e download+SHA-256; ETag não vale checksum.
Falha de autenticação/transporte não é interpretada como ausência. O manifesto é
o último objeto publicado, depois dos dados conferidos. Erro interrompe o fluxo;
um reenvio reutiliza somente bytes já verificados. Listagem final também é conferida.

```bash
poetry run python -m scripts.storage.archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.archive restore --bundle-id <bundle_id>
```

A segunda publicação deve reportar zero uploads e todos os objetos reutilizados.
A restauração baixa o manifesto, confere identidade, orçamento, paths, tamanhos e
hashes e publica somente o diretório completo em
`data/storage/restored/<bundle_id>/`. Esse destino deve estar ausente: repetir a
restauração falha preservando a cópia anterior. Staging temporário de verificação
é removido ao sair; uploads parciais remotos são preservados para retomada. Recibos
bem-sucedidos ficam em `data/storage/receipts/`. Não há DELETE automático.

Este teste comprova integridade dos bytes, **não recarga do modelo, migração de
URIs históricas, backup de bancos ou cobertura de toda a camada**. O bucket é
local e não fornece uma cópia fisicamente independente. Registrar o resultado
real antes de ampliar volume ou considerar o marco completo.

## Resultado informado pelo autor — 2026-10-15

O inventário terminou com `complete=true`, zero ocorrências e **1.643 arquivos /
3.132.647.292 bytes (2,92 GiB)**. Foram informados 541.857.722.368 bytes livres.
Isso confirma cobertura de metadados no escopo `data/` declarado, sem assegurar
consistência de bancos ativos ou classificação de todos os usos.

| Família | Arquivos | Bytes |
| --- | ---: | ---: |
| Bronze | 188 | 107.269.302 |
| Silver | 198 | 40.802.918 |
| Gold e variantes | 506 | 326.821.198 |
| Replay e medições | 104 | 2.401.048.316 |
| MLflow | 637 | 255.927.327 |
| Classificação pendente | 10 | 778.231 |

O autor depois identificou os dez arquivos: três da release, um recibo de
exportação, dois relatórios/logs Docker, três arquivos do exemplo de interface
e um `.gitkeep` vazio. A release está em `data/serving/reference-v1/`. Essa
interpretação não altera o inventário histórico; o classificador de prefixos
ainda os apresenta como `other_review_required`.

RustFS respondeu HTTP 200 em `/health/ready`. O bundle
`c275e438ca7e63fa716d08dd3cdf3b7f20522637b1f06dc522b7b87ee9657d76`
incluiu `assessment/result.json` e `assessment/receipt.json` do ensaio
`paced-native-v1`. A primeira publicação enviou e verificou três objetos
(dois documentos + manifesto); a segunda reutilizou os três, com **zero uploads**.
A restauração concluiu com dois arquivos e **17.559 bytes**, em diretório separado,
após os controles de identidade, tamanho, paths e SHA-256 da rotina.

O autor informou também 36 testes de armazenamento aprovados em 0,06 s. Foram
recebidas saídas do terminal; o assistente não recebeu os bytes dos relatórios,
manifesto ou objetos para recalcular seus hashes independentemente. O resultado
não abrange recarga de modelo, datasets ou backups SQLite.
[Evidência e caminhos dos relatórios](../../references/evidence/storage_pilot_author_validation_2026-10-15.json).

## Release completa: recuperação de bytes e recarga

O autor preparou o bundle
`a33bb2fbb3e9503b4bb96c92dd6950677127b77fbf5bedb4322007587176b154`
com os três arquivos de `data/serving/reference-v1/` e `data/serving-export.json`.
A primeira publicação enviou/verificou cinco objetos (quatro arquivos + manifesto);
a repetição reutilizou os cinco com zero uploads. A restauração concluiu com
**quatro arquivos / 735.485 bytes** em outro diretório. Foram recebidas saídas de
terminal, sem inspeção independente dos objetos pelo assistente.
[Evidência do autor](../../references/evidence/storage_release_archive_author_validation_2026-10-15.json).

O autor também aprovou o gate de recarga abaixo: **1 passed, 6 deselected em
0,32 s**, score `0.4178630794049542`, erro absoluto `0.0`, readiness 200 e
entrada inválida 422. O modelo e manifesto mantiveram seus hashes históricos.
[Evidência e limites](../../references/evidence/storage_release_reload_author_validation_2026-10-15.json).

O teste permanece explícito para verificações futuras. Ele
usa `create_app`/`load_release` existentes, obtém o SHA do manifesto da evidência
histórica versionada (fora do bundle) e lê somente a release restaurada. Confere
arquivo por arquivo, ambiente, tipos aprovados, identidade, readiness HTTP 200,
score de controle com erro absoluto até `1e-12` e entrada inválida HTTP 422.

```bash
FRAUD_STORAGE_RELEASE_LIVE=1 \
FRAUD_STORAGE_RELEASE_BUNDLE=a33bb2fbb3e9503b4bb96c92dd6950677127b77fbf5bedb4322007587176b154 \
poetry run pytest -q -s tests/storage/test_restored_release.py -k native
```

Score histórico e observado: `0.4178630794049542`; release preservada:
`b3deb7e7bb6d4189bc7671453f8643d2`. O teste imprime o resultado e preserva
recibo em `data/storage/receipts/release-reload-*.json` após sucesso. O relatório
informado foi `release-reload-ckbmqt6l.json`. O assistente recebeu o JSON impresso
e o resumo pytest, sem os bytes do modelo/relatório para recalcular hashes.

O HTTP é exercitado em processo com FastAPI TestClient, sem porta TCP ou novo
container. Esse gate não acessa S3 nem a release original, não treina, não abre
Gold ou reserva temporal, e não mede latência/throughput de produção. A suíte
normal executa controles com modelos gerados e ignora o caso nativo por padrão.

## Bronze completa — preparo offline antes da transferência

**Preparo, publicação, reenvio e restauração completos aprovados pelo autor.**
O piloto de artefatos permanece `fraud_storage_bundle_v1`, até 64 arquivos/64 MiB.
A Bronze tem perfil separado `fraud_bronze_bundle_v1`, orçamento de **512 arquivos /
256 MiB**, manifesto externo até 256 KiB e caminhos restritos ao snapshot do commit e ao inventário de origem.
O orçamento cobre o conjunto observado (~102 MiB em `data/raw/`), não representa
uma limitação de hardware; excedê-lo exige revisão explícita do perfil. Não há
lotes manuais para fragmentar a cobertura de um snapshot.

Na raiz do projeto:

```bash
poetry run python -m scripts.storage.bronze_archive prepare
```

O comando obtém o commit de `references/handbook_source.json` e exige Bronze
completa no verificador existente. Inclui todos os arquivos regulares do snapshot
`data/raw/handbook/<commit>/`, inclusive manifesto e auditorias, mais **os bytes
originais do inventário** no caminho `references/handbook_source.json` dentro do
bundle. Markers fora do snapshot não pertencem ao conjunto. Não reescreve os
manifestos históricos nem desserializa pickles.

O manifesto externo lista tamanhos/SHA-256 e fixa commit, SHA do inventário e SHA
do manifesto Bronze. O inventário acompanha a cópia para permitir verificação sem
acesso ao `references/` original. A exceção de path de referência é exclusiva
desse schema; o piloto conserva a restrição de paths sob `data/`.

Escritores da Bronze devem estar parados. Lock de extração, arquivo mutante,
symlink, banco ativo, partição ausente/corrompida, cobertura incompleta ou alteração
no conjunto de auditorias interrompem o preparo. O verificador Bronze existente
cria/remove seu lock transitório durante as conferências. Isso não equivale a um
snapshot atômico de filesystem ou backup de banco ativo.

A cópia usa blocos de 1 MiB e diretório temporário. Antes de publicar o bundle
local, valida todos os arquivos e repete a verificação Bronze com **o inventário
arquivado**: tamanho, Git blob SHA-1, SHA-256, identidade e cobertura completa.
Auditorias são preservadas e conferidas por bytes; não há validação semântica nova
dos seus campos históricos. Fonte e manifestos são preservados. Repetir os mesmos
bytes reutiliza a identidade; um destino existente é novamente conferido.

Sucesso retorna `prepared_verified_locally`, `bundle_id`, arquivos/bytes, número
de partições e `complete=true`; um recibo fica em `data/storage/receipts/`.
No conjunto original, a expectativa de cobertura é **183 partições**. Arquivos
auxiliares e inventário acrescentam itens ao payload; o manifesto externo é outro
arquivo. O preparo mede os números efetivos, sem inferi-los do inventário global.

Para verificar novamente apenas o bundle local, quando necessário:

```bash
poetry run python -m scripts.storage.bronze_archive verify --bundle-id <bundle_id>
```

O autor aprovou o preparo nativo em 2026-10-15: **187 arquivos de origem**, mais
inventário, total **188 arquivos/107.309.243 bytes**, **183 partições**, `complete=true`.
Bundle `bca0bdcbd60853db575dcdd5af986fd7608057c69fb368fd5fa0007aa62bc4c0`.
[Evidência relatada](../../references/evidence/bronze_archive_author_validation_2026-10-15.json).
O preparo não acessou S3 nem desserializou pickle; fonte e manifestos preservados.

### Transferência Bronze: um job, cliente reutilizado

`scripts/storage/bronze_transfer.py` publica/restaura o schema Bronze em
`fraud-datasets/bronze/v1/<bundle_id>/`. O piloto continua em `fraud-artifacts`
e recusa Bronze. A separação evita alterar seus limites e dependências.
O cliente anterior inicia um contêiner AWS CLI por operação: adequado ao piloto,
mas custoso para centenas de objetos. A Bronze usa **um job Compose por comando**,
com um cliente boto3/conexões reutilizados e streaming sequencial de 1 MiB.
Não há novo serviço permanente nem pool paralelo.

O SDK possui lock operacional com versões/hashes, instalado só na imagem
`Dockerfile.storage` e no tox `bronze-sdk`; o lock científico é preservado.
O build usa a base Python já fixada por digest e allowlist própria, sem datasets,
`.env` ou pacote científico no contexto. O job só monta `data/storage/`, usa
UID/GID local, filesystem raiz somente leitura e credenciais via ambiente do
Compose. Endpoint interno `http://rustfs:9000`, path-style, SigV4, timeout de
conexão 5 s/leitura 30 s e até três tentativas por requisição. O hash dos scripts
é conferido antes de acessar o SDK, impedindo executar imagem local desatualizada.

**Publicação:** conferir o bundle local com o verificador Bronze; criar o bucket
se realmente ausente; PUT com `If-None-Match: *` e checksum SHA-256; ler de volta
cada objeto e comparar tamanho/hash. Conflito, acesso negado ou falha de rede
interrompem, sem sobrescrever/remover objetos. Objetos válidos de uma tentativa
parcial são reutilizados na próxima chamada. O manifesto externo é o marcador
final: só é enviado depois dos payloads conferidos. A listagem paginada exige o
conjunto exato; ETag não é tratado como SHA-256. O protocolo é do cliente, sem
Object Lock/versionamento nem proteção contra exclusão por administrador.

**Restauração:** exige manifesto remoto final, tamanho máximo de 256 KiB e
identidade do bundle. Baixa para `data/storage/downloads/bronze-<temporário>`;
confere tamanhos/SHA-256 por streaming e cobertura dos objetos. Depois o host
aplica o **verificador Bronze existente com o inventário baixado**, sem ler o
snapshot/inventário/bundle originais e sem pickle. Só então renomeia a cópia para
`data/storage/restored/<bundle_id>`. Destino existente não é sobrescrito. Falhas
de transporte removem o temporário; falha semântica preserva quarentena para
análise. Mantenha os bundles/restores sem escritores concorrentes.

Para reproduzir em um ambiente com o bundle preparado e RustFS pronto,
construir apenas o job e publicar. O autor já concluiu estes comandos:

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml --profile storage-tools build storage-transfer

poetry run python -m scripts.storage.bronze_transfer publish \
  --bundle-id bca0bdcbd60853db575dcdd5af986fd7608057c69fb368fd5fa0007aa62bc4c0
```

Esperado: `success`, `uploaded_objects=189`, `reused_objects=0`,
`verified_objects=189` (188 payloads + manifesto). Se uma tentativa anterior já
enviou parte dos arquivos, os contadores de reuso refletem isso. O job imprime
progresso no stderr; o JSON final e recibo incluem versões SDK, hash dos scripts
e duração. O SDK fornece tentativas limitadas por requisição, sem teto global
arbitrário para o conjunto. Não imprime credenciais nem requer expandir `config`.

Para conferir idempotência, repetir publicação. A restauração abaixo exige que
`data/storage/restored/<bundle_id>` ainda não exista; o autor já a executou com
sucesso. Aplicar o fechamento documental não exige repetir esse restore:

```bash
poetry run python -m scripts.storage.bronze_transfer publish \
  --bundle-id bca0bdcbd60853db575dcdd5af986fd7608057c69fb368fd5fa0007aa62bc4c0

poetry run python -m scripts.storage.bronze_transfer restore \
  --bundle-id bca0bdcbd60853db575dcdd5af986fd7608057c69fb368fd5fa0007aa62bc4c0
```

Reenvio esperado: **0 uploads/189 reutilizados/189 conferidos**. Restore esperado:
`restored_verified`, `complete=true`, **188 arquivos/107.309.243 bytes e
183 partições**. Recibos em `data/storage/receipts/bronze-publish-*.json` e
`bronze-restore-*.json`. Não enviar segredos do `.env` junto dos resultados.

### Resultado nativo informado pelo autor — 2026-10-15

**Build, publicação, reenvio e restauração aprovados no RustFS.** O bundle é o
mesmo do preparo: `bca0bdcbd60853db575dcdd5af986fd7608057c69fb368fd5fa0007aa62bc4c0`,
no bucket `fraud-datasets`, prefixo `bronze/v1/<bundle_id>/`.

| Operação | Resultado observado | Tempo reportado pelo job |
| --- | --- | --- |
| Publicação inicial | 189 uploads; zero reutilizados; 189 objetos lidos/conferidos | 3,558664244999818 s |
| Reenvio | Zero uploads; 189 reutilizados; 189 conferidos | 1,025176987001032 s |
| Download da restauração | 188 arquivos/107.309.243 bytes; 189 objetos conferidos | 0,8571889770028065 s |
| Verificação final no host | `restored_verified`, `complete=true`; 183 partições; inventário baixado | Não medido separadamente |

Os 188 arquivos são o payload (187 arquivos do snapshot mais o inventário);
o manifesto de arquivamento acrescenta o 189º objeto/arquivo externo.
O worker usou boto3/botocore `1.43.114`, hash de código
`6dea17162c2104b2c05766e4ffd3d5bcf851bec9f5c7557cdc284621f10564fa`.
O build terminou em 5,7 s conforme a saída Docker, sem protocolo de benchmark.
Os tempos `elapsed_seconds` medem o bloco do worker: não incluem toda a chamada
Poetry/Compose, inicialização do contêiner ou a conferência semântica Bronze
posterior no host. Não foram medidos CPU/RAM, throughput sustentado ou SLA.

Recibos informados, preservados em `data/storage/receipts/`:

- `bronze-publish-qoxp1ujx.json`: primeira publicação.
- `bronze-publish-ix29d9x4.json`: reenvio idempotente.
- `bronze-restore-qbmjovyf.json`: restauração completa verificada.

[Evidência do autor](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
O assistente recebeu comandos/JSONs impressos e conferiu a correspondência do
hash do worker com os scripts entregues; não inspecionou o bucket nem os bytes
dos recibos/arquivos nativos. A execução no host é distinta das fixtures e do
Stubber da preparação, cujos registros históricos foram preservados.

O gate **arquivamento e recuperação da Bronze completa** está fechado.
Os comandos não desserializaram pickle nem leram a fonte original no restore.
O arquivo local continua sendo a fonte dos consumidores (`primary_storage_migrated=false`).
A ampliação Silver implementada posteriormente está descrita na seção seguinte,
com contrato, schema, contagens e proveniência no restore antes de mudar leitores.
Gold de desenvolvimento/estudos, bancos, migração MLflow e tracking permanecem em etapas próprias. Os volumes atuais e futuros
PVCs não constituem uma cópia fora do laptop; Helm continua no roadmap após
recuperação/rollback e com política de persistência explícita.

## Silver completa: preparo e recuperação com contrato

**Preparo, publicação, reenvio e restauração completos aprovados pelo autor.** O fechamento nativo Bronze acima é preservado.
A Silver possui dados em `silver_v1/` e auditorias em `runs/`, fora desse diretório.
Arquivar apenas os Parquets perderia metadados operacionais e o contrato necessário
à conferência independente da cópia. O incremento cobre esse conjunto completo
sem incluir EDA, reconstruir dados ou criar outro cliente S3.

### Conteúdo e verificação

O CLI `scripts/storage/silver_archive.py` seleciona:

- Todo `data/interim/handbook/<commit>/silver_v1/`, incluindo manifesto.
- Todas as auditorias `data/interim/handbook/<commit>/runs/silver_<run_id>.json`;
  a auditoria indicada por `run_id` no manifesto é obrigatória.
- Os bytes de `references/handbook_source.json` e `references/silver_contract_v1.json`.

Schema de arquivamento `fraud_silver_bundle_v1`: caminhos limitados a esse snapshot,
auditorias próprias e duas referências exatas; proveniência fixa commit e hashes
do inventário, contrato e manifesto Silver. Orçamento explícito de **512 arquivos,
256 MiB de payload e manifesto externo até 256 KiB**, usando o teto conservador
já aprovado para datasets. O inventário global da família Silver inclui outros
outputs, por isso arquivos/bytes efetivos são medidos pelo preparo e não inferidos
dos 198 arquivos anteriormente inventariados em toda `data/interim/`.

Com escritores parados, o preparo recusa `.silver_v1.lock`, symlinks, bancos,
arquivos faltantes/corrompidos e auditorias/conjunto mutantes durante a cópia.
Usa o staging limitado já existente e só publica o bundle local depois de
`verify_silver` aprovado com as referências arquivadas. O verificador existente
confere tamanhos/SHA-256, schema Arrow, contagens por partição, cobertura e
reconciliação SQL (linhas, IDs distintos, fraudes, genuínos e valores zero).
Auditorias e manifestos conservam seus bytes. Nenhuma escrita altera os originais.
O comando não lê Bronze, pickle, modelos ou scores; preserva os hashes históricos
de entrada sem recomputar a Bronze nem repetir a etapa de aceitação original.

### Primeiro gate no host: preparo

Para reproduzir o preparo com Silver existente e escritores parados, após os
incrementos anteriores. O autor já concluiu esse comando:

```bash
poetry run python -m scripts.storage.silver_archive prepare
```

Esperado na Silver histórica: `prepared_verified_locally`, `complete=true`,
**183 partições**, **1.754.155 linhas e IDs distintos**, **14.681 fraudes**,
**1.739.474 genuínas** e **42 valores zero**. Os números são a referência do
build/verify de 06/10, agora também reproduzidos no preparo/restore relatados.
O comando mede
`archive_files`, `included_audits`, bytes e `bundle_id`, e preserva recibo em
`data/storage/receipts/silver-prepare-*.json`. O preparo nativo mediu 187 arquivos,
40.288.931 bytes e uma auditoria, com contrato e inventário incluídos.

### Transporte reutilizado e recuperação

As operações de payload, PUT condicional, checksum/readback, listagem e manifesto
por último são as mesmas da Bronze. O worker existente aceita seleção explícita
de perfil e usa `fraud-datasets/silver/v1/<bundle_id>/`; o default Bronze conserva
seu schema, prefixo e IDs. O SDK, imagem base e serviços não ganham dependências.
`silver_archive` controla os gates Silver no host; não há um cliente S3 paralelo.

**Rebuild após mudança nos scripts operacionais**, inclusive para Bronze:
o handshake de hash recusa imagem antiga. O autor já reconstruiu o job para essa
versão antes do round-trip Silver; o fechamento documental atual não exige rebuild. Construir apenas o job não reinicia RustFS,
Prometheus, Grafana ou exporter. As identidades dos bundles existentes não mudam;
o novo hash do worker será registrado nas próximas execuções.

Para reproduzir em ambiente novo, substituir `<bundle_id>` pelo valor retornado.
O restore exige destino ainda ausente; o autor já o concluiu e não precisa
repeti-lo para aplicar este fechamento documental:

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml --profile storage-tools build storage-transfer

poetry run python -m scripts.storage.silver_archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.silver_archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.silver_archive restore --bundle-id <bundle_id>
```

Primeira publicação: `archive_files + 1` objetos enviados/conferidos; o manifesto
externo é o adicional. Reenvio completo: zero uploads, todos reutilizados e
conferidos. Uma tentativa parcial reutiliza apenas os objetos válidos existentes.
Restore: download limitado para `data/storage/downloads/silver-<temporário>`,
verificação com inventário/contrato **baixados**, schema e contagens SQL; só então
recebe `data/storage/restored/<bundle_id>`. Retorna `restored_verified`, cobertura,
contagens, arquivos/bytes e recibo `silver-restore-*.json`. Não lê o conjunto ou
metadados originais, e não sobrescreve destino existente. Falha de transporte
remove temporário; falha semântica preserva quarentena para diagnóstico.

### Silver: resultado nativo informado pelo autor — 2026-10-15

Bundle `f9ec010e67cdd4bd7b8fe992b7503ed4c8a3c0147a63caadf882b8f7db2cf224`,
bucket `fraud-datasets`, prefixo `silver/v1/<bundle_id>/`.

| Operação | Resultado observado | Tempo reportado pelo job |
| --- | --- | --- |
| Preparo offline | 187 arquivos/40.288.931 bytes; contrato/inventário e uma auditoria | Não reportado |
| Publicação inicial | 188 uploads; zero reutilizados; 188 objetos lidos/conferidos | 3,329493249002553 s |
| Reenvio | Zero uploads; 188 reutilizados/conferidos | 0,986083370000415 s |
| Download | 187 arquivos/40.288.931 bytes; 188 objetos conferidos | 0,8359925039985683 s |
| Verificação final no host | `restored_verified`, `complete=true`; schema e SQL reconciliados | Não medido separadamente |

Payload de 187 arquivos: 183 Parquets, manifesto Silver, uma auditoria,
inventário e contrato. O manifesto externo acrescenta o 188º objeto/arquivo.
Conferência final: **183 partições**, **1.754.155 linhas e IDs distintos**,
**14.681 fraudes**, **1.739.474 genuínas**, **42 valores zero**. A restauração usou
contrato/inventário baixados, sem ler fonte original, Bronze ou pickle;
`primary_storage_migrated=false`.

O autor concluiu o build do job (1,3 s reportado, com cache). SDK boto3/botocore
`1.43.114`, hash do worker
`ea8d8bc890511c5fd9cae7ed543c797b93c35282a710242176a47ddc36c55023`,
conferido contra os scripts entregues. Os tempos `elapsed_seconds` são do worker;
excluem a verificação Silver final no host e parte da chamada Poetry/Compose.
Não houve medição de CPU/RAM, throughput sustentado ou SLA.

Recibos informados em `data/storage/receipts/`:

- `silver-prepare-ef9zeoas.json`.
- `silver-publish-6muofg7r.json`.
- `silver-publish-yustitol.json`.
- `silver-restore-oxm0jbt3.json`.

[Evidência nativa relatada](../../references/evidence/silver_archive_author_validation_2026-10-15.json).
O assistente recebeu os comandos/JSONs impressos, sem inspecionar o bucket,
recibos ou bytes nativos. A preparação com fixtures e SDK continua preservada.
O gate de arquivamento/recuperação de `silver_v1` e seus metadados está fechado;
EDA não pertence a esse bundle. Consumidores seguem nos paths locais. O inventário
Gold e o gate nativo de `gold_v1` estão registrados na seção seguinte.
Desenvolvimento/estudos, adoção primária, MLflow/backup e Helm/PVCs permanecem em
seus gates.
[Evidência de preparação](../../references/evidence/silver_archive_preparation_2026-10-15.json).

## Gold v1 completa: arquivo com contratos e protocolo

**Arquivamento e recuperação de `gold_v1` aprovados pelo autor em 2026-10-15.**
[Evidência nativa](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
O gate inclui 48 arquivos/25.196.448 bytes e 49 objetos, com referências e auditoria;
os demais grupos do inventário continuam fora desse perfil.
O [inventário do autor](../../references/evidence/gold_storage_inventory_author_2026-10-15.json)
confirmou 506 arquivos/326.821.198 bytes, leitura de metadados completa/zero issues.
Isso não constitui verificação de conteúdo. A família contém:

| Grupo | Arquivos | Bytes | Tratamento |
| --- | ---: | ---: | --- |
| `gold_v1` | 43 | 25.151.651 | Primeiro dataset selecionado; auditoria externa e referências acompanham |
| `development_gold_v2` | 383 | 271.102.856 | Dataset e estudo/outputs; definir cobertura e backup consistente do SQLite previsto no código |
| `baseline_v1` | 11 | 5.262.099 | Resultados/modelos, com gate próprio |
| `diagnostics_v1` | 8 | 618.051 | Diagnósticos/relatórios |
| `experiment_v1` | 20 | 10.385.129 | Ablações/modelos e estado |
| `final_evaluation_v1` | 9 | 1.917.497 | Avaliação preservada e vínculos congelados |
| `reference_assessment_execution_v1` | 24 | 12.337.597 | Execução, acesso e resultados preservados |
| `reference_dependence_diagnostics_v1` | 1 | 25.359 | Relatório |
| `reference_uncertainty_analysis_v1` | 1 | 10.288 | Relatório |
| `runs` | 5 | 10.671 | Associar auditorias aos conjuntos; quatro paths JSON informados |
| Fora do agrupamento reconhecido | 1 | 0 | Caminho não informado pelo resumo; manter revisão pendente |

O grupo de desenvolvimento excede o teto de 256 MiB e reúne papéis distintos.
O código de otimização usa `study/study.db`; a presença/estado desse banco nativo
não foi confirmada pelo resumo de paths JSON. Não copiar um possível banco ativo
como arquivo avulso ou eliminar estudos para caber no limite. Esses grupos
permanecem no plano, com vínculos e estratégias de recuperação próprios.

### Perfil e referências do Gold original

`python -m scripts.storage.gold_archive` oferece `prepare`, `verify`, `publish`
e `restore`. Seleciona todo `data/processed/handbook/<commit>/gold_v1/`, todas
as auditorias `runs/gold_<run_id>.json` do commit (a do criador é obrigatória) e:

- `references/handbook_source.json`.
- `references/silver_contract_v1.json`.
- `references/gold_contract_v1.json`.
- `references/temporal_protocol_v1.json`.

Schema `fraud_gold_bundle_v1`, até **512 arquivos/256 MiB**, manifesto externo
até **256 KiB**. O teto conservador de datasets é preservado; só os paths Gold,
auditorias próprias e quatro referências exatas são aceitos. Proveniência fixa
commit e hashes das referências e do manifesto Gold. Source manifests/auditorias
mantêm bytes originais; referências extras são exclusivas desse perfil, sem
abrir o piloto/Bronze/Silver para arquivos arbitrários de `references/`.

Exige escritores parados e recusa `.gold_v1.lock`, symlinks, bancos, arquivos
faltantes/corrompidos, auditoria ausente e mutações do conjunto durante staging.
O staging é publicado localmente só após repetir `verify_gold` com referências
arquivadas. Conferências: tamanho/SHA-256, schema Arrow, allowlist/invariantes
das 19 features, datas/partições previstas, IDs únicos e contagens por split.
Não lê Silver/Bronze, recalcula features ou executa modelos. Preserva os hashes
históricos da Silver e contextos, sem recomputá-los contra a fonte; isso não é
nova avaliação de qualidade do modelo/holdout ou paridade batch/streaming.
Auditorias são preservadas por integridade sem reinterpretar seus campos.

### Preparo aprovado e procedimento de reprodução

```bash
poetry run python -m scripts.storage.gold_archive prepare
```

Resultado nativo informado: `prepared_verified_locally`, `complete=true`,
**42 partições**, **19 features**, **402.877 linhas**:

| Split | Linhas verificadas |
| --- | ---: |
| Treino | 268.668 |
| Validação | 67.255 |
| Teste | 66.954 |

O preparo mediu **48 arquivos/25.196.448 bytes**: 42 Parquets, manifesto Gold,
uma auditoria e quatro referências. O manifesto externo acrescenta o 49º objeto.
Recibo: `data/storage/receipts/gold-prepare-6r5krrua.json`.
Bundle: `af32af6befe8ad2dba7f36965011ead09d9cdf7c0ac9be81bffcdea92e2e45aa`.
Os 43 arquivos/25.151.651 bytes do inventário de `gold_v1` não incluíam a auditoria
externa e as referências. Nenhum original foi removido.

### Transporte compartilhado e restore

O worker já utilizado aceita o perfil Gold em
`fraud-datasets/gold/v1/<bundle_id>/`. Usa um job/cliente por comando e os mesmos
controles de PUT condicional, SHA-256/readback, listagem e manifesto por último.
O SDK, lock operacional e serviços não mudam. O CLI Gold mantém o gate semântico
no host. Bronze/Silver conservam schema, prefixo e identidade dos bundles válidos.

O autor reconstruiu apenas o job e aprovou o handshake de código. Para reproduzir
em outro ambiente, construir a imagem atual; após alterações dos scripts do
worker, reconstruir antes de operações S3. Não é necessário reconstruir para
aplicar este fechamento documental. Esse build não reinicia os serviços existentes.
Substituir `<bundle_id>` pela identidade medida; comandos abaixo são referência,
não precisam ser repetidos sobre o destino já restaurado:

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml --profile storage-tools build storage-transfer

poetry run python -m scripts.storage.gold_archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.gold_archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.gold_archive restore --bundle-id <bundle_id>
```

Primeira publicação completa: `archive_files + 1` objetos; reenvio completo:
zero uploads, todos reutilizados/conferidos. O manifesto externo é adicional.
Restore em `data/storage/downloads/gold-<temporário>`, com validação de bytes e
`verify_gold` sobre a cópia e as referências baixadas. Só então recebe
`data/storage/restored/<bundle_id>` e retorna `restored_verified` com partições,
19 features e contagens por split iguais às do preparo. Destino existente é
protegido. Falha de transporte remove temporário; falha semântica preserva
quarentena. O restore não consulta dataset, contratos ou protocolo originais.

### Resultado nativo informado pelo autor — 2026-10-15

| Operação | Resultado | Recibo em `data/storage/receipts/` |
| --- | --- | --- |
| Preparo | 48 arquivos/25.196.448 bytes, uma auditoria e quatro referências | `gold-prepare-6r5krrua.json` |
| Publicação | 49 uploads, zero reutilizados, 49 objetos verificados | `gold-publish-cm0bl7pk.json` |
| Republicação | Zero uploads, 49 reutilizados e verificados | `gold-publish-ul3sv3wv.json` |
| Restore | `restored_verified`, 48 arquivos e 49 objetos verificados | `gold-restore-s3yo571_.json` |

O restore reconciliou os mesmos 42 Parquets, 19 features e 402.877 linhas/splits,
com inventário, contratos e protocolo baixados. `original_source_read=false`,
`bronze_read=false`, `silver_read=false`, `features_recomputed=false` e
`pickle_deserialized=false`. O verificador existente foi reaplicado à cópia;
a fonte original foi preservada. Isso não é recomputação das features nem
confirmação nova do modelo ou dos hashes da Silver contra a camada original.

O build relatado levou 1,7 s com cache; índice local da imagem:
`sha256:985040e83ed03424d7cf8a989ebd95da04177e4da786e2431a5820fd5fdf7caa`.
Hash do worker conferido contra os scripts entregues:
`d1531495855f538812b383966374fdab8401357d13bbaba5945cd1a21363552c`.
SDK boto3/botocore 1.43.114. Durações do worker: publicação 0,9033 s, reenvio
0,5403 s, download 0,4174 s. Excluem conferência semântica no host, startup do
container e parte da chamada Poetry/Compose; não são tempo total, benchmark ou SLA.

O assistente recebeu comandos/JSONs impressos; não acessou bucket, recibos ou
bytes nativos. Os paths dos recibos são referências, sem reconstrução de seus bytes.
[Evidência nativa Gold](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
A [evidência de preparação](../../references/evidence/gold_archive_preparation_2026-10-15.json)
permanece com seu estado histórico anterior à execução nativa.

O gate de arquivamento/recuperação de `gold_v1` e seus metadados está fechado.
Os leitores continuam locais. O gate do dataset de desenvolvimento está registrado
na seção seguinte; o estudo Optuna também teve backup/recuperação consistente
aprovados, incluindo SQLite e seus artefatos vinculados, no escopo abaixo. Modelos/avaliações, migração
MLflow, adoção primária e recuperação de PVCs continuam com gates próprios.

## Gold de desenvolvimento: dataset e estudo

**Arquivamento/recuperação do dataset aprovados pelo autor em 2026-10-15.
Backup e recuperação S3 do estudo aprovados pelo autor em 2026-10-16.**
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
O payload final inclui duas referências: 66 arquivos/38.202.741 bytes e 67 objetos.
O [inventário do autor](../../references/evidence/development_storage_inventory_author_2026-10-15.json)
delimitou o run `9f86edf1e8f2408098c5377456e8968e3a7ca5925a20222b8ab8547ef94cbaf9`:

| Componente | Arquivos | Bytes | Tratamento |
| --- | ---: | ---: | --- |
| Manifesto do dataset | 1 | 60.777 | Preservar bytes e identidade |
| Parquets em `transactions/` | 63 | 38.098.557 | Arquivar com manifesto e política; verificador existente |
| Metadados na raiz de `study/` | 4 | 572.772 | Incluem SQLite de 540.672 bytes e lock; backup consistente próprio |
| Recibos em `study/attempts/` | 63 | 285.660 | Coordenar com banco e artefatos do estudo |
| Modelos/predições em `study/models/` | 252 | 232.085.090 | Preservar identidades, hashes e vínculos dos recibos |
| Total | 383 | 271.102.856 | Dois conjuntos com responsabilidades distintas |

Dataset: **64 arquivos/38.159.334 bytes**. Estudo: **319 arquivos/232.943.522 bytes**,
incluindo lock. Ambos cabem individualmente em 256 MiB; separá-los decorre de
imutabilidade versus estado transacional e dos verificadores necessários.
Não aumentar o teto nem dividir arbitrariamente uma cópia para contorná-lo.
A existência de `.writer.lock` não prova que exista um escritor ativo. O inventário
não abriu banco/modelos nem verificou integridade de conteúdo.

### Perfil do dataset e recuperação

`scripts/storage/development_gold_archive.py` oferece `prepare`, `verify`,
`publish` e `restore`. O preparo exige `--run-id` explícito; seleciona apenas
`development_gold_v2/<run_id>/manifest.json` e `transactions/`, mais:

- `references/handbook_source.json`.
- `references/hgb_optuna_protocol_v1.json`.

Schema `fraud_development_gold_bundle_v1`, 512 arquivos/256 MiB e manifesto
externo até 256 KiB. Proveniência fixa source commit, development run ID,
hashes de inventário/política/manifesto. Somente esses paths e Parquets diários
são aceitos; estudo, bancos, outras versões e referências arbitrárias ficam fora.
O perfil não abre nem percorre `study/` e não testa seu lock. Recusa entradas
extras desconhecidas do dataset, symlinks, corrupção, arquivos faltantes,
run/diretório incoerentes ou mutação do conjunto durante staging.

Staging e cópia baixada passam por `verify_data` congelado com a política
arquivada: identidade derivada, autorização temporal, 19 features em ordem,
schema Arrow, invariantes de features, datas, hashes, contagens e IDs únicos.
Não consulta Silver/Bronze, recalcula features, carrega modelo, acessa MLflow ou
executa Optuna. Manifesto e proveniência histórica da implementação/Silver são
preservados por bytes, sem revalidar hashes upstream contra uma nova Silver.
`confirmation_evaluated=false` permanece; isso não produz nova avaliação de
holdout ou comparação de modelos.

Preparo offline aprovado; comando de referência para reprodução:

```bash
poetry run python -m scripts.storage.development_gold_archive prepare \
  --run-id 9f86edf1e8f2408098c5377456e8968e3a7ca5925a20222b8ab8547ef94cbaf9
```

O preparo nativo aprovou **63 partições/19 features/603.669 linhas**,
com `status=prepared_verified_locally`, `complete=true`, `study_included=false`
e `study_read=false`. Mediu **66 arquivos/38.202.741 bytes** (64 de dataset e
duas referências; elas acrescentam 43.407 bytes ao inventário do dataset).
Bundle: `4e20595ffe482aacfb279d8dd8dea245e0ccc0f1d602c62f17d0cc52d12d6802`.
Run: `9f86edf1e8f2408098c5377456e8968e3a7ca5925a20222b8ab8547ef94cbaf9`.
Manifesto de desenvolvimento preservado:
`b34988a71990356dd38ae62258d8f7ff232f9ecd8611fb9b9a31be80169d91e5`.
Recibo: `data/storage/receipts/development-gold-prepare-8o6sg3v7.json`.

O mesmo job/SDK atende `fraud-datasets/development-gold/v1/<bundle_id>/`.
O autor reconstruiu apenas `storage-transfer` e aprovou o handshake. Em uma
reprodução limpa, construir a imagem atual; futuras alterações dos scripts do
worker exigem rebuild, inclusive para perfis anteriores. Este fechamento
documental não exige reconstrução nem reinício dos serviços. SDK/lock operacional,
dependências científicas, contratos e núcleo de modelagem permanecem iguais.

Para reproduzir em outro ambiente, substitua `<bundle_id>` pela identidade medida.
Os comandos abaixo são referência; o destino já restaurado é protegido e não
precisa de novo restore para aplicar este fechamento:

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml --profile storage-tools build storage-transfer

poetry run python -m scripts.storage.development_gold_archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.development_gold_archive publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.development_gold_archive restore --bundle-id <bundle_id>
```

Publicação verifica readback completo e publica manifesto por último; reenvio
completo deve ter zero uploads. O manifesto externo acrescenta um objeto ao
payload (67 objetos conferidos no ciclo nativo). Download vai para quarentena `development-gold-*`; somente
após verificador semântico recebe `data/storage/restored/<bundle_id>`. Falha
semântica preserva quarentena, destino final existente é protegido. Restore usa
somente cópia e referências baixadas, sem fonte original ou estudo. Durações do
worker não incluem toda a chamada Compose/startup nem verificação semântica no host.

### Resultado nativo informado pelo autor — 2026-10-15

| Operação | Resultado | Recibo em `data/storage/receipts/` |
| --- | --- | --- |
| Preparo | 66 arquivos/38.202.741 bytes, inventário/política incluídos | `development-gold-prepare-8o6sg3v7.json` |
| Publicação | 67 uploads, zero reutilizados, 67 objetos verificados | `development-gold-publish-0w_sbr_y.json` |
| Republicação | Zero uploads, 67 reutilizados/verificados | `development-gold-publish-qhkbfdl2.json` |
| Restore | `restored_verified`, 66 arquivos/67 objetos verificados | `development-gold-restore-762fn8gt.json` |

O restore reconciliou 63 partições, 19 features e 603.669 linhas. Run e hash do
manifesto coincidem com o preparo/publicações; política/inventário foram baixados.
`original_source_read=false`, `bronze_read=false`, `silver_read=false`,
`study_read=false`, `study_included=false`, `study_backup_validated=false`,
`features_recomputed=false` e `pickle_deserialized=false`.
`confirmation_evaluated=false` foi preservado. Os originais continuam locais.

Build relatado: 1,5 s com cache; índice local da imagem:
`sha256:4dbf9c21bbf984bef1e86d4cff2c331ee3b0b0c641d7ad387696532dba73ebfa`.
Hash do worker conferido contra os scripts entregues:
`3d259cd0a3795c7454ff6c795a81c9b08bcb188c8fbb719e3103e67ea2adc9c5`.
SDK boto3/botocore 1.43.114. Worker: publicação 1,3583 s, reenvio 0,5207 s e
download 0,4812 s. São durações parciais, sem startup/Compose completo nem
verificação semântica no host; não constituem benchmark ou SLA.

O assistente recebeu comandos/JSONs impressos, sem acessar bucket, recibos ou
bytes nativos. Os paths dos recibos são referências, sem reconstruir seus bytes.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
A [evidência da preparação](../../references/evidence/development_gold_archive_preparation_2026-10-15.json)
conserva seu estado histórico anterior aos comandos nativos.
Gate do dataset fechado; o estudo teve seu ciclo próprio aprovado, conforme abaixo.
Tracking, adoção dos leitores e Helm/PVCs permanecem em seus critérios próprios.

### Estudo consistente e vínculos

O inventário confirmou `study/study.db`, mas não seu estado lógico ou recuperação.
O preparo offline agora coordena snapshot do banco com modelos, predições,
tentativas e relatórios, respeitando bloqueio de escrita e usando o verificador
existente. O gate nativo e a transferência/recuperação S3 estão na seção seguinte.
Preservar run ID, hash do manifesto e identidade do bundle do dataset, além das
referências de tracking congeladas. Copiar arquivos não migra URIs nem comprova
retomada de treino ou recuperação do MLflow. Nenhum estudo/modelo será apagado.
[Preparação e testes](../../references/evidence/development_gold_archive_preparation_2026-10-15.json).

## Backup consistente do estudo HGB: preparo offline

**Preparo nativo e publicação/reenvio/restore do estudo aprovados pelo autor.**
[Ciclo S3 de 2026-10-16](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json). O dataset de desenvolvimento
já tem round-trip nativo aprovado; o estudo tem ciclo próprio porque seu SQLite
precisa ser coordenado com arquivos associados. A escolha mantém SQLite para o
estudo histórico, checkpoint/telemetria locais; PostgreSQL segue como destino do
servidor MLflow. Paralelização compartilhada futura do Optuna tem gate de backend.

### Conjunto e dependência

`scripts/storage/hgb_study_backup.py` oferece somente `prepare` e `verify`.
Requer localmente o bundle/restore de desenvolvimento já verificado, identificado
por `--dataset-bundle-id`. Resolve `data/storage/bundles/<id>` ou
`data/storage/restored/<id>` e reaplica o verificador do dataset. O primeiro
candidato existente deve passar; falha não é ocultada por fallback automático.

O schema `fraud_hgb_study_bundle_v1` limita **512 arquivos/256 MiB**, manifesto
externo até **256 KiB**. Inclui:

- Snapshot independente de `study/study.db` produzido pela SQLite Online Backup API.
- `study/protocol.json`, `report.json`, `attempts/` e `models/` completos no layout
  HGB conhecido: modelo skops, predições Parquet, métricas e recibos por fold.
- Manifesto original do dataset, inventário e protocolo HGB arquivados.
- Recibo `study/storage-snapshot.json`, com método, hash lógico SQLite, origem
  histórica e vínculos por run ID, hash do manifesto e ID do bundle do dataset.

WAL/SHM/journal são tratados pela API SQLite; eles e `.writer.lock` não viram
payload. Arquivos desconhecidos, outros bancos ou layouts fora do perfil falham
antes de publicar um bundle. A rotina não desserializa modelos, treina, acessa
MLflow, recalcula features ou produz avaliação nova do holdout.
O banco do snapshot pode ter bytes físicos diferentes da fonte: integridade,
foreign keys e SHA-256 do dump lógico são conferidos, além do checksum físico
do snapshot arquivado. Modelos/recibos/predições/relatório mantêm seus bytes.

O inventário nativo informou 319 arquivos/232.943.522 bytes de estudo, incluindo
lock. Se o conjunto permanecer igual, esperamos 318 arquivos persistentes mais
manifesto/inventário/política e recibo do snapshot: **322 arquivos de payload**.
Bytes e identidade finais serão medidos no preparo, sem presumir equivalência
física do SQLite. Não duplicamos os 63 Parquets no bundle do estudo: o dataset
é dependência explícita e deverá ser recuperado primeiro.

### Consistência e verificação sem reescrever histórico

O preparo abre o `.writer.lock` existente em modo leitura e tenta adquirir o
mesmo bloqueio exclusivo/não bloqueante do otimizador. Se houver escritor ativo,
recusa a operação. Não cria nem remove lock para forçar passagem. O bloqueio é
mantido durante snapshot/cópia/verificação e publicação local do bundle.
A leitura SQLite usa `mode=ro`; verifica integridade e foreign keys, captura o
hash lógico e produz backup por páginas com orçamento e timeout. Uma segunda
leitura confere que o conteúdo lógico da fonte não mudou. Também recusa mutação
do conjunto de arquivos durante o staging. O controle pressupõe que escritores
cooperativos respeitem o lock existente; não coordena outros serviços/hosts.

Este perfil é para **estudo com orçamento encerrado e trials terminados**;
recusa snapshot de estudo em andamento. Nenhum novo trial ou tentativa é criado.
O staging só se torna bundle após verificação. A repetição sobre fonte estável
reutiliza a identidade aprovada, sem sobrescrever conteúdo divergente.

O verificador congelado `verify_hgb_optimization` reconcilia identidade/política,
trials/objetivos, modelos por hash, predições, métricas, populações, recibos e
orçamento. Ele inicializa acesso Optuna/SQLAlchemy e compara `study_path` absoluto
no relatório. Por isso **não roda sobre o banco ou relatório originais/arquivados**:
usa cópia temporária do estudo e dataset. Apenas `study_path` do relatório
nessa cópia recebe o caminho temporário; o relatório original e todas as URIs
continuam preservados por bytes. A verificação não é migração de paths/MLflow,
recarga de modelos ou prova de retomada de treinamento.

A cópia temporária é removida após sucesso/falha; cabe no orçamento de espaço
pré-checado junto com snapshot/staging. Memória e tempo nativos ainda serão
medidos se necessários; não há alegação de SLA ou benchmark. O verificador
`verify` funciona após remover a fonte original, desde que backup e dependência
do dataset estejam disponíveis localmente. Não cria uma nova execução científica.

### Preparo nativo aprovado

Com a otimização parada e o dataset já arquivado disponível:

```bash
poetry run python -m scripts.storage.hgb_study_backup prepare \
  --dataset-bundle-id 4e20595ffe482aacfb279d8dd8dea245e0ccc0f1d602c62f17d0cc52d12d6802
```

Esperamos `prepared_verified_locally`, `complete=true`, integridade/foreign keys,
`sqlite_online_backup=true`, `study_writer_lock_acquired=true` e vínculos do
dataset preservados (63 partições/603.669 linhas). Pela execução histórica,
20 trials/63 tentativas; o comando mede as contagens efetivas, arquivos, bytes,
hash lógico e novo bundle ID. `s3_accessed=false`, `refit=false`, `mlflow_accessed=false` e
`production_promotion=false`. A saída histórica trazia `transfer_implemented=false`
antes desta ampliação; preparos futuros já informam a disponibilidade do transporte.
O autor executou o preparo e aprovou 20 trials concluídos/zero falhas, 63 tentativas,
322 arquivos/233.048.483 bytes, integridade/foreign keys e decisão `retain_reference`.
Bundle: `e18ded1975bf541fe5993698f286c86c64e8abed514e085f64330905d7ec5198`.
Digest lógico SQLite:
`c4e8397e1f1636800b45fe5cf842ce25a481df940ca5afbec62c4e691da5eee7`.
Recibo: `data/storage/receipts/hgb-study-prepare-hnt2l8o0.json`.
[Evidência do autor](../../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
O preparo offline não precisa ser repetido para habilitar a transferência.

Para repetir a conferência local usando o novo ID medido:

```bash
poetry run python -m scripts.storage.hgb_study_backup verify --bundle-id <study_bundle_id>
```

O transporte S3 foi habilitado em incremento próprio abaixo. O schema/manifesto
e a identidade do snapshot nativo foram preservados; não recriar o snapshot para
alterar o destino. Nenhum serviço ou dependência foi adicionado.

Referências de implementação: [SQLite Online Backup API](https://www.sqlite.org/backup.html)
e [Python sqlite3.Connection.backup](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup).

## Transferência S3 do estudo HGB

**Ciclo nativo S3 aprovado pelo autor em 2026-10-16.**
[Evidência nativa](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
`scripts/storage/hgb_study_transfer.py` oferece `publish` e `restore`. O mesmo
job `storage-transfer` e cliente boto3 transportam o snapshot opaco, sem imports
científicos no container. O bucket é **`fraud-artifacts`**, prefixo
**`hgb-study/v1/<bundle_id>/`**. Datasets continuam em `fraud-datasets` com seus
prefixos/IDs; piloto de artefatos mantém `pilot/v1/`. Não há outro cliente ou serviço.
A seleção de bucket decorre do perfil validado, não de uma opção livre do CLI.

No host, `publish` reaplica a verificação do snapshot/dependência antes do S3.
No worker: PUT condicional, SHA-256/readback completo, listagem paginada exata,
manifesto externo por último e reenvio sem sobrescrever conteúdo divergente.
Restaurar exige `--dataset-bundle-id` explícito: o dataset deve estar disponível
como bundle/restore local e passa no verificador **antes de qualquer download**.
O CLI não baixa dependências implicitamente nem consulta fontes/MLflow.

Download vai para `data/storage/downloads/hgb-study-*`. O host confere schema,
ID do bundle, vínculo exato do dataset, digest lógico SQLite, trials/objetivos,
hashes/modelos/predições/recibos/métricas pelo verificador existente. Só após
sucesso recebe `data/storage/restored/<study_bundle_id>`. Falha de transporte
limpa download parcial; falha semântica preserva quarentena. Destino final
existente é protegido. Relatório/URIs/banco arquivados permanecem intactos;
`study_path` é adaptado apenas em relatório descartável da verificação.
Isso valida recuperação de estado/artefatos, sem retomar treinamento ou migrar
runs/URIs do MLflow. Modelos são conferidos por hash, sem recarga/inferência.

### Procedimento de reprodução e disponibilidade

O autor já reconstruiu o job e concluiu as três operações. Este fechamento
documental não exige build, novo snapshot ou repetição do restore aprovado;
o destino final existente é protegido. Para outro ambiente, construir a imagem
atual após mudanças no worker e disponibilizar o dataset vinculado localmente.

O job usa `run --no-deps`: construir a imagem não inicia RustFS. Antes de
transferências, conferir disponibilidade; se necessário, iniciar somente RustFS,
preservando o volume existente:

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml \
  --profile storage up -d --no-deps rustfs

curl --retry 10 --retry-delay 2 --retry-connrefused \
  --connect-timeout 3 --max-time 5 --retry-max-time 40 \
  -fsS -o /dev/null -w 'RustFS HTTP %{http_code}\n' \
  http://127.0.0.1:19000/health/ready
```

Após HTTP 200, os comandos de referência são:

```bash
poetry run python -m scripts.storage.hgb_study_transfer publish \
  --bundle-id e18ded1975bf541fe5993698f286c86c64e8abed514e085f64330905d7ec5198

poetry run python -m scripts.storage.hgb_study_transfer restore \
  --bundle-id e18ded1975bf541fe5993698f286c86c64e8abed514e085f64330905d7ec5198 \
  --dataset-bundle-id 4e20595ffe482aacfb279d8dd8dea245e0ccc0f1d602c62f17d0cc52d12d6802
```

O host já dispõe da dependência recuperada; não repetir seu round-trip.
Repetir `publish` testa reutilização. Repetir `restore` sobre o destino aprovado
será recusado para protegê-lo. Não remover volumes ou originais para reproduzir.

### Resultado nativo informado pelo autor — 2026-10-16

| Operação | Resultado | Recibo em `data/storage/receipts/` |
| --- | --- | --- |
| Publicação | 323 uploads, zero reutilizados, 323 objetos lidos/verificados | `hgb-study-publish-ct9_zih6.json` |
| Republicação | Zero uploads, 323 reutilizados/verificados | `hgb-study-publish-f90w4vod.json` |
| Restauração | `restored_verified`, 322 arquivos/233.048.483 bytes, 323 objetos verificados | `hgb-study-restore-m5t1puoi.json` |

As três operações preservaram **20 trials concluídos, zero falhas, 63 tentativas**,
orçamento finalizado e decisão `retain_reference`. Dependência: 63 partições e
603.669 linhas do dataset de desenvolvimento. Digest lógico SQLite:
`c4e8397e1f1636800b45fe5cf842ce25a481df940ca5afbec62c4e691da5eee7`.
O restore confirmou integridade/foreign keys, relatório e referências de tracking
preservados, com `original_source_read=false`. Somente `study_path` de uma cópia
descartável do relatório foi adaptado para verificação; a cópia arquivada ficou intacta.

Hash do worker conferido contra os scripts entregues:
`ecc7b7fefbe5f3e3e8c96581988877855433824c7705ddf129fb217aa4f6a7bf`.
SDK boto3/botocore 1.43.114. Tempos do worker: publicação 4,9379 s, reenvio
1,6495 s e download 1,6061 s. Excluem verificação semântica no host, startup e
comando Poetry/Compose completo. Build com cache relatado: 1,6 s. Não são benchmark/SLA.

A tentativa inicial retornou `EndpointConnectionError`; o autor depois confirmou
HTTP 200 e publicou o mesmo snapshot com sucesso. A causa exata da indisponibilidade
não foi estabelecida; isso não comprova erro de credenciais nem corrupção.

O assistente recebeu saídas impressas, sem acessar bucket, recibos, banco ou dados
nativos. Paths de recibos são referências; seus bytes/hashes não foram reconstruídos.
[Evidência nativa](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json). A
[preparação com testes](../../references/evidence/hgb_study_transfer_preparation_2026-10-15.json)
permanece com seu estado histórico anterior à execução nativa.

Gate de arquivamento/recuperação do estudo concluído fechado. Modelos foram
conferidos por hash, sem recarga/inferência; `training_resumption_validated=false`.
Tracking não foi acessado/migrado e consumidores continuam locais. Helm/PVCs e
cópia independente da máquina mantêm critérios próprios.

O inventário somente leitura do backend MLflow, artefatos e URIs
foi coletado e revisado pelo autor: zero referências não resolvidas/localizações
gerenciadas ausentes, Default sem runs/modelos ou diretório existente. Seu status
conservador review_required foi preservado. Preparo/verify do backup offline
do tracking tiveram preparo/verify e ciclo S3 nativos aprovados para os dois
stores. Recarga operacional e migração PostgreSQL + S3 continuam em seus gates.
[Procedimento e critérios](MLFLOW.md#inventário-antes-da-migração-postgresql--s3). Não migrar o estudo SQLite concluído só para uniformizar bancos.

## Migração sem reescrever evidências

1. Inventariar todos os conjuntos atuais e medir arquivos, bytes e dependências;
   distinguir fonte, resultado reproduzível, estado ativo e cache descartável.
2. Testar o arquivamento idempotente de uma release e de um ensaio concluído,
   com manifesto e download para um diretório novo, preservando o original.
3. Aplicar o mesmo contrato a Bronze/Silver/Gold e tracking, verificando os
   controles específicos de cada camada e registrando a cobertura migrada.
4. Somente após restauração aprovada, adotar S3 como cópia canônica dos conjuntos
   publicados; o workspace vira materialização/cache gerenciado quando necessário.

Não apagar a fonte local durante essa migração. O armazenamento precisa preservar
auditorias e arquivos associados, não apenas o Parquet ou modelo principal. Os
manifestos históricos têm caminhos absolutos e vínculos congelados em alguns
fluxos; eles não serão editados para aparentar compatibilidade com outro destino.
Uma restauração pode exigir materialização no caminho original ou um adaptador
versionado futuro. A migração do MLflow precisa tratar explicitamente suas URIs.

Para copiar SQLite, parar os escritores ou usar um mecanismo de backup consistente.
No ensaio completo, o escritor já está parado; arquivar juntos os dois bancos,
metadados e avaliação. Um banco ativo não deve ser copiado como arquivo avulso,
montado sobre S3 ou guardado em volume temporário de container.

## Persistência e recuperação

Na bancada Compose, usar volumes nomeados para serviços e paths controlados para
jobs. Em KinD, explicitar PersistentVolumeClaims (PVCs), localização física e procedimento de recriação;
a exclusão do cluster não pode apagar silenciosamente a única cópia valiosa.
O provisionamento Helm incluirá PVCs para RustFS, PostgreSQL, Prometheus e Grafana
quando esses serviços forem migrados, com StorageClass, capacidade, acesso e
política de retenção declarados. Validar recriação de Pod, reinstalação e
restauração após recriar o cluster, identificando onde os bytes ficam no host.
PVC/PV têm ciclo de vida próprio; retenção e backup exigem procedimentos explícitos.
Backup e restauração precisam ser medidos antes de trocar o provisionamento.

RustFS, Docker e KinD no mesmo laptop continuam usando o mesmo hardware. A
organização protege contra perda do diretório de trabalho quando existe uma cópia
válida, mas não resolve perda do disco/máquina. Uma cópia independente em mídia
ou destino disponível é uma etapa separada de recuperação de desastre; não exige
contratar cloud, nem será apresentada como existente antes de ser criada/verificada.

O próximo marco fecha quando houver inventário, upload/download idempotentes e
restauração verificável, com cobertura explicitada por conjunto. A instalação do
RustFS isoladamente não satisfaz esse critério.

## Referências e contratos

- [Kubernetes: PVs, PVCs e políticas de recuperação](https://kubernetes.io/docs/concepts/storage/persistent-volumes/).

- [AWS CLI: imagem oficial e fixação de versão](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-docker.html).

- [Pipeline e contratos das camadas](../data/DATA_PIPELINE.md).
- [MLflow e cuidados de migração](MLFLOW.md).
- [Observabilidade e evidência de operação](OBSERVABILITY.md).
- [Roadmap e critérios da feature store](../project/ROADMAP.md).
- [RustFS: protocolo S3 e diferenças de compatibilidade](https://docs.rustfs.com/en/administration/protocols/s3).
- [DuckDB: acesso a S3](https://duckdb.org/docs/stable/core_extensions/httpfs/s3api.html).
- [MLflow: backend store](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/).
- [Prometheus: storage](https://prometheus.io/docs/prometheus/latest/storage/).


## Critério de fechamento da persistência global

**Requisito do autor: nenhum estado durável sem destino, responsável, retenção e
recuperação definidos.** Inclui corrigir os produtores para que não voltem a criar
arquivos soltos na raiz do checkout, home ou paths Linux arbitrários. Inventário
global e aposentadoria dos caminhos antigos **ainda não estão concluídos**.

| Área identificada no código/configuração | Estado e ação necessária |
| --- | --- |
| data/raw, interim, processed, tracking, replay, serving e exemplos | Defaults locais conhecidos; mapear todos os produtores/overrides CLI, cobertura por conjunto e destinos gerenciados antes de retirar leitores antigos |
| data/storage: bundles, inventários, recibos, restores e staging | Saídas operacionais explícitas; o inventário data_except_storage não as cobre. Inventariar cópias, consumo e política de retenção antes da limpeza |
| Volumes Docker rustfs-data, prometheus-data, grafana-data | Declarados no Compose; inventário de conteúdo/retenção/recuperação próprios, futura relação com PVCs. Não foram inspecionados por este preparo |
| dist/ e diretórios de ferramentas/cache | Build e execução de desenvolvimento; localização e limpeza explícitas, separados de dados de negócio duráveis |
| reports/figures no config.py | Defaults passaram para data/reports e data/reports/figures; sem uso ativo identificado e sem mover diretórios legados |
| DuckDB temporário | No SDK instalado 1.5.6, consulta a duckdb_settings mostrou temp_directory=.tmp. Auditar spill e configurar destino temporário gerenciado, sem concluir que consulta em memória jamais escreve arquivo |
| Diretórios temporários de verificação/testes | Ciclo de vida explícito e descarte gerenciado. O novo backup usa staging sob data/storage; não cria saída persistente na home ou raiz do projeto |

A captura MLflow inventaria sua unidade inteira, incluindo auxiliares regulares,
e só escreve em destinos fixos gerenciados. Ela não declara o restante do projeto
inventariado: o resultado mantém project_wide_storage_inventory_complete=false.
Dados/artefatos duráveis seguirão para armazenamento gerenciado; contratos,
políticas, código e evidências versionadas continuam no Git. Os temporários e
ambientes regeneráveis terão retenção/limpeza explícitas.

Para retirar um caminho antigo: identificar produtor/leitor e conteúdo, validar
substituição e recuperação, alterar configuração/código, provar que o caminho não
é recriado numa execução limpa e só então remover dados/scripts obsoletos. Uma
auditoria estática e a instalação de S3 isoladamente não satisfazem esse critério.
O inventário e o destino de persistência global avançam junto dos gates por unidade,
sem apagar evidência histórica ou empurrar toda a limpeza para uma migração cega.


O preparo nativo detectou o segundo store documentado em
`data/tracking/interface-smoke`: 1 run concluída, 1 logged model, 6 arquivos/32.088
bytes, sem localizações gerenciadas ausentes. A correção inclui os dois stores
com bancos/WAL e artefatos conferidos independentemente e limites agregados,
preservando a separação do smoke sintético. Bancos desconhecidos continuam
bloqueados. Retentativa de preparo e verificação nativos aprovadas para os dois stores;
publicação, reenvio idempotente e recuperação S3 aprovados pelo autor. Migração,
recarga operacional e limpeza permanecem abertas. [Cobertura e comando](MLFLOW.md#cobertura-dos-dois-stores-mlflow--correção-de-2026-10-16).
[Registro do diagnóstico e preparação](../../references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).


O backup MLflow teve **preparo e verificação locais aprovados pelo autor**:
dois stores, 71 runs, 67 logged models, 633 artefatos; pacote de 640 arquivos e
256.354.588 bytes. O verificador conferiu os dois bancos e artefatos sem ler
originais. Tox nativo: 70 testes aprovados, um aviso, Ruff/formatação/lockfile
aprovados. [Registro nativo](../../references/evidence/mlflow_backup_author_validation_2026-10-16.json). A transferência S3 agora está
implementada/testada com o job existente, incluindo restauração em diretório
separado e nova verificação por store; publicação/reenvio/restore nativos aprovados.
A recuperação sobre cópia descartável foi validada pelo autor em 2026-10-16; o resultado local via SDK está no runbook MLflow.
[Procedimento](MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow).
O acesso local ao histórico e à referência pelo SDK foi validado depois desse
preparo. Migração PostgreSQL, adoção pelos consumidores e inventário/limpeza
globais mantêm critérios próprios. Código científico, contratos,
evidências e URIs históricas permanecem preservados.


**Ciclo S3 do snapshot MLflow aprovado pelo autor em 2026-10-16:** publicação com
641 objetos enviados/verificados, reenvio com zero uploads/641 objetos reutilizados
e restore em diretório separado com 640 arquivos/256.354.588 bytes. Os dois stores
mantiveram 71 runs, 67 logged models, 633 artefatos e os hashes SQLite/lógicos;
integridade/FKs/tabelas/URIs conferidas, sem leitura dos originais pelo verificador.
16 testes nativos passaram no tox. [Evidência nativa](../../references/evidence/mlflow_transfer_author_validation_2026-10-16.json).
Gate de acesso ao histórico e recarga do modelo concluído em cópia descartável,
com paths históricos mapeados somente nessa cópia e sem depender dos originais.
O autor validou esse uso local pelo SDK em 2026-10-16.
O snapshot conferido permanece preservado; PostgreSQL, adoção pelos leitores,
rollback, inventário global e limpeza continuam abertos.
[Resultado e próximo critério](MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16).

### Uso do snapshot MLflow recuperado

Integridade/round-trip S3 já validados; acesso ao histórico e recarga do modelo
validados pelo autor em ensaio descartável em 2026-10-16.
A referência principal é o [runbook MLflow](MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).
A cópia em `data/storage/rehearsals/` é temporária; o recibo em
`data/storage/receipts/` preserva o mapa. Não altera bundle/restores ou consumidores.

**Recuperação local MLflow aprovada pelo autor em 2026-10-16:** os dois stores
forneceram 71 runs, 67 logged models e 321 registros de histórico. A referência
recarregada reproduziu o score com erro zero; restores e tabelas foram preservados
e a cópia descartável removida. O gate fecha acesso local pelo SDK e um smoke de
inferência; serviço HTTP/PostgreSQL, migração primária e inventário global mantêm
critérios próprios. [Resultado e evidência](MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

## Inventário do checkout e catálogo de produtores

O próximo recorte fecha uma lacuna do inventário anterior: `archive inventory`
percorre `data/`, exclui `data/storage/` e agrupa conjuntos amplos. Isso foi suficiente
para iniciar o archive, mas não enumera cópias/staging/recibos nem arquivos soltos
na raiz. O novo `scripts.storage.persistence_inventory` percorre o checkout,
incluindo `data/storage/`, e associa cada arquivo a uma localização/família declarada
em `references/persistence_catalogue_v1.json`. O catálogo registra responsáveis,
produtores conhecidos e fronteiras externas. É uma revisão manual de código,
sem análise estática exaustiva e sem provar que os produtores estão confinados.

**Implementado/testado; inventário nativo aprovado no recorte do checkout.** Nenhum destino
foi migrado ou imposto, nenhum dado/script foi movido ou excluído. O coletor é um
módulo separado para preservar os hashes dos produtores/verificadores de backup e
do job S3 que sustentam os bundles já aprovados. Não requer novo serviço ou SDK.

### Executar e interpretar

Na raiz do checkout:

```bash
poetry run python -m scripts.storage.persistence_inventory
```

O comando lê o catálogo versionado e a lista de caminhos do índice Git. Para os
demais arquivos, lê somente metadados: caminho relativo, tamanho lógico, mtime,
classificação, responsável, presença no índice e possível extensão de banco/sidecar.
Não abre datasets, modelos, SQLite/DuckDB, recibos de execução ou credenciais;
não importa/executa os produtores nem contata Docker ou S3. `.env` é reconhecido
pelo nome, sem ler conteúdo. A saída completa fica em
`data/storage/inventory/persistence-*.json`, com permissão `0600`; compartilhar
inicialmente apenas o resumo JSON impresso.

| Campo | Interpretação |
| --- | --- |
| `scan_complete` | O percurso local dentro da fronteira declarada terminou sem falhas de leitura de metadados/links ignorados |
| `files` / `logical_bytes` | Entradas regulares percorridas e soma dos tamanhos lógicos; não medem espaço físico único nem deduplicação |
| `families` | Classificação para revisão; não atesta que todos os arquivos da família estejam cobertos por algum bundle |
| `database_candidates` | Arquivos com extensões de banco/sidecar; nenhum banco foi aberto ou verificado |
| `files_requiring_review` | Arquivos fora das regras e destinos legados, como saídas reais em `reports/` e `models/` |
| `external_boundaries_not_inspected` | Volumes Docker, bind configurável, argumentos de saída e temporários/caches fora do checkout registrados no catálogo |
| `project_wide_storage_inventory_complete=false` | O scan não fecha as fronteiras externas ou a cobertura de todos os produtores/consumidores |

`status=review_required` é intencional: o relatório orienta decisões que ainda
precisam ser tomadas. Não significa falha do comando. `consistent_snapshot=false`
explicita que a coleta de metadados não congela escritores nem produz backup
consistente. Bancos WAL/SHM/journal são listados como arquivos, sem copiá-los.

As árvores de ferramentas declaradas (`.git`, `.venv`, `.tox`, caches de Python etc.)
são registradas como exclusões, com tamanho recursivo desconhecido; não são tratadas
como vazias. Caches homônimos dentro de `data/` continuam percorridos. Não há limite
artificial de arquivos ou bytes do inventário. Links não são seguidos e deixam
`scan_complete=false`; falhas de acesso também são registradas. O relatório é criado
após o scan, portanto entra somente na coleta seguinte.

### O que a revisão de produtores encontrou

Os destinos padrão de datasets, estudos, replay, tracking, serving e arquivos de
recuperação ficam em `data/`. na revisão inicial, `REPORTS_DIR`/`FIGURES_DIR` declaravam `reports/` na raiz;
a revisão não encontrou produtor ativo usando essas constantes. A existência de
um scaffold não autoriza remover arquivos reais encontrados ali. Exemplos e várias
CLIs aceitam caminhos fornecidos pelo chamador; esses overrides ainda não têm
política central de contenção. O catálogo não transforma essa liberdade em garantia.

Há `TemporaryDirectory` de verificação/recarga/exportação no temporário do sistema,
com limpeza por contexto; eles são distintos de artefatos duráveis. Caches e
configurações de dependências fora do checkout não foram inventariados. No ensaio
novo de recuperação MLflow, temporários/downloads já ficam em `data/storage/rehearsals/`.

Os três volumes Compose (`prometheus-data`, `grafana-data`, `rustfs-data`) têm
responsabilidade declarada, mas seus bytes/objetos não são enumerados neste comando.
`STATE_DIRECTORY` pode apontar fora do checkout, e o `.env` não foi lido. A próxima
decisão usará o resumo nativo para escolher cobertura faltante e quais produtores
mudar, antes de adotar PostgreSQL/S3 nos consumidores. A restrição dos destinos,
backup dos conjuntos restantes, volumes/PVCs, rollback e limpeza têm gates próprios.
[Evidência de preparação](../../references/evidence/persistence_inventory_preparation_2026-10-16.json).

## Destinos de escrita — primeira cobertura operacional

O inventário nativo percorreu **4.990 arquivos/4.541.511.714 bytes lógicos**, sem
ocorrências de coleta. O único item de revisão foi `data/external/.gitkeep`, vazio;
foi reconhecido como marcador e preservado. Os 20 candidatos a banco se dividem em
2 stores MLflow originais, 1 estudo HGB, 11 bancos de eventos/checkpoints/telemetria
e 6 cópias em bundles/restores. Tamanho igual não autoriza deduplicação.

A inspeção enviada pelo autor confirmou os três volumes nomeados de RustFS,
Prometheus e Grafana e o bind somente leitura de `paced-native-v1` no exporter.
`/var/lib/docker/volumes/` é um destino gerenciado pelo Docker. Metadados de mounts
não enumeram objetos nem comprovam recuperação dos volumes; a parte dos binds de
configuração do Grafana veio cortada. [Registro nativo](../../references/evidence/persistence_inventory_author_validation_2026-10-16.json).

A primeira contenção cobre quatro pontos de entrada, escolhidos porque recebem
saídas livres e podem iniciar treino, containers ou criar estado/credenciais:

| Comando | Destino exigido |
| --- | --- |
| `python -m examples.model_contribution` | saída em `data/examples/<run>`; tracking opcional em `data/tracking/<store>` |
| `python -m examples.serving_smoke` | release em `data/serving/<release>` |
| `python -m scripts.serving.check_docker_runtime` | recibo em `data/serving/<arquivo>` |
| `python -m scripts.monitoring.local_stack init` | estado em `data/replay/<ensaio>` |

Os caminhos relativos são interpretados a partir do diretório corrente e devem
terminar dentro da área do checkout, mesmo quando fornecidos como absolutos.
O comando recusa a própria raiz da área, `..`, links simbólicos (inclusive
quebrados e links para dentro da área) e componentes incompatíveis. A validação
não cria diretórios e ocorre antes de treino, Docker ou criação de estado.
As recusas indicam a área esperada. Não há opção silenciosa de fallback.

Exemplo de uso já compatível:

```bash
poetry run python -m examples.model_contribution \
  --output "$PWD/data/examples/interface-managed-v1" \
  --tracking-root "$PWD/data/tracking/interface-smoke"
```

Esse comando treina um smoke sintético e grava tracking; não é necessário executá-lo
para validar a política. Os testes automatizados usam fixtures, stubs de efeitos
caros e diretórios temporários. Não é preciso repetir replay, backup ou recuperação.

`REPORTS_DIR` agora aponta para `data/reports/`, e `FIGURES_DIR` para sua subpasta
`figures/`. A revisão não encontrou consumidor ativo dessas constantes. A mudança
remove um default futuro na raiz; não move arquivos antigos em `reports/`.

**Cobertura parcial:** o helper atua nessas quatro CLIs. As funções internas
continuam aceitando caminhos explícitos para testes/integrações controladas; outras
CLIs, caminhos derivados e caches de dependências ainda não têm essa contenção.
Não é sandbox de sistema operacional nem proteção contra troca concorrente de
symlinks. A checagem não garante que bibliotecas só escrevam no destino validado.
O `.env` atual não é reescrito, e o checkpoint/telemetria existentes permanecem
preservados. `project_wide_storage_inventory_complete` continua falso.

Não há dependências novas, mudanças no código científico, migração, exclusão ou
alteração dos produtores de bundles congelados. Os paths aceitos poderão receber
mounts/PVCs no deployment futuro; isso ainda não configura o backend PostgreSQL/S3.
O próximo recorte operacional é um ensaio isolado do serviço MLflow PostgreSQL + S3,
com destinos explícitos, prova de artefato e rollback antes de adotar consumidores.
A cobertura das demais CLIs seguirá registrada, sem exigir inventário de todo o
Linux como condição para esse ensaio.

Validação no assistente: 24 testes novos e seleção final de **82 testes aprovados no
tox**, incluindo o teste do catálogo corrigido após a execução ampla. Ruff,
formatação e lockfile aprovados. Execução nativa dessa contenção aprovada pelo autor: 82 testes, em 2026-10-16.
[Preparação e histórico da validação](../../references/evidence/managed_output_paths_preparation_2026-10-16.json).


### Serviço MLflow PostgreSQL/S3 — ensaio isolado validado

**Gate nativo aprovado pelo autor em 2026-10-17:** uma run operacional foi
conferida por SQL, e seu artefato JSON por proxy HTTP e leitura S3 independente.
Após recriar PostgreSQL e MLflow, os registros e bytes permaneceram disponíveis.
Os IDs dos containers mudaram; imagens e volume PostgreSQL foram preservados.
O RustFS permaneceu em execução. Pytest: **1 aprovado em 21,56 s**; tox: **OK**.

A UI em `http://127.0.0.1:15001` mostra o experimento 2, run
`87ac0b4a310f4ab8952a366932c0d12a`. O ensaio cobre uma run com dois pontos de
histórico e um JSON pequeno. Adoção pelos produtores, recarga de modelo pelo novo
serviço, histórico e backup PostgreSQL seguem com critérios próprios.

[Operação e próximos critérios](../../infra/tracking/README.md).
[Evidência relatada e limites](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

### Persistência do primeiro produtor HTTP

A opção HTTP do exemplo mantém três arquivos de inspeção em
`data/examples/<execução>` e envia modelo/metadados ao serviço. No laboratório,
o destino do servidor permanece PostgreSQL `mlflow_lab` e RustFS
`fraud-tracking-lab/v1`; o cliente usa somente HTTP. A saída JSON identifica o
destino e a run/modelo. O recibo da validação opt-in fica em
`data/storage/tracking-lab/receipts/contribution-*.json`, com modo `0600`.

O catálogo acrescenta essa fronteira HTTP e seu produtor de recibos. O inventário
MLflow histórico é limitado ao store local e não resolve IDs do novo serviço;
os recibos HTTP ficam na área operacional já excluída dessa resolução. Arquivos
e fronteira continuam inventariados por metadados, sem inferir recuperação global.
Rollback de roteamento não remove dados remotos. O store local criado pelo teste
é novo/descartável; stores históricos não são abertos pelo teste nativo.
[Procedimento e limites](../modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

### Persistência da validação do HGB pelo serviço

O exemplo de contribuição teve recarga/rollback local aprovados pelo autor.
O próximo ensaio guarda fixture e fold em
`data/storage/tracking-lab/hgb-fold-<id>/dataset` e recibo em
`data/storage/tracking-lab/receipts`. O experimento remoto sintético é
`fraud-hgb-fold-smoke-http-v1`; não pertence aos snapshots históricos locais.
Um novo estudo HTTP completo vincula a URI MLflow à sua identidade, mantendo
SQLite apenas para o Optuna. O inventário entre stores e a recuperação de futuros
estudos HTTP ainda precisam de cobertura; não se declara inventário global ou
migração primária. [Procedimento e limites](HGB_TRACKING.md).

### Saídas HTTP dos três baselines

No CLI `baseline_experiment run --tracking-uri`, a saída padrão é
`data/storage/tracking-lab/baselines`; um `--output-root` explícito deve ser filho
de `data/storage`. O helper de destinos passa a aceitar a área `storage`, com
as verificações existentes de caminho e links. O modo local conserva seu destino
anterior, e APIs internas não são isoladas por essa política parcial.

O ensaio nativo preserva fixture, manifest e audit sob
`data/storage/tracking-lab/baseline-<id>` e recibo em `receipts/baseline-<id>.json`.
Esses IDs remotos não entram na varredura de referências do store local histórico.
Isso não implementa inventário entre stores, backup ou recuperação do novo fluxo.
[Comandos e falha parcial](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Trio recuperado pelo proxy: aceite de integração

O autor aprovou a recarga dos três modelos do baseline sem seus arquivos locais,
com erro zero e sem criar tracking SQLite. O diretório e recibo sintéticos foram
preservados em `data/storage/tracking-lab`.
[Evidência](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).

A prova cobre recuperação dos modelos pelo serviço em execução. Não testa perda
do PostgreSQL/volume, recuperação coordenada de metadados/S3, inventário de todos
os objetos, migração histórica ou remoção dos originais. Nenhum arquivo de dados,
objeto S3 ou volume é movido/excluído por este fechamento.

### Inventário explícito de consumidores do tracking

```bash
poetry run python -m scripts.storage.persistence_inventory --tracking-consumers
```

O coletor existente lê fontes e catálogo, confere hashes/funções e observa
chamadas sintáticas relacionadas a tracking. Não importa fontes, abre dados,
bancos/modelos ou lê credenciais. O modo padrão permanece só de metadados.
O relatório privado fica em `data/storage/inventory/tracking-consumers-*.json`.

A revisão preparada contém 17 registros, sete fronteiras locais e 55 chamadas
observadas em 124 fontes. O sucesso do recorte é `tracking_review_valid=true`
com `issues=0`; `review_required` continua correto porque os bloqueios não foram
migrados. Aliases/dinâmica/plugins podem escapar; não é inventário global.
[Mapa e protocolo de recuperação aprovado](TRACKING_RECOVERY.md).

**Recuperação da bancada aprovada pelo autor (2026-10-19):** PostgreSQL e 38
objetos S3 restaurados em volumes/rede próprios; 8 runs, 6 modelos e 44 pontos
históricos conferidos pela API. O modelo fixado recarregou pelo proxy e reproduziu
20 scores com erro máximo zero. Fonte/captura preservadas, alvo e credenciais
temporários removidos; pytest live passou em 20,89 s. **Recorte de inventário e
recuperação encerrado.** Próxima prioridade: CD/rollback do serving, seguido de
monitoramento de dados/features. Migração dos sete consumidores históricos,
cutover, limpeza e inventário global seguem em backlog próprio.
[Comandos, escopo e limites](TRACKING_RECOVERY.md).
