# MLflow com PostgreSQL e RustFS — ensaio isolado

Este recorte dá um destino de serviço aos novos registros de tracking, antes de
alterar os consumidores atuais. Usa MLflow **3.17.0**, como o cliente científico;
PostgreSQL **17.11** fixado por digest; e o RustFS já existente. Não requer cloud,
licença, reprocessamento dos datasets ou novo treinamento.

**Estado: gate nativo aprovado pelo autor em 2026-10-17.** Metadados no PostgreSQL
e artefato no RustFS foram conferidos antes e depois da recriação dos containers
PostgreSQL/MLflow. Imagens e volume foram preservados; RustFS não foi reiniciado.
[Resultado e limites](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

## Responsabilidades e destinos

| Unidade | Destino e responsável |
| --- | --- |
| Tracking HTTP/UI | `http://127.0.0.1:15001`; serviço `mlflow` do projeto Compose `fraud-tracking-lab` |
| Metadados novos | Banco `mlflow_lab`; volume `fraud-tracking-lab_postgres-data`; PostgreSQL sem porta publicada no host |
| Papel da aplicação | `mlflow`, proprietário apenas do banco dedicado, sem superusuário/CREATE ROLE/CREATE DATABASE; bootstrap administrativo separado |
| Artefatos do ensaio | Bucket `fraud-tracking-lab`, prefixo `v1/`, no RustFS existente |
| Credenciais PostgreSQL | `infra/tracking/.env`, ignorado pelo Git, criado com modo `0600`; geração exclusiva, sem sobrescrever |
| Credenciais S3 | Reutilizadas por interpolação do `.env` de observabilidade somente no servidor e no conferidor S3 |
| Probe cliente | Recebe apenas o endpoint HTTP; nenhuma credencial PostgreSQL/S3 |
| Relatórios e temporários do controle | `data/storage/tracking-lab/`; recibos `0600`, temporários removidos ao sair |
| Temporários de serviço/SDK | `/tmp` em tmpfs dos containers; imagem/raiz dos jobs e do MLflow somente leitura |

O Compose é separado da observabilidade. Ele acessa RustFS pela rede externa
`fraud-local-observability_default`. A inicialização do bucket somente consulta/cria
`fraud-tracking-lab`; não copia, enumera ou remove arquivos dos buckets anteriores.
O conferidor S3 tem as credenciais existentes do operador; a restrição ao bucket no
código não equivale a uma política IAM de menor privilégio já implementada.

O servidor usa `--serve-artifacts --artifacts-destination s3://fraud-tracking-lab/v1`.
Experimentos novos herdam URIs `mlflow-artifacts:/...`; o cliente envia/recebe bytes
pelo servidor. Não fornecer `artifact_location` local nem `--default-artifact-root`
neste ensaio. O middleware de hosts permanece ativo. Jobs assíncronos de servidor e
rollups de tracing estão desligados: não há necessidade de suas filas SQLite/Redis
para este caso de tracking. O serviço local não oferece autenticação multiusuário;
não publicá-lo fora do loopback como se fosse uma instalação de produção.

O runtime operacional tem um lock próprio com hashes (`requirements.txt`) e não
modifica `poetry.lock`. Inicialmente usa o pacote MLflow completo para manter sua
instalação suportada e evitar montar um subconjunto manual de dependências internas;
reduzir a imagem exige medir tamanho e preservar o contrato, em etapa posterior.

## Preparar e subir

Pré-requisito: a observabilidade/RustFS já inicializados, com RustFS pronto em
`http://127.0.0.1:19000/health/ready` e a rede externa existente. Na raiz do checkout:

```bash
poetry run python -m scripts.tracking.local_stack init
poetry run python -m scripts.tracking.local_stack up
poetry run python -m scripts.tracking.local_stack status
```

`init` cria apenas as credenciais dedicadas e o workspace. Se já existir `.env`,
recusa sobrescrever. Preservar o arquivo em retentativas. `up` confere a prontidão
RustFS, constrói a imagem, prepara o bucket dedicado e aguarda os dois serviços
ficarem saudáveis. O primeiro build baixa dependências operacionais; não treina.
A inicialização SQL cria o papel da aplicação somente em volume PostgreSQL novo.

Abra **http://127.0.0.1:15001**. O banco começa vazio: as **71 runs históricas não
foram migradas**. Elas continuam nos stores locais, com backups/recarga aprovados.
Os módulos atuais de treino continuam apontando para seus destinos anteriores;
nenhuma variável global `MLFLOW_TRACKING_URI` é alterada por este pacote.

## Validar persistência com pytest/tox

O teste real é explícito e fica fora da suíte padrão:

```bash
.venv/bin/python -m tox -e tracking-lab
```

Esse comando:

1. Confere identidade/saúde dos containers, volume PostgreSQL, destino S3 e ausência
   de mounts para stores históricos no servidor.
2. Cria um experimento e uma run de escopo operacional: parâmetro, dois pontos de
   histórico de métrica e um JSON pequeno. Não há modelo, dataset ou inferência.
3. Recupera o artefato pelo proxy; confere a run diretamente com SQL somente leitura,
   incluindo o papel sem superusuário; lê os bytes também diretamente do RustFS.
4. Para o MLflow e **recria somente PostgreSQL e MLflow deste laboratório**, mantendo
   o volume nomeado e os objetos. Confere que os IDs dos containers mudaram.
5. Repete as três leituras e exige paridade de metadados, histórico, URI e bytes.

RustFS, exporter, Prometheus e Grafana não são reiniciados. O teste altera a
disponibilidade dos dois serviços novos durante a recriação; eles são dedicados ao
ensaio. Uma trava impede dois ensaios concorrentes pelo mesmo controle.
Cada execução cria uma nova run/experimento; não promete reuso idempotente da run.

O recibo fixa também o código do probe/controlador e os IDs das imagens e containers.
Um probe de imagem antiga é recusado antes da recriação.

O recibo final fica em `data/storage/tracking-lab/receipts/rehearsal-*.json`.
Em falha, registra `status=failed` e o tipo do erro, sem incluir saída capturada que
possa conter credenciais; se a recriação já começou, tenta recolocar os dois
serviços em pé. Dados do ensaio são preservados, e uma falha pode deixar run/objeto
para diagnóstico. Nenhuma rotina de limpeza automática ou exclusão de volume.

O critério de aprovação é `status=success`, com `postgres_verified`, `s3_verified`,
`proxy_verified` e `container_recreation_validated` verdadeiros. Compartilhar o
resumo do teste e o JSON do recibo, sem `.env` ou `docker inspect` completo.

## Testes sem os serviços reais

```bash
.venv/bin/python -m tox -e py314 -- tests/tracking
```

As chamadas Docker são simuladas; testes reais ficam ignorados por padrão. Há
casos de destino errado, mounts históricos, serviço não saudável, falhas em SQL,
S3, escrita/leitura e recriação: nenhum pode produzir recibo de sucesso.

O runtime e o proxy HTTP também podem ser conferidos em ambiente isolado:

```bash
.venv/bin/python -m tox -e tracking-runtime
```

Esse ambiente instala o lock operacional com hashes e testa um servidor HTTP real
com banco/artefatos descartáveis locais. Não acessa os stores do projeto nem o
RustFS. É um teste de dependências/transporte, não de backend PostgreSQL/S3.

## Parar, retomar e próximos critérios

```bash
poetry run python -m scripts.tracking.local_stack down
# Para retomar, preservando .env e o volume:
poetry run python -m scripts.tracking.local_stack up
```

`down` atua apenas no projeto `fraud-tracking-lab`, sem `--volumes`. A observabilidade,
a rede externa, o RustFS e os stores anteriores permanecem disponíveis. Não usar
remoção de volumes como solução para uma falha de inicialização.

Esta prova não é migração histórica, backup PostgreSQL, recuperação após perda do
host/volume, recarga de modelo pelo novo servidor ou SLA. O volume e o RustFS
continuam no mesmo laptop. A próxima adoção deve começar por um produtor de novas
runs, com URI explícita e rollback. Depois, planejar histórico, backup/restore
PostgreSQL, consumidores de datasets e Helm/PVCs com critérios próprios.

Fontes primárias: [servidor e proxy MLflow](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/),
[artefatos](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/),
[PostgreSQL 17.11](https://www.postgresql.org/docs/release/17.11/).

A preparação anterior registrou 91 testes aprovados e 2 ignorados, além do
runtime HTTP. Esse teste chamava as funções diretamente e não detectou a mistura
de mensagens com JSON. A regressão agora executa o worker como processo separado;
ver [diagnóstico e validação](../../references/evidence/tracking_worker_json_fix_2026-10-16.json).

## Retomar após a falha no canal JSON — 2026-10-16

O MLflow 3.17.0 imprime os links da run/experimento ao encerrar a run. Essas
mensagens precediam o JSON em stdout, causando `Worker did not return a JSON result`.
O worker agora direciona as mensagens do SDK para stderr e emite apenas um JSON
final em stdout. O controlador continua recusando banners, saída vazia ou documentos
concatenados. Exceções não geram resultado de sucesso.

O autor informou serviços saudáveis e a run
`1b9495ddd4864e54ba94cd36aeaf004e`, no experimento 1. A saída do worker indica
escrita e leitura via HTTP; o controlador não chegou às leituras independentes
SQL/S3 nem à recriação. Preservar a run e o recibo
`data/storage/tracking-lab/receipts/rehearsal-09cab6fa651146ec9988875dbdd72965.json`.

Depois de aplicar a correção:

```bash
.venv/bin/python -m tox -e py314 -- tests/tracking
poetry run python -m scripts.tracking.local_stack up
poetry run python -m scripts.tracking.local_stack status
.venv/bin/python -m tox -e tracking-lab
```

Não repetir `init`. `up` reconstrói a imagem e atualiza o servidor antes de
registrar a nova baseline, mantendo `.env`, volume e bucket. Somente fazer build
não atualiza o container já em execução; a comparação de imagens do ensaio exige
que servidor e worker usem a mesma versão antes da recriação. O teste criará outra
run operacional; o registro anterior permanece disponível. Não remover volumes
ou objetos para resolver esta falha. A retentativa de 2026-10-17 gerou o recibo de sucesso registrado abaixo.

## Resultado nativo aprovado — 2026-10-17

O autor executou `up`, `status` e `tox -e tracking-lab` após aplicar a correção.
O build reutilizou a camada de dependências e terminou em 1,5 s; o bucket já
existia (`created=false`). Ambos os serviços ficaram saudáveis.

| Verificação | Resultado relatado |
| --- | --- |
| Run e experimento | `87ac0b4a310f4ab8952a366932c0d12a`, experimento `2` |
| Metadados PostgreSQL e proxy HTTP | Aprovados antes/depois da recriação; dois pontos no histórico de métrica |
| Artefato S3 | `fraud-tracking-lab/v1/2/87ac0b4a310f4ab8952a366932c0d12a/artifacts/probe.json`; bytes conferidos |
| Identidades | IDs dos dois containers mudaram; imagens e volume `fraud-tracking-lab_postgres-data` permaneceram iguais |
| RustFS | Não reiniciado |
| Teste | `1 passed in 21.56s`; tox `tracking-lab` OK em 23,79 s |

Recibo: `data/storage/tracking-lab/receipts/rehearsal-71acfcf3daca43348a02baa1d3cd27af.json`.
O assistente conciliou o JSON impresso com os hashes do código entregue e do payload
determinístico; não abriu os bytes do recibo ou os serviços nativos. A execução
completa do ensaio levou 21,5566 s, incluindo controle/recriação, sem constituir SLA.

Na UI **http://127.0.0.1:15001**, abrir o experimento 2 e a run acima permite
inspecionar parâmetro, histórico de métrica e `probe.json`. A run da primeira
falha e seu recibo continuam preservados. Não é necessário repetir o ensaio para
aplicar este fechamento documental.

A próxima adoção deve ligar um único produtor de novas runs, com URI explícita,
recarga de modelo pelo proxy e rollback. O ensaio atual utiliza somente um JSON
operacional: não transfere as 71 runs históricas, não exercita inferência pelo
novo serviço nem recupera PostgreSQL após perder volume/host. Backup PostgreSQL,
inventário global, consumidores de datasets e Helm/PVCs continuam com critérios
próprios. [Evidência detalhada](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

## Primeiro produtor de modelos — preparo de 2026-10-17

O exemplo de contribuição agora aceita `--tracking-uri http://127.0.0.1:15001`.
Esta mudança é somente no cliente; não exige reconstruir a imagem do servidor
ou repetir `init`. O experimento é `fraud-model-interface-smoke-http-v1`, separado
do experimento local e dos probes de persistência.

Com os serviços existentes saudáveis, executar:

```bash
poetry run python -m scripts.tracking.local_stack status
.venv/bin/python -m tox -e tracking-contribution
```

O teste executa a CLI, publica um modelo skops, retira temporariamente os arquivos
locais de inspeção do caminho e baixa/recarrega pelo proxy, conferindo scores.
Restaura os arquivos ao sair, verifica uma nova run local em store descartável e
exige que containers/imagens/volume permaneçam iguais. Compartilha a trava com o
ensaio de recriação, sem executar essa recriação. O cliente produtor é iniciado
sem variáveis AWS. O recibo novo fica em
`data/storage/tracking-lab/receipts/contribution-*.json`.

Cada execução bem-sucedida preserva um exemplo em `data/examples/` e uma run/modelo
HTTP; o teste de rollback não mantém outro store local. Falhas preservam o recibo
com `status=failed` e podem deixar artefatos de inspeção/run parcial para diagnóstico.
A prova deste produtor foi **aprovada pelo autor no ambiente nativo** após a
correção de transporte: recarga independente e retorno local explícito, erro zero.
O gate PostgreSQL/S3 de persistência anterior permanece no seu escopo.
[Contrato, execução manual e critérios](../../docs/modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

## Retentativa do produtor após falha de download por chunks

A primeira tentativa do produtor criou/listou os artefatos, mas falhou ao baixá-los
por chunks. A política do cliente não havia desabilitado a seleção automática de
URLs assinadas anunciada pelo backend S3. Agora o contexto HTTP exige transporte
pelo servidor MLflow, com restauração das flags ao sair. O teste real acrescentado
reproduz a seleção anterior e verifica GET do modelo pelo proxy após a correção.

Depois de aplicar `fraud-model-contribution-proxy-fix`:

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/integrations/test_mlflow_http_tracking.py \
  tests/integrations/test_mlflow_tracking.py \
  tests/modeling/contracts/test_model_interface.py

.venv/bin/python -m tox -e tracking-contribution
```

O laboratório existente deve estar saudável. A correção está no cliente: não
requer `init`, rebuild, novas credenciais ou mudança de endpoint. O teste fará
outra pequena contribuição de integração e verificará a recarga/retorno local;
não reutiliza nem apaga a run da falha. Preserve o recibo
`contribution-30de2840b5824d039bf642699582dc13.json`, run
`5d865db80aa64100be02179d0158e761` e modelo
`m-ba7a41359c2242c58eec7a080528445f`. Compartilhe a saída e o novo recibo.

A retentativa nativa retornou `status=success` e foi aprovada pelo autor. O gate anterior
de persistência segue no seu escopo; o probe em rede Docker não distinguia
streaming no servidor de URLs assinadas. [Diagnóstico e inferências](../../docs/operations/MLFLOW.md#download-por-chunks-e-capacidades-s3--correção-de-2026-10-17).

### Próximo aceite — um fold HGB

A retentativa `tracking-contribution` foi aprovada pelo autor em 2026-10-17:
recarga independente e rollback local, erro zero e containers preservados.
O próximo ensaio é `.venv/bin/python -m tox -e tracking-hgb`, com um fold
sintético no experimento `fraud-hgb-fold-smoke-http-v1`. Não exige build/init,
reinício ou alteração de credenciais. Não executa a otimização histórica.
[Procedimento, identidade e limites](../../docs/operations/HGB_TRACKING.md).

### Trio de baselines — regressão explícita

O fold HGB já foi aprovado pelo autor: erro zero, reutilização e containers
preservados. O comando de regressão é `.venv/bin/python -m tox -e tracking-baseline`:
três modelos sintéticos (dummy/regressão/HGB), em runs independentes no experimento
`fraud-baseline-smoke-http-v1`, com recarga sem saídas locais.
Não precisa de build/init ou reinício. O ensaio não executa a comparação Handbook
ou a busca Optuna. O aceite nativo desse trio foi aprovado pelo autor em 2026-10-17.
[Comandos e limites](../../docs/modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Resultado do trio e próximo limite

O autor aprovou `tracking-baseline`: 1 teste em 6,55 s, tox OK em 19,96 s;
três runs independentes e erro zero nas recargas sem modelos locais disponíveis.
Os containers/imagens/volume não mudaram. [Evidência](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).
Não é necessário reiniciar serviços ou repetir esse ensaio para encerrar a etapa.
Restauração PostgreSQL/S3, consumidores históricos e cutover conservam gates próprios.

### Revisão de consumidores antes da captura/restauração

O próximo comando é de fontes, sem contato com esta bancada:
`poetry run python -m scripts.storage.persistence_inventory --tracking-consumers`.
O mapa preparado tem 17 registros e sete fronteiras locais; conferir
`tracking_review_valid=true` e `issues=0`. O inventário não lê `.env`, bancos ou
modelos. O coletor padrão de metadados continua disponível.

O protocolo implementado de recuperação usa projeto/volumes/rede próprios e
fonte preservada; seu aceite nativo está registrado abaixo. Este fechamento
é documental e não inicia jobs nem para serviços.
[Protocolo e limites](../../docs/operations/TRACKING_RECOVERY.md).

**Recuperação da bancada aprovada pelo autor (2026-10-19):** PostgreSQL e 38
objetos S3 restaurados em volumes/rede próprios; 8 runs, 6 modelos e 44 pontos
históricos conferidos pela API. O modelo fixado recarregou pelo proxy e reproduziu
20 scores com erro máximo zero. Fonte/captura preservadas, alvo e credenciais
temporários removidos; pytest live passou em 20,89 s. **Recorte de inventário e
recuperação encerrado.** Próxima prioridade: CD/rollback do serving, seguido de
monitoramento de dados/features. Migração dos sete consumidores históricos,
cutover, limpeza e inventário global seguem em backlog próprio.
[Comandos, escopo e limites](../../docs/operations/TRACKING_RECOVERY.md).
