# MLflow e persistência dos modelos

Os novos treinamentos usam `baseline_v2`: pipelines completos em skops e publicação
nativa no MLflow durante a mesma execução. A política científica continua em
`baseline_protocol_v1.json`: candidatos, features, janelas e métricas não mudaram.

## Execução atual

```bash
poetry install
make validate
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment run
make mlflow-ui
```

O treinamento cria três runs principais independentes no experimento
`fraud-temporal-baseline-v2`, uma por candidato. Cada run contém um único pipeline
nativo sklearn/pyfunc, incluindo o scaler da regressão logística, parâmetros,
métricas de validação, assinatura e versões para carregamento. O pyfunc usa
`predict_proba`, com classes `[0, 1]`; a coluna 1 é o score de fraude.
Scores com pesos de classe são usados para ranking, sem promessa de calibração.

A publicação usa `mlflow.sklearn.log_model(serialization_format="skops")`.
Tipos internos necessários ao gradient boosting estão explicitamente revisados.
A recarga local e a recarga do pyfunc devem preservar todos os scores de validação
com `rtol=atol=1e-12`. Um erro na recarga faz a run MLflow falhar.
Não há autolog, runs filhas, registro como champion ou avaliação do teste.

O manifesto e a auditoria locais ligam cada modelo ao `run_id` e `model_uri`
retornados pelo MLflow. A URI nativa pode ter formato `models:/...`; copie a URI
retornada, sem reconstruí-la a partir do ID da run. Uma nova execução de treinamento
cria outra execução local e outras runs, mesmo com parâmetros iguais.

Para fixtures ou uma investigação local explicitamente sem tracking:

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment run --no-track
```

O manifesto registra `tracking: {}` nesse caso. O padrão da CLI inclui MLflow.

## Verificação e armazenamento

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment verify "<baseline_path>"
poetry run python -m fraud_detection_mlops.integrations.mlflow_tracking verify "<model_uri>"
```

O primeiro comando confere arquivos e recalcula métricas sem carregar modelos,
para artefatos históricos `baseline_v1` ou atuais `baseline_v2`.
O segundo verifica formato, assinatura, classes/features e carregamento do modelo
no armazenamento local. A comparação com os scores de validação ocorre na publicação;
a verificação isolada da URI não repete essa comparação.

SQLite: `data/tracking/mlflow.db`. Artefatos: `data/tracking/artifacts/`.
UI: <http://127.0.0.1:5001>. `--tracking-root` permite usar outro diretório local.
O backend e os artefatos existentes continuam acessíveis, incluindo as três runs
históricas do autor. Preserve banco e artefatos juntos, com os escritores parados
antes de copiar. URIs de artefatos locais absolutas exigem cuidado ao mover o checkout.

O [plano de armazenamento](STORAGE.md) prevê artefatos em RustFS/S3 e backup
consistente dos metadados. Essa migração ainda não foi implementada; copiar os
arquivos não atualiza URIs já gravadas. **PostgreSQL é o destino escolhido para o backend do novo servidor MLflow**.
O SQLite permanece ativo até migrar e reconciliar IDs/runs, parâmetros, métricas
e URIs de artefatos, com backup, recarga e rollback demonstrados. Não retreinar
modelos históricos para essa migração. O código atual força SQLite: trocar
`MLFLOW_TRACKING_URI` ou atualizar schema não copia os registros entre bancos.
O piloto S3 atual não migra esse tracking nem reescreve suas URIs.

O staging local só é publicado após os controles e o tracking solicitados passarem.
Se a publicação de um candidato falhar, a auditoria registra a falha e as runs que
já concluíram. Essas runs individuais podem continuar `FINISHED`; isso não significa
que o baseline completo foi publicado. Não há transação distribuída, retomada de
migração, reutilização automática de runs ou promoção automática. Investigue a
falha antes de decidir por outro treinamento; uma repetição terá novo ID.

## Experimento histórico preservado

A migração `mlflow_skops_v1` foi executada e verificada pelo autor em 2026-10-07,
sem refit e sem avaliar o teste. [Recibo](../../references/evidence/mlflow_execution_2026-10-07.json).
O código que a realizou é recuperável no Git em `6ba10b5`; foi retirado da árvore
ativa, sem uma cópia alternativa de scripts legados. Não existe mais comando `publish`
nem opção `--trust-local-models` nesta versão.

O [notebook 06](../../notebooks/stages/06_mlflow_tracking.ipynb) reúne o procedimento
atual e os resultados históricos. Outputs reais da migração foram preservados como
registros estáticos em Markdown; as células executáveis usam a API atual e aguardam
execução local. A versão executada anterior é recuperável no Git. Não há notebook
07 separado, nem é necessário retreinar o baseline histórico para esta refatoração.


## Busca Optuna temporal

O experimento `fraud-temporal-optuna-v1` registra uma run principal por ajuste de
modelo/corte: três referências e até 60 ajustes de busca. Cada run contém Pipeline
completa, parâmetros, métricas, scores e proveniência de protocolo/dados/features.
`study_candidate` e `fold_id` relacionam a run ao trial persistido em SQLite; nenhum
modelo é registrado/promovido automaticamente. O relatório do estudo agrega os
cortes, sem guardar múltiplos modelos em uma mesma run. Fits concluídos são reutilizados.
Recuperação parcial e orçamento estão no [protocolo](../modeling/EVALUATION_PROTOCOL.md#otimização-temporal-do-hgb-com-optuna-v1).


## Inventário antes da migração PostgreSQL + S3

**Inventário nativo corrigido e revisão do Default concluídos pelo autor em 2026-10-16.**
[Evidência nativa](../../references/evidence/mlflow_inventory_author_validation_2026-10-16.json).
O destino escolhido continua sendo um servidor MLflow com metadados PostgreSQL
e artefatos em RustFS/S3. Este incremento levanta o estado atual para definir a
transferência e sua aceitação; não instala servidor nem migra banco/artefatos.

O backend atual é fixado por `integrations/mlflow_tracking.py`: `local_tracking`
seleciona `sqlite:///.../mlflow.db`, inclusive quando há variável de ambiente
externa. Verificações de avaliação, ablação e exportação de serving também
exigem esse arquivo. Portanto, configurar somente `MLFLOW_TRACKING_URI` não
substitui essa integração. A mudança futura precisa cobrir esses consumidores.

### Executar o inventário local

Encerre a UI MLflow se estiver aberta (Ctrl+C no terminal que a iniciou).
Não execute treino, avaliação ou outro escritor de tracking durante a coleta.
Não é necessário interromper RustFS, Prometheus ou Grafana para este comando.

```bash
poetry run python -m scripts.storage.mlflow_inventory
```

O comando usa `data/tracking/mlflow.db` e `data/tracking/artifacts/` da raiz do
projeto. Escreve somente um relatório novo `data/storage/inventory/mlflow-*.json`,
com permissão 0600, e imprime um resumo. Não importa/inicializa MLflow nem conecta
ao SQLite original: copia banco e WAL, quando presente, para diretório temporário,
consulta a cópia via sqlite3 somente leitura e remove a cópia ao terminar.
SHM original é apenas identificado/conferido; a cópia usa seu próprio estado
transitório. Um rollback journal não vazio é recusado para revisão do estado.

Os fingerprints e metadados dos arquivos de banco são comparados antes/depois.
Artefatos são lidos em blocos para SHA-256; mudanças de arquivo/conjunto ou dos
JSONs de referência invalidam a coleta. Esses checks observacionais **não
substituem lock compartilhado nem backup coordenado**: mantenha escritores parados.
Sem banco existente, o comando falha sem criar outro store.

### Cobertura e interpretação

| Parte | O que o relatório contém | Limite |
| --- | --- | --- |
| Backend SQLite | Revisão Alembic; nomes/colunas, contagens e fingerprints de todas as tabelas; quick_check e foreign keys na cópia | Fingerprint SQLite não prova equivalência entre dialetos |
| Experimentos/runs | IDs, ciclo de vida, status e localizações de artefatos; inclui runs apagadas e histórico completo de métricas no fingerprint | Não imprime valores de parâmetros/tags; não altera estados |
| Modelos MLflow 3 | IDs de logged models, source run, status numérico do schema e localizações; contagens de registry/model versions | Logged model não equivale a versão registrada/champion |
| Arquivos | Paths, tamanhos e SHA-256 de arquivos regulares sob tracking/artifacts | Locais externos/remotos são classificados sem acesso; sem desserialização |
| Referências | JSONs em data/, exceto tracking e storage; campos run_id/mlflow_run_id, model_uri/logged_model_uri, tracking_root/tracking_uri e artifact_uri/artifact_location | IDs de 32 hex exigem vínculo existente, campo mlflow_run_id, contexto tracking/mlflow ou recibo mlflow.json; UUIDs locais genéricos ficam sem classificação. Model_uri ao lado não prova que run_id seja MLflow; tags opacas, MLmodel YAML, código/notebooks e evidências históricas não são ligados exaustivamente |

URIs externas/sensíveis têm valor redigido; não há leitura de .env, credenciais
ou rede. O relatório completo guarda paths locais e IDs para revisão no host;
o resumo é suficiente para a próxima conversa. Não publicar o relatório bruto
automaticamente. Nenhum artefato sem referência é declarado descartável.

`status=success` indica coleta completa sem os pontos de revisão identificados.
`review_required` também preserva o relatório: examinar runs/modelos pendentes,
IDs ausentes, URIs fora do diretório gerenciado ou localizações não existentes.
Um experimento vazio ou run falha pode legitimamente não ter diretório de
artefatos; distinguir isso de perda de arquivos. `complete=true` cobre a coleta
declarada, não a cobertura remota nem aprovação de migração/recuperação.
Se a coleta falhar por mutação, pause o escritor e repita; não encerrar runs
históricas, excluir WAL/SHM ou alterar URIs para fazer o inventário passar.

### Critérios antes de trocar o tracking

| Gate | Evidência exigida |
| --- | --- |
| 1. Inventário nativo | Contagens, schema/revisão, cobertura de artefatos e vínculos; revisar itens pendentes antes de escolher o método de transferência |
| 2. Backup restaurável | Escritores parados; snapshot consistente dos metadados junto a manifesto/checksums de artefatos; restaurar em diretório isolado e reconciliar antes de tocar na origem |
| 3. Ensaio PostgreSQL + S3 | Serviços locais com versões fixadas; schema compatível com MLflow 3.17.0; testar método de transferência sobre cópias, incluindo relações e histórico, antes da troca |
| 4. Identidade e URIs | Preservar experiment/run/model IDs, parâmetros, histórico de métricas, tags e ciclo de vida; mapa explícito de cada localização de artefato para S3; models:/ e runs:/ precisam continuar resolvendo. Se algum método gerar novos IDs, não aceitar como migração transparente |
| 5. Recarga e consumidores | Downloads/checksums, modelo/assinatura/classes/features e scores de controle dentro da tolerância existente; avaliar/exportar via servidor HTTP sem exigir mlflow.db local; nenhum refit ou novo uso do holdout |
| 6. Troca e retorno | Só trocar configuração após paridade/saúde; escrita de controle isolada no novo servidor e leitura de artefato S3; restaurar backup PostgreSQL e artefatos e ensaiar retorno ao tracking anterior, declarando o tratamento de escritas novas |

O gate 2 tem preparo/verificação offline nativos aprovados. A transferência S3
teve publicação/reenvio/recuperação nativos aprovados. Restauração operacional
e recarga continuam pendentes; gates 3–6 seguem abertos.
O inventário nativo delimitou a cobertura atual; o ensaio de migração ainda deve
demonstrar que o método escolhido preserva as identidades; não há promessa de conversão genérica SQLite→PostgreSQL por um comando.
Não fazer re-log dos modelos históricos com IDs novos nem editar recibos antigos
para aparentar continuidade. Alterações de paths serão explícitas e rastreáveis.

Acesso futuro dos clientes será pelo servidor MLflow HTTP; PostgreSQL é seu
backend. Banco e artefatos terão persistência/backup próprios. O Optuna concluído
e os checkpoints de replay permanecem nos usos SQLite já justificados. KinD/Helm
e PVCs vêm após recuperação e rollback; não são necessários para este inventário.

A documentação oficial separa [backend de metadados](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/)
e [armazenamento de artefatos](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/),
incluindo localizações persistidas por experimento/run. `mlflow db upgrade`
atualiza schema e não representa, neste plano, a transferência entre bancos.
Referências consultadas em 2026-10-16; o inventário foi testado com o schema do
SDK **3.17.0 fixado no lock**, sem atualizar a dependência para seguir docs latest.

Preparação: tox `py314` aprovou **28 testes/27 avisos**, incluindo **19 casos do
inventário**, Ruff, formatação e lockfile. [Evidência de preparação](../../references/evidence/mlflow_inventory_preparation_2026-10-16.json).


### Revisão da coleta nativa inicial — 2026-10-16

O autor executou 19 testes (uma depreciação SQLAlchemy) e coletou o inventário
`data/storage/inventory/mlflow-h1r78wa0.json`: 5 experimentos, 70 runs FINISHED,
66 logged models, nenhum registered model/model version, 627 arquivos de artefatos
com 253.128.963 bytes e 285 JSONs de referência. Schema Alembic dc11669786a5;
632 parâmetros, 315 métricas/315 latest_metrics e 1.321 tags. Não houve localizações
de artefatos gerenciados ausentes. Esses números foram impressos pelo autor;
o assistente não acessou banco, arquivos ou bytes do relatório nativo.

As duas referências não resolvidas eram o mesmo UUID local
961bb0c32fb84433af922908ea58e76b, em report.json e runs/diagnostics_*.json.
O gerador `evaluation/validation_diagnostics.py` cria run_id com uuid4().hex;
model_uri identifica o modelo de origem e mlflow_run_id identifica a run do
tracking no relatório final. A presença de model_uri foi uma heurística incorreta
no inventário. Foi removida, mantendo IDs conhecidos e contextos explícitos como
referências MLflow; IDs locais ambíguos continuam visíveis como unclassified_run_id,
sem afirmar que estão ausentes do tracking. Novos testes cobrem relatório/auditoria
com esse formato e conservam a detecção de IDs explicitamente MLflow ausentes.
Nenhum relatório/recibo científico é alterado para corrigir essa classificação.

A localização fora da cobertura pertence ao experimento 0, Default:
`/home/wanderson/Documentos/fraud-detection-mlops/mlruns/0`. Não é ignorada
automaticamente. O autor conferiu as associações e metadados locais; o resultado
está registrado a seguir. Revalidar essa condição antes de preparar o backup.
Não mover/excluir arquivos nem reescrever o artifact_location para eliminar o aviso.
O relatório original permanece preservado; a execução corrigida grava outro arquivo.


### Resultado corrigido e fechamento do inventário — 2026-10-16

O autor aprovou **21 testes/um aviso em 7,31 s** e coletou
`data/storage/inventory/mlflow-ljdjyua8.json`: as mesmas 70 runs FINISHED,
66 logged models e 627 arquivos/253.128.963 bytes; agora **zero referências não
resolvidas** e **zero localizações gerenciadas ausentes**. O Default tem zero
runs e logged models na coleta inicial usada para a inspeção; o path mlruns/0
não existe. As associações vieram do relatório inicial e o filesystem foi
conferido separadamente: não se afirma snapshot atômico entre essas observações.

O único aviso restante é a localização declarada desse experimento. É metadado
histórico sem payload existente observado, não uma run perdida. Preservar a linha
do Default e seu artifact_location no backup; não criar diretório vazio, editar
URI ou suprimir o check globalmente para produzir status success.
O JSON bruto conserva **review_required**; a revisão humana fecha o gate do
inventário para o estado observado. Isso não aprova backup, recarga ou migração.
[Evidência e limites](../../references/evidence/mlflow_inventory_author_validation_2026-10-16.json).

### Backup offline do tracking: preparo e verificação

**Preparo e verificação nativos aprovados em 2026-10-16 para os dois stores; ciclo S3 nativo aprovado para o snapshot; recarga operacional e migração pendentes.**
`scripts/storage/mlflow_backup.py` oferece somente `prepare` e `verify`.
O perfil `fraud_mlflow_tracking_bundle_v1` preserva a unidade de recuperação:
SQLite normalizado, todos os artefatos e arquivos regulares auxiliares do tracking,
diretórios vazios, inventário e recibo de captura. Não há novo serviço, cliente
S3 ou dependência. Os perfis anteriores conservam seus contratos; o worker compartilhado passa a aceitar o snapshot MLflow sem alterar archive.py.

#### Captura e cobertura

1. Com UI MLflow, treino, avaliação e demais escritores parados, o operador declara
   `--writers-stopped`. O comando recusa runs/modelos não terminados, referências
   ausentes, URI externa sem cobertura, bancos/sidecars fora dos dois stores
   suportados e colisões de nomes.
2. Refaz o inventário e identifica **todo data/tracking**, incluindo auxiliares
   fora de artifacts. Revalida o Default: somente o experimento 0 sem runs/modelos
   e com o path declarado ausente é aceito sem payload; qualquer mudança é recusada.
   Registry/model versions e outras tabelas com localizações de artefatos ainda
   não mapeadas são recusadas quando contêm dados, sem descartar seus registros.
3. Copia banco/WAL para área temporária gerenciada, confere contra o inventário e
   aplica SQLite Online Backup API **nessa cópia**, produzindo mlflow.db com journal
   DELETE. SHM não integra payload; não conecta SQLite/MLflow na fonte original.
4. Copia arquivos regulares em blocos, preserva diretórios e confere integridade,
   foreign keys, conteúdo lógico de todas as tabelas, IDs/URIs e hashes/conjunto
   dos artefatos contra o inventário arquivado. Não carrega modelos ou faz refit.
5. Refaz a observação de fonte e referências e só publica o diretório final após
   verificação e ausência de mutação. Checks observacionais **não são lock global
   dos escritores**; a quiescência declarada é pré-condição, não uma parada automática.

As URIs e referências históricas ficam intactas. O snapshot SQLite pode ter bytes
físicos diferentes da fonte; a equivalência é conferida pelo conteúdo de todas as
tabelas. A assinatura de código produtor identifica archive.py, mlflow_inventory.py
e mlflow_backup.py, e o recibo registra Python. Repetição sobre fonte/código/ambiente
inalterados reutiliza o bundle após verificar seu conteúdo; destino inválido é recusado.

#### Destinos e ciclo de vida

| Saída | Destino explícito | Ciclo de vida |
| --- | --- | --- |
| Staging/cópia SQLite/inventários transitórios | data/storage/mlflow-backup-* e fraud-mlflow-inventory-* | Diretórios temporários gerenciados e removidos ao terminar com sucesso/falha |
| Snapshot local validado | data/storage/bundles/<bundle_id>/ | Conteúdo endereçado por SHA-256; conservar até transferência/recuperação e política de retenção aprovadas |
| Recibo da operação | data/storage/receipts/mlflow-backup-prepare-*.json ou verify-*.json | Novo arquivo por operação, permissão 0600, sem sobrescrever resultados anteriores |
| Inventário/captura arquivados | Payload data/tracking/storage-inventory.json e storage-snapshot.json | Metadados do backup, separados da fonte; os nomes são reservados e colisão na fonte é recusada |

O perfil tem **1.024 arquivos/256 MiB**, manifesto externo até 256 KiB. Isso comporta
a unidade com 627 artefatos observados sem dividir o tracking nem ampliar perfis
de dataset/estudo. Preflight mede arquivos/banco/sidecars; validação final inclui
banco normalizado e metadados no total. Se exceder, para sem ampliar limites ou
excluir conteúdo. O espaço livre precisa cobrir staging e verificação. Os bytes
observados de artefatos não são usados como estimativa do backup total.

#### Primeiro gate nativo: preparar o tracking existente

Encerre a UI (Ctrl+C no terminal que a iniciou) e não rode escritores de tracking
durante a captura. Os demais serviços podem continuar rodando.

```bash
poetry run python -m scripts.storage.mlflow_backup prepare --writers-stopped
```

Enviar o JSON produzido. Se a fonte não mudou desde o inventário revisado, esperamos
70 runs/66 logged models/627 artefatos no principal, mais 1 run/1 modelo/6 artefatos
no store de interface; ver a correção de cobertura abaixo. Arquivos/bytes totais, auxiliares, SHA do
snapshot, digest lógico e bundle ID serão **medidos**, sem inventar o resultado.
O comando não acessa S3 nem exige build de imagem. A captura inclui dados privados
do tracking; não publicar automaticamente seu conteúdo bruto.

Após conferir o preparo, a referência de verificação é:

```bash
poetry run python -m scripts.storage.mlflow_backup verify --bundle-id <bundle_id>
```

`verify` usa somente o bundle: confere arquivos/conjunto/diretórios e reaplica
checagens SQLite/metadados, sem consultar o tracking ou paths históricos da fonte.
Os testes verificam isso após remover as fontes e relatórios gerados das fixtures.
Não adapta URIs, inicializa MLflow ou faz recarga/inferência. O worker agora aceita esse perfil local; a transferência tem verificação e gate nativo próprios, descritos abaixo.
Restore operacional com MLflow, paridade do modelo, adoção HTTP, PostgreSQL e
rollback continuam pendentes. Nenhuma limpeza da origem é automática.

Preparação aprovada: tox py314 com **111 passed/1 skipped/27 warnings**, incluindo
37 casos do backup; Ruff/formatação/lockfile aprovados. SDK isolado: cinco casos
aprovados. [Evidência de preparação](../../references/evidence/mlflow_backup_preparation_2026-10-16.json).


### Cobertura dos dois stores MLflow — correção de 2026-10-16

O primeiro preparo nativo foi interrompido antes de publicar o snapshot: havia
um segundo banco previsto pelo exemplo de interface, mas não coberto pelo preparo.
O autor identificou `data/tracking/interface-smoke/mlflow.db` (954.368 bytes) e
inventariou esse store: 2 experimentos, 1 run FINISHED, 1 logged model e 6 arquivos
com 32.088 bytes; nenhuma localização gerenciada ausente. O path fora de artifacts
é o mesmo `mlruns/0`, cujo enquadramento como Default vazio/sem associações é
revalidado na captura. O relatório informado é
`data/storage/inventory/mlflow-interface-smoke-4ph8q2z3.json`.
São saídas de terminal fornecidas pelo autor; o assistente não recebeu os bancos,
artefatos ou o JSON integral. Os 58 testes nativos anteriores passaram, mas não
cobriam a presença desse store auxiliar dentro da unidade de recuperação.

O preparo agora contempla explicitamente `data/tracking` e, quando existir seu
banco, `data/tracking/interface-smoke`. Cada store tem inventário, banco normalizado
com seu WAL committed, integridade/FKs, conteúdo de todas as tabelas, IDs/URIs e
artefatos verificados separadamente. O conjunto é observado novamente antes da
publicação local; escritores de **ambos** os stores devem permanecer parados.
Não é uma transação simultânea entre bancos nem um lock compartilhado de escritores.
Referências JSON do projeto são conciliadas com os IDs dos dois stores, recusando
IDs duplicados/ambíguos. Um banco em outro path continua exigindo cobertura explícita;
não se transforma em arquivo auxiliar nem é ignorado. Não há fusão dos stores,
remoção de arquivos ou alteração dos produtores científicos neste incremento.

O manifesto mantém `fraud_mlflow_tracking_bundle_v1`, com duas entradas extras de
proveniência quando o store auxiliar está presente. O inventário coordenador e o
recibo de captura registram esse store; seu inventário também acompanha o payload
em `data/tracking/interface-smoke/storage-inventory.json`. O verificador mantém
suporte ao snapshot de um único store e exige cobertura coerente para o segundo.
Os limites de 1.024 arquivos/256 MiB valem para o **pacote inteiro**, incluindo os
dois bancos normalizados, artefatos, auxiliares e metadados. Não foram ampliados.

O resultado inclui `stores` e `store_count`, `total_runs` e `total_logged_models`.
`runs`, `logged_models` e os campos de banco/tabelas legados continuam descrevendo
o store principal; `artifact_files`, `artifact_bytes` e `default_reviews` são totais.
Se as fontes permanecerem iguais às observações, a referência é **70 + 1 runs,
66 + 1 modelos e 627 + 6 artefatos**, preservando sua separação. Isso é expectativa
condicional, não resultado de um backup nativo já aprovado.

Repetir, com UI/treino/avaliação e demais escritores parados:

```bash
poetry run python -m scripts.storage.mlflow_backup prepare --writers-stopped
```

Enviar o JSON resultante antes da próxima etapa. A correção tem regressão pytest/tox com stores gerados pelo SDK. A retentativa
nativa e a verificação do bundle foram aprovadas, conforme o registro abaixo. S3, recarga
operacional, PostgreSQL, adoção primária, inventário global e limpeza continuam
abertos. Evidências anteriores preservadas, sem refit ou nova avaliação científica.

[Evidência da correção e diagnóstico](../../references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).


### Preparo e verificação nativos do MLflow — 2026-10-16

O autor executou o tox selecionado: **70 passed, 1 warning**, Ruff, formatação
(235 arquivos) e lockfile aprovados. O aviso é a depreciação noload do MLflow/SQLAlchemy.
O comando direto `.venv/bin/python -m tox` funcionou; a causa exata do erro de
argumentos no launcher via Poetry não foi determinada.

`prepare` retornou `prepared_verified_locally`; `verify` retornou `verified_locally`
para o mesmo bundle `c9c4e6e0f684ba21484e7491c026a24a76002570c251c97c98b244f043a4d924`, código produtor,
hashes, contagens e bytes. Verificação de integridade/FKs, conteúdo de todas as
tabelas e URIs históricas aprovada; `verify` informou `original_source_read=false`.

| Store | Runs | Logged models | Artefatos | Bytes dos artefatos |
| --- | ---: | ---: | ---: | ---: |
| Principal | 70 | 66 | 627 | 253.128.963 |
| Interface sintética | 1 | 1 | 6 | 32.088 |
| Total | 71 | 67 | 633 | 253.161.051 |

O pacote possui **640 arquivos/256.354.588 bytes**, incluindo dois bancos, dois
inventários, captura e dois auxiliares. Está abaixo de 1.024 arquivos/256 MiB.
Os dois Default foram revalidados. Os bancos/recibos completos não foram recebidos
pelo assistente; foram conciliadas as saídas impressas, sem rehash independente.
Os originais nativos não foram removidos e MLflow/modelos não foram recarregados.
[Evidência nativa](../../references/evidence/mlflow_backup_author_validation_2026-10-16.json). Gate de preparo/verificação local fechado.

### Transferência e recuperação S3 do snapshot MLflow

`scripts/storage/mlflow_transfer.py` publica/restaura o **bundle já preparado**;
não captura bancos ativos e não depende da presença dos stores originais.
Reutiliza `bronze_transfer.run_worker` e o job `storage-transfer`, sem novo serviço,
SDK ou lockfile. O perfil continua `fraud_mlflow_tracking_bundle_v1`, 1.024
arquivos/256 MiB; limites dos demais arquivos/datasets/estudos não são ampliados.
Bucket **fraud-artifacts**, prefixo **mlflow-tracking/v1/<bundle_id>/**.

Publicação: o host verifica o snapshot inteiro antes do job; o worker valida o
manifesto e os bytes, usa PUT condicional e lê integralmente cada objeto de volta.
O manifesto de conclusão é publicado por último; reenvio reutiliza apenas objetos
com tamanho/hash corretos, e objetos extras ou conflitos impedem aprovação.
Foram verificados **641 objetos** no ciclo nativo do bundle atual, com publicação,
reenvio idempotente e recuperação aprovados pelo autor. O upload não altera URIs ou registros de nenhum banco.

Recuperação: o worker baixa um conjunto exato para `data/storage/downloads/mlflow-tracking-*`.
Falhas de transporte removem o download parcial. Após conferir os bytes, o host
reconstrói somente os diretórios registrados (S3 não guarda diretórios vazios) e
verifica novamente integridade/FKs/tabelas/IDs/URIs e artefatos de cada store.
Somente então renomeia para `data/storage/restored/<bundle_id>/`. Destino existente
é recusado. Falha semântica preserva a quarentena para diagnóstico, sem criar um
restore final. Arquivos recuperados e recibos têm modo 0600; quarentena é 0700.
O caminho Default ausente não é fabricado, pois não integra os diretórios arquivados.

O worker permanece stdlib + boto3/botocore operacionais, sem MLflow/scikit-learn.
Dockerfile/allowlist passam a incluir mlflow_inventory.py e mlflow_backup.py para
validar o perfil; handshake de código abrange os quatro módulos executáveis.
O código produtor do bundle e os bytes do snapshot anterior permanecem intactos.
Reconstruir a imagem é necessário para atualizar esse job, sem reiniciar observabilidade.

#### Comandos do ciclo S3 validado para o snapshot existente

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml \
  --profile storage-tools build storage-transfer

curl --retry 10 --retry-delay 2 --retry-connrefused \
  --connect-timeout 3 --max-time 5 --retry-max-time 40 \
  -fsS -o /dev/null -w 'RustFS HTTP %{http_code}\n' \
  http://127.0.0.1:19000/health/ready

poetry run python -m scripts.storage.mlflow_transfer publish \
  --bundle-id c9c4e6e0f684ba21484e7491c026a24a76002570c251c97c98b244f043a4d924
```

RustFS deve estar iniciado pelo perfil storage. O build não inicia o serviço.
O autor concluiu publicação, reenvio e recuperação conforme o registro abaixo.
Os comandos de referência para reenvio e recuperação são:

```bash
poetry run python -m scripts.storage.mlflow_transfer publish --bundle-id <bundle_id>
poetry run python -m scripts.storage.mlflow_transfer restore --bundle-id <bundle_id>
```

Recibos ficam em `data/storage/receipts/mlflow-publish-*.json` e
`mlflow-restore-*.json`; o job monta somente data/storage e não recebe os stores
ativos. Não há path de saída arbitrário. O restore não inicializa MLflow ou adapta
URIs; não comprova `models:/`, serving ou treino retomados. Recarga operacional,
paridade, PostgreSQL + S3, consumidores HTTP e rollback continuam nos próximos
gates. O snapshot no RustFS do mesmo laptop não constitui cópia independente da
máquina. Inventário global/limpeza permanecem abertos.


### Ciclo S3 nativo do snapshot MLflow — 2026-10-16

O autor aprovou build do job atualizado, readiness RustFS HTTP 200 e tox py314
selecionado: **16 passed, 1 warning**, 12,34 s de pytest/18,79 s de tox. Aviso
conhecido de noload no MLflow/SQLAlchemy. As três saídas S3 mantêm bundle
`c9c4e6e0f684ba21484e7491c026a24a76002570c251c97c98b244f043a4d924`, fingerprints de produtor/worker,
versões boto3/botocore 1.43.114, contagens e hashes dos dois stores iguais aos
observados no preparo/verify local.

| Operação | Resultado informado |
| --- | --- |
| Publicação | 641 uploads, zero reutilizados, 641 objetos verificados |
| Reenvio | Zero uploads, 641 reutilizados e verificados |
| Recuperação isolada | 640 arquivos/256.354.588 bytes, 641 objetos verificados |
| Conteúdo | Dois stores; 71 runs, 67 logged models, 633 artefatos, dois auxiliares |
| Bancos | Integridade, foreign keys, conteúdo de tabelas e URIs históricas aprovados |

Recibos informados em data/storage/receipts:
`mlflow-publish-dt8j82lc.json`, `mlflow-publish-1_46ucd7.json` e
`mlflow-restore-tncsnmve.json`. Restore em
`data/storage/restored/c9c4e6e0f684ba21484e7491c026a24a76002570c251c97c98b244f043a4d924/`.
Os tempos do worker foram 8,881 s/2,767 s/2,223 s; excluem a verificação no host
e não medem duração completa de recuperação. Não usar como SLA ou benchmark completo.

O restore informou `original_source_read=false`, com as mesmas assinaturas dos
bancos físico/lógico e as contagens por store. O assistente conciliou saídas de
terminal; não recebeu os bancos, objetos, arquivos ou JSONs integrais dos recibos
para recalcular hashes. Os originais nativos não foram removidos. Nenhum modelo
foi carregado e MLflow não foi inicializado. A evidência de preparação mantém seu
estado histórico; este registro acrescenta o resultado posterior.
[Evidência nativa](../../references/evidence/mlflow_transfer_author_validation_2026-10-16.json). **Gate de arquivamento/recuperação S3 deste snapshot fechado.**

#### Próximo gate delimitado: recuperar o uso operacional

O ensaio abaixo implementa o acesso ao histórico e ao modelo em cópia descartável.
Testes com stores gerados pelo SDK passaram; **a execução nativa foi aprovada
pelo autor em 2026-10-16**, conforme o resultado registrado ao final.
Depois dessa prova, delimitar o ensaio PostgreSQL + S3, preservando IDs, parâmetros,
histórico de métricas, tags, ciclo de vida e mapa de URIs. Adoção pelos consumidores
e rollback são gates próprios. Helm/PVCs, cópia independente da máquina, inventário
global e limpeza dos produtores antigos continuam abertos.

## Ensaio descartável de recuperação MLflow

Implementado em 2026-10-16 para responder a uma pergunta delimitada: o histórico e
o modelo da release podem ser usados pelo SDK MLflow **sem seus caminhos originais**?
O archive já havia provado integridade e recuperação de bytes; esse ensaio acrescenta
consulta e inferência. Não sobe servidor, migra backend, treina ou avalia nova janela.

### Entradas, contenção e paridade

`scripts.storage.mlflow_recovery` verifica os dois conjuntos em
`data/storage/restored/`: snapshot MLflow e release de serving. A referência externa
é `references/evidence/laboratory_serving_native_validation_2026-10-09.json`, já
versionada antes desse ensaio. Seu hash fixa o manifesto da release; desse manifesto
vêm a URI MLflow e o ambiente. Identidade do modelo e entrada/score são conciliados
com o controle HTTP histórico antes de qualquer desserialização.

O ensaio copia o snapshot para `data/storage/rehearsals/mlflow-recovery-*`.
Adapta apenas `artifact_location` de experiments/logged_models e `artifact_uri` de
runs. Registra o mapa completo e compara todas as células de todas as tabelas com
a transformação declarada. Default sem artefatos recebe destino vazio gerenciado.
URIs do bundle, restore e bancos originais permanecem preservadas.

Um subprocesso consulta os dois stores pela API MLflow: conjuntos de runs,
experimentos, status/ciclo de vida, parâmetros, tags, métricas recentes, histórico
completo de métricas e vínculos dos logged models. Confere novamente as tabelas
após as leituras. Resolve **um modelo**, identificado pela URI da release,
verifica seus bytes contra o hash externo e usa a revisão de tipos skops já adotada.
A inferência usa o smoke fixado; tolerância absoluta `1e-12`, relativa zero.
Esse smoke confirma identidade/paridade, não a qualidade de todos os modelos do store.

Caches, downloads e temporários do worker ficam na cópia gerenciada; telemetria
MLflow é desativada. Hooks de auditoria Python bloqueiam abertura dos dados do
projeto fora da cópia, conexão a outros SQLite, escrita fora do ensaio, conexões
sockets e processos adicionais. Operações opcionais de bibliotecas podem ser
bloqueadas e tratadas internamente; o relatório conta essas tentativas. Tentativa
de acessar dados proibidos impede sucesso. Esses hooks não constituem sandbox de
sistema operacional nem garantia contra bibliotecas nativas maliciosas.

Após o worker, artefatos e metadados arquivados são conferidos novamente, assim
como ambos os restores. A cópia temporária é removida. Sucesso produz recibo com
permissão `0600` em `data/storage/receipts/mlflow-recovery-*.json`, mapa de URIs,
identidades, contagens e score. Erro não produz recibo de sucesso. O recibo inclui
paths absolutos no mapa: conservar localmente; compartilhar apenas o resumo impresso.

### Comando do ensaio nativo

Usar o ambiente bloqueado pelo Poetry, Python 3.14.4/MLflow 3.17.0 e as versões
fixadas da release. O comando não usa Docker, RustFS ou rede: lê os **restores já
aprovados**, sem novo build/publicação/restore. Não aponta o SDK para o banco original.
Há cópia temporária de aproximadamente 256 MB mais downloads do modelo.

```bash
poetry run python -m scripts.storage.mlflow_recovery \
  --bundle-id c9c4e6e0f684ba21484e7491c026a24a76002570c251c97c98b244f043a4d924 \
  --release-bundle-id a33bb2fbb3e9503b4bb96c92dd6950677127b77fbf5bedb4322007587176b154
```

Critério do ensaio, **observado no relato nativo abaixo**: `status=success`,
71 runs e 67 logged models consultados entre os dois stores; 321 registros de
histórico de métricas; modelo `9728f8dddc4daa9e5478db1a8a173adecb71e41c5992965c117d2b46a27625d9`,
score `0.4178630794049542` dentro de `1e-12`; verificações de histórico, recarga,
preservação dos restores e limpeza aprovadas. `operational_tracking_restore_validated`
refere-se apenas a esse acesso local via SDK em cópia SQLite. Não representa
servidor HTTP, PostgreSQL, cutover, reload de todos os modelos ou migração primária.

Se o comando parar, preservar o estado e enviar a saída. Não refazer snapshot,
substituir o restore nem adaptar bancos históricos para fazer o teste passar.
[Evidência de preparação](../../references/evidence/mlflow_recovery_preparation_2026-10-16.json).

### Compatibilidade do smoke com a assinatura histórica — correção de 2026-10-16

O autor aprovou os 14 testes pelo tox, mas a tentativa nativa parou na validação
MLflow: `CUSTOMER_TX_COUNT_1D` chegou como `int64`, enquanto a assinatura arquivada
exige `double` para os 19 preditores. A fixture original inferia tipos mistos a
partir do pedido HTTP e não reproduzia essa fronteira. Naquela tentativa, a recuperação
ficou pendente e a falha não gerou recibo de sucesso. A retentativa posterior
foi aprovada e está registrada abaixo.

O worker agora adapta somente as colunas inteiras cujo destino declarado é
`double`, verificando representação exata valor a valor antes de `predict`.
Uma contagem como `2**53 + 1`, que seria arredondada em float64, é recusada.
A assinatura original, o pedido HTTP tipado, os valores do smoke, o modelo e a
tolerância `1e-12` são preservados. O relatório acrescenta
`signature_input_adaptations`, com coluna e tipos de origem/destino, sem valores.

A regressão exercita recarga real via pyfunc com URIs `runs:/`/`models:/` tanto
com assinatura mista quanto com todas as entradas `double`. Também confere limites
de representação e preservação do frame original. Não altera treinamento,
contratos Gold/HTTP, artefatos ou snapshot; não exige build ou nova transferência.
Após aplicar a correção e passar o tox, o autor repetiu o mesmo comando de
recuperação e informou o resultado de sucesso abaixo.
[Evidência da correção](../../references/evidence/mlflow_recovery_signature_fix_preparation_2026-10-16.json).

## Recuperação MLflow validada pelo autor — 2026-10-16

O autor informou **22 testes aprovados, 21 avisos em 23,72 s**, tox `py314` OK em
29,18 s. A retentativa corrigida retornou `status=success`. Este registro concilia
saída de terminal; o assistente não recebeu o JSON integral do recibo, o mapa de
URIs, os bancos ou bytes do modelo nativo para recalcular hashes.

| Verificação | Resultado informado |
| --- | --- |
| Store principal | 70 runs, 66 logged models e 315 registros de histórico de métricas |
| Store de interface | 1 run, 1 logged model e 6 registros de histórico de métricas |
| Histórico pela API | 71 runs, 67 logged models, 321 registros; `full_API_history_verified=true` |
| Referência recarregada | `runs:/d3be86000fd24dc8a053dbcab778d4b6/model`; hash `9728f8dddc4daa9e5478db1a8a173adecb71e41c5992965c117d2b46a27625d9` |
| Inferência de controle | Score esperado/observado `0.4178630794049542`; erro absoluto zero, tolerância `1e-12` |
| URIs | 145 células remapeadas somente na cópia descartável |
| Assinatura | 12 colunas inteiras convertidas exatamente para `float64`, conforme o destino `double` |
| Preservação | Tabelas após leituras e os dois restores preservados; cópia descartável removida; fonte original não lida |
| Controles de I/O | Zero violações de dados; zero conexões de rede e processos adicionais permitidos; 1 operação de rede e 3 de processo bloqueadas |

A contagem de tentativas bloqueadas não identifica, neste resumo, a biblioteca
responsável. Ela descreve ações impedidas pelos hooks Python; estes continuam sem
constituir sandbox de sistema operacional. O ensaio carregou **um modelo** para o
controle de inferência; os 67 logged models foram consultados como metadados.

Recibo local: `data/storage/receipts/mlflow-recovery-icz1xazs.json`.
Digest do mapa **informado pelo autor**, sem recálculo pelo assistente:
`e228ff6eb48e15890660d885ecae5467010e4d02816565b8202f4072b557d4cc`.
[Evidência documental](../../references/evidence/mlflow_recovery_author_validation_2026-10-16.json).

**Gate de acesso local via SDK, histórico e recarga da referência fechado.**
O escopo é cópia SQLite descartável dos restores existentes, sem S3 nesta chamada,
servidor HTTP, PostgreSQL ou cutover. Não houve refit, nova confirmação de qualidade,
promoção ou migração do armazenamento primário. Inventário global, adoção pelos
consumidores, cópia fora da máquina, rollback e limpeza permanecem abertos.
Este fechamento é documental; não requer repetir teste, build ou recuperação.


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

### Adoção HTTP pelo exemplo de contribuição — preparo de 2026-10-17

O gate de persistência do laboratório continua aprovado. Esta entrega liga apenas
`examples.model_contribution` ao serviço por `--tracking-uri`; os demais runners
conservam o destino local. `log_candidate` aceita exatamente um root local ou URI
HTTP e exige artefatos `mlflow-artifacts:/...` no modo HTTP. O servidor decide o
destino; o cliente não fornece `artifact_location` local.

O modelo passa pelo formato/loader/assinatura revisados e paridade existentes.
O experimento novo é `fraud-model-interface-smoke-http-v1`, com tags de integração
sintética e `not_promoted`. URI/limites de requisição são restaurados ao sair;
nenhuma falha provoca fallback automático. O retorno local é uma nova execução
explícita, sem remoção de runs/artefatos remotos.

[Comandos, recibo e aceite nativo](../modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).
A validação no assistente usa servidor HTTP e SQLite/arquivos descartáveis.
O autor aprovou depois a recarga deste modelo e o retorno local no serviço nativo,
conforme o fechamento abaixo; essa prova é distinta do ensaio anterior com JSON.

### Download por chunks e capacidades S3 — correção de 2026-10-17

O autor aprovou 79 testes de regressão (tox `py314`), mas `tracking-contribution`
falhou em 67,30 s durante a recarga interna da CLI. Run
`5d865db80aa64100be02179d0158e761`, experimento 3, modelo
`m-ba7a41359c2242c58eec7a080528445f`. Os cinco arquivos foram listados, mas cada
um falhou em um chunk. Os avisos de remoção referem-se aos arquivos parciais em
`/tmp/fraud-mlflow-check-*`; não são comprovantes de exclusão de objetos no RustFS.
Recibo: `data/storage/tracking-lab/receipts/contribution-30de2840b5824d039bf642699582dc13.json`.

No código instalado do MLflow 3.17.0, `MlflowArtifactsRepository` consulta
`/api/3.0/mlflow/server-info` quando a variável multipart não está explicitamente
definida. O servidor com S3 anuncia suporte e o cliente pede URLs assinadas, mesmo
para arquivos pequenos nesse caminho da versão fixada. O endpoint S3 configurado
é `http://rustfs:9000`, próprio da rede Docker. Que essa seja a origem da falha
nativa é uma inferência consistente com código/configuração; as URLs assinadas
nativas e o erro de transporte de cada chunk não foram inspecionados.

A política HTTP do projeto é cliente → MLflow → object store. Para garanti-la,
o contexto agora fixa ambas as variáveis `MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD`
e `MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD` em `false`, restaurando seus valores
anteriores ao sair. Isso usa configuração suportada da versão fixada, sem patch
no SDK, acesso S3 direto no cliente, troca de endpoint, incremento de timeout,
rebuild do servidor ou alteração das verificações de modelo.

Um gateway local de teste anuncia as capacidades de S3 e devolve URLs diretas
inacessíveis. O SDK real reproduziu o erro dos cinco chunks antes da correção.
Depois, a CLI recarrega com paridade e o gateway observa GET do `model.skops` pelo
proxy, sem pedidos de URL assinada. O backend desse fixture continua SQLite com
arquivos descartáveis; a retentativa PostgreSQL/RustFS posterior foi aprovada
pelo autor, conforme o fechamento a seguir.

O probe de persistência anterior rodava dentro da rede Docker. Seu campo
`proxy_verified` verifica acesso pela URI MLflow; não distinguia streaming pelo
servidor de download delegado por URL assinada. A prova de persistência/recuperação
dos registros continua válida no escopo relatado, mas não é tratada como prova
prévia de que todos os bytes atravessavam o servidor MLflow.
[Evidência e retentativa](../../references/evidence/model_contribution_proxy_fix_2026-10-17.json).

### Contribuição HTTP aprovada; próximo produtor: HGB — 2026-10-17

O autor aprovou a retentativa: **34 testes de regressão e 1 teste nativo**.
O modelo foi recarregado pelo serviço sem a saída local disponível, com erro
máximo zero. O retorno explícito ao tracking local descartável e a preservação
dos containers foram conferidos. A criação de tabelas no log pertence a esse
retorno local deliberado; não indica fallback silencioso do HTTP.
[Evidência do autor](../../references/evidence/model_contribution_http_author_validation_2026-10-17.json).

A próxima integração prioriza o **HGB**, começando por um fold sintético com
identidade de destino, recarga independente e reutilização sem fit/publicação.
O estudo de 20 trials/63 tentativas e a decisão `retain_reference` ficam
preservados. O fold HGB foi aprovado pelo autor; o estudo completo não será
reexecutado para testar transporte. [Procedimento e limites](HGB_TRACKING.md).

### Baselines: três runs pelo serviço HTTP

O autor aprovou o fold HGB do runner Optuna: recarga independente com erro zero
e reutilização sem fit/publicação. [Evidência](../../references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).
Esse runner não treina o trio inicial de baselines.

O trio recebe agora uma opção HTTP no runner `baseline_experiment.py`, com URI
explícita, três runs principais e recarga skops antes da publicação local. O
experimento normal é `fraud-temporal-baseline-http-v1`; o teste nativo usa
`fraud-baseline-smoke-http-v1` com fixtures sintéticos. As saídas HTTP e os recibos
ficam sob `data/storage/tracking-lab`, mantendo os IDs remotos fora da varredura
do store histórico local. O aceite nativo do trio foi aprovado pelo autor em 2026-10-17.
[Comandos, falhas parciais e limites](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Fechamento da adoção do trio — 2026-10-17

O autor aprovou as três runs dummy/regressão/HGB no experimento 5, com recarga
independente dos modelos, arquivos locais indisponíveis e erro zero em todos.
Pytest: 1 aprovado em 6,55 s; tox `tracking-baseline` OK. Não foi criado tracking
SQLite, e os containers permaneceram iguais.
[Evidência](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).

Contribuição, fold Optuna HGB e trio de baselines têm agora aceites distintos.
Isso valida a rota de novas execuções opt-in pelo serviço. Não muda o padrão
local, migra histórico ou comprova recuperação do PostgreSQL. O próximo
planejamento deve mapear os consumidores locais e delimitar restauração isolada
de metadados/artefatos antes de cutover; não ampliar adoção primária ou limpar
origens apoiando-se apenas no sucesso da recarga.

### Próxima fronteira: revisão dos consumidores e recuperação isolada

O modo `scripts.storage.persistence_inventory --tracking-consumers` verifica a
revisão de 17 registros, com sete fronteiras locais. Freeze, ownership, hashes,
ledger e vinculação dos recibos impedem uma simples troca global de URI.
Os produtores HTTP aprovados não tornam esses consumidores compatíveis por si.

A revisão preserva todos os contratos científicos e os scripts de backup
históricos. O protocolo implementado usa dump PostgreSQL e manifest fechado de objetos S3,
com escritores pausados na captura e restore num alvo próprio. O aceite nativo
está registrado abaixo; este fechamento documental não executa comandos na bancada. [Comando, mapa e protocolo](TRACKING_RECOVERY.md).

**Recuperação da bancada aprovada pelo autor (2026-10-19):** PostgreSQL e 38
objetos S3 restaurados em volumes/rede próprios; 8 runs, 6 modelos e 44 pontos
históricos conferidos pela API. O modelo fixado recarregou pelo proxy e reproduziu
20 scores com erro máximo zero. Fonte/captura preservadas, alvo e credenciais
temporários removidos; pytest live passou em 20,89 s. **Recorte de inventário e
recuperação encerrado.** Próxima prioridade: CD/rollback do serving, seguido de
monitoramento de dados/features. Migração dos sete consumidores históricos,
cutover, limpeza e inventário global seguem em backlog próprio.
[Comandos, escopo e limites](TRACKING_RECOVERY.md).

## MLflow no KinD — aceite nativo de 2026-10-20

MLflow instalado via Helm sobre PostgreSQL/RustFS dedicados. O autor aprovou
publicação, recarga independente HTTP, comparação dos bytes do modelo no S3 e
recarga após recriar o pod MLflow, com erro zero e fundação preservada. O gate
nativo passou em 21,56 s. Isso demonstra persistência e integração operacional
com uma fixture sintética; a migração histórica e o backup/restore deste novo
destino continuam pendentes. Próximo recorte: objetos/dados no RustFS do KinD,
com aceites próprios. [Resultado e evidência](KIND_TRACKING.md#aceite-nativo--2026-10-20).
