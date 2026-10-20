# Organização e dependências

<!-- kind-foundation-acceptance-2026-10-20:start -->
**Estado em 2026-10-20 — fundação KinD/Helm aprovada.** PostgreSQL e RustFS
prontos; marcadores SQL/S3 preservados após recriar os pods, com os mesmos PVCs.
Regressão nativa: 45 testes aprovados. Bootstrap corrigido após ajuste de
inotify no host; persistência desse ajuste após reboot ainda não confirmada.
A fundação foi integrada ao MLflow; o aceite e o próximo recorte estão registrados abaixo.
[Aceite, pré-requisito e limites](operations/KIND_FOUNDATION_ACCEPTANCE.md). As notas anteriores mantêm seu contexto histórico.
<!-- kind-foundation-acceptance-2026-10-20:end -->


A estrutura acompanha responsabilidades existentes. O pacote permanece na raiz
do checkout. O serving tem runtime e recursos operacionais explícitos;
infraestrutura entra por capacidade implementada.

## Persistência e armazenamento

O [plano de armazenamento](operations/STORAGE.md) define responsabilidades e
estado de implementação. Bronze/Silver/Gold e artefatos publicados terão cópias
versionadas em RustFS via S3, com manifestos/checksums e restauração demonstrada.
DuckDB permanece motor analítico sobre Parquet. SQLite continua no estado ativo
do replay; MLflow ainda usa SQLite, com **PostgreSQL escolhido como destino**
do novo servidor e migração própria antes da troca. Prometheus usa
TSDB em volume próprio; Grafana usa provisioning versionado e volume para estado.
Uma feature store eventual reutiliza o offline e escolhe online conforme o spike.

O inventário e piloto S3 limitado estão em `scripts/storage/archive.py`, fora dos
scripts identificados do replay. O cliente AWS CLI usa perfil Compose próprio;
a suíte padrão usa S3 simulado. O autor validou upload/reenvio/restauração real
de documentos e release no RustFS. O modelo restaurado foi recarregado com
identidade preservada e erro zero no score histórico, via TestClient em processo.
A migração ampla ainda não foi verificada. `scripts/storage/bronze_archive.py`
prepara um snapshot Bronze completo com inventário arquivado e validação offline
antes de publicar localmente; o autor aprovou 183 partições e 188 arquivos.
O perfil próprio preserva o piloto. `scripts/storage/bronze_transfer.py` delega
cada transferência a um job `storage-transfer` com cliente boto3 reutilizado,
dependências operacionais fixadas por hashes e acesso apenas a `data/storage/`.
A restauração passa pelo verificador Bronze no host antes de receber seu caminho
final. O autor validou o build e o round-trip nativo: 189 objetos, reenvio sem
uploads e 183 partições recuperadas/verificadas com o inventário baixado.
Os consumidores continuam lendo a materialização local. O novo
`scripts/storage/silver_archive.py` prepara Silver com inventário, contrato e
suas auditorias em `runs/`; usa o mesmo worker S3 com perfil explícito Silver.
No restore, aplica `verify_silver` existente sobre a cópia: integridade, schema
Arrow, cobertura e contagens SQL. O autor aprovou preparo e round-trip Silver:
188 objetos, reenvio sem uploads e 183 partições/1.754.155 linhas recuperadas
com contrato/inventário baixados. [Evidência Silver](../references/evidence/silver_archive_author_validation_2026-10-15.json).
Gold de desenvolvimento/estudos e tracking seguem em etapas próprias. [Evidência](../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
Arquivos locais, volumes e cópias S3
precisam de inventário e recuperação, e um único laptop não fornece independência
física das cópias. KinD + Helm é o destino final escolhido, após restauração e
rollback; metas condicionais como broker/feature store não bloqueiam essa implantação.

`scripts/storage/gold_archive.py` trata somente `gold_v1` e suas auditorias,
incluindo inventário, contratos Silver/Gold e protocolo temporal. Reutiliza o
staging e worker S3 (perfil Gold/prefixo próprio); o host aplica `verify_gold`
congelado na cópia com essas referências. Schema, invariantes das features,
datas, IDs e split counts são conferidos sem recomputar features ou acessar
Silver/Bronze/modelos. O autor aprovou o ciclo nativo: 48 arquivos/25.196.448 bytes,
49 objetos, reenvio sem uploads e restore verificado com 42 partições, 19 features
e 402.877 linhas, usando referências baixadas. Consumidores ainda leem paths locais.
[Evidência Gold](../references/evidence/gold_archive_author_validation_2026-10-15.json).
Estudos Optuna, modelos e avaliações têm cobertura e verificação próprias.
[Detalhes](operations/STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo).

`scripts/storage/development_gold_archive.py` seleciona um run explícito de
`development_gold_v2`: manifesto e `transactions/`, inventário e protocolo HGB.
O perfil tem prefixo `development-gold/v1/<bundle_id>/`, usa o mesmo worker e
`verify_data` congelado com a política arquivada. Preserva run ID, manifesto e
proveniência de implementação/Silver sem reescrever caminhos históricos; não
consulta estudo/SQLite/modelos ou dados upstream. O autor aprovou preparo e
round-trip: 66 arquivos/38.202.741 bytes, 67 objetos, reenvio sem uploads e restore
com 63 partições/19 features/603.669 linhas. Política/inventário vieram da cópia
baixada; identidade do manifesto foi preservada. Leitores continuam locais.
[Evidência nativa](../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
Estudo e dataset são unidades de recuperação distintas, ligadas pelo
run ID e hash do manifesto. O preparo offline do backup do estudo está em
`scripts/storage/hgb_study_backup.py`: reutiliza o lock real do escritor, abre a
fonte SQLite em modo somente leitura e usa Online Backup API; confere integridade,
foreign keys e digest do dump lógico. Modelos, predições, tentativas e relatórios
acompanham o snapshot; WAL/SHM/journal/lock não são copiados como payload.
`verify_hgb_optimization` existente reconcilia trials e métricas em uma cópia
descartável com dataset arquivado. Somente `study_path` do relatório dessa cópia
é adaptado; banco/relatório arquivados e referências de tracking permanecem intactos.
O autor aprovou o preparo nativo: 322 arquivos/233.048.483 bytes, 20 trials/63 fits,
sem refit ou alteração de tracking. `scripts/storage/hgb_study_transfer.py`
reutiliza o worker em `fraud-artifacts/hgb-study/v1/<bundle_id>/`; datasets mantêm
bucket/prefixos/identidades. Restore exige ID explícito de dataset local verificado
antes do download e reaplica a verificação do estudo antes do destino final.
Ciclo S3 aprovado pelo autor em 2026-10-16: 323 objetos, reenvio sem uploads e
322 arquivos restaurados/verificados, 20 trials/63 tentativas e mesmo hash lógico.
O restore não leu a fonte original. Recuperação do estudo concluído demonstrada;
retomada de treinamento e migração de tracking mantêm critérios próprios.
[Evidência S3](../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
[Evidência nativa do preparo](../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
[Contrato](operations/STORAGE.md#backup-consistente-do-estudo-hgb-preparo-offline).
[Contrato e limites](operations/STORAGE.md#gold-de-desenvolvimento-dataset-e-estudo).

## Modelagem

| Local | Responsabilidade e fronteira |
| --- | --- |
| `modeling/contracts/model_interface.py` | `ModelSpec`, construção, fit e score com checks de compatibilidade |
| `modeling/contracts/prediction_schema.py` | Tipos de metadados e scores persistidos |
| `modeling/contracts/temporal_assessment_protocol.py` | Snapshot do protocolo estatístico e vínculos com recibos históricos; lê somente JSON versionado |
| `modeling/algorithms/<algoritmo>.py` | Factory completa e não ajustada; sem política temporal ou publicação |
| `modeling/algorithms/model_catalog.py` | Registro explícito e parâmetros padrão dos algoritmos |
| `modeling/experiments/candidate_training.py` | Entrada compartilhada de ajuste, métricas e artefatos |
| `modeling/experiments/baseline_policy.py`, `baseline_experiment.py` | Política fixa, verificação e execução da baseline |
| `modeling/experiments/temporal_development_data.py`, `hgb_optimization.py` | Preparação autorizada e busca de hiperparâmetros HGB |
| `modeling/experiments/terminal_feature_ablation.py` | Ablações específicas das features de terminal |
| `modeling/experiments/candidate_freeze.py`, `final_holdout_evaluation.py` | Congelamento e avaliação final autorizada |
| `modeling/experiments/reference_temporal_assessment.py` | Preparação do plano temporal da referência; somente metadados, sem execução reservada |
| `modeling/experiments/reference_assessment_execution.py` | Execução autorizada da janela fixada, auditoria de acesso, retomada e verificação offline |
| `modeling/experiments/experiment_provenance.py`, `experiment_artifacts.py` | Identidade de implementação e integridade compartilhadas |
| `evaluation/` | Ranking, comparação pareada, diagnósticos e referência condicional de fila aleatória sobre contagens diárias |
| `integrations/` | Tracking MLflow e persistência skops |

Dependências seguem essas fronteiras: algoritmos dependem do contrato; integrações
validam o contrato; métricas recebem rótulos, scores e metadados; experimentos
compõem essas capacidades e controlam datas, orçamento e gates. Algoritmos e
contratos não importam executores. O treino compartilhado não depende da política
da baseline. A avaliação final permanece em experimentos por consumir um holdout
sob uma autorização específica.

Uma contribuição nova entra por
[CONTRIBUTING_MODELS](modeling/CONTRIBUTING_MODELS.md). O catálogo não amplia as
políticas congeladas. Nomes de modelos, parâmetros nativos, classes, features,
schemas e formato skops permanecem iguais nesta mudança estrutural.

## Entradas e proveniência

Os adaptadores antigos de `modeling/` foram retirados após a migração do código,
exemplos e notebooks para os módulos explícitos da tabela. `modeling.experiments`
agrupa os executores; cada comando identifica seu módulo, incluindo
`modeling.experiments.terminal_feature_ablation`. O contrato `model_interface_v1`
permanece em `modeling/contracts/model_interface.py` com as mesmas regras.

Scripts externos devem usar os caminhos atuais. Os
[comandos e regras de migração](operations/OPERATIONS.md#migração-da-organização-da-modelagem)
mantêm o mapa das entradas aposentadas e distinguem leitura histórica de novas
execuções. Recibos históricos conservam os comandos efetivamente executados.

O inventário de código é recursivo, incluindo contratos, algoritmos, avaliação e
integrações. Novos ajustes exigem código e políticas commitados. Uma refatoração
muda a identidade da implementação; não reescrevemos manifestos, protocolos nem
recibos históricos para permitir retomada com código diferente.

## Dados e features

| Local | Responsabilidade e fronteira |
| --- | --- |
| `data/contracts/transaction_schema.py`, `silver_contract.py` | Tipos físicos, validação de inteiros e aceitação da Bronze |
| `data/contracts/gold_contract.py`, `temporal_protocol.py` | Regras da Gold e autorização das janelas temporais |
| `data/contracts/dataset_errors.py` | Erros compartilhados, sem dependência dos builders |
| `data/ingestion/handbook_inventory.py` | Identidade da fonte e seleção das partições |
| `data/ingestion/handbook_bronze.py`, `handbook_download.py` | Aquisição, integridade, retomada e CLI da Bronze |
| `data/datasets/silver_dataset.py`, `gold_dataset.py` | Construção, publicação atômica, verificação e consulta |
| `data/datasets/reference_assessment_dataset.py` | Contexto Silver restrito e features causais da avaliação de setembro; sem carga ou ajuste de modelo |
| `data/quality/transaction_profile.py`, `training_eda.py` | Diagnóstico dos dados brutos e exploração restrita ao treino |
| `features/feature_schema.py` | Ordem, tipos e schema dos preditores e metadados |
| `features/causal_history.py` | Fórmulas e janelas SQL sobre o passado elegível |
| `features/feature_validation.py` | Finitude e relações entre os valores calculados |

Schemas e contratos não dependem de builders. Silver compõe ingestão, perfil e
aceitação; Gold compõe Silver, protocolo e features. A qualidade compartilha o
schema físico, sem carregar o construtor da Gold. Modelagem importa o schema das
features; experimentos usam os loaders e verificadores de datasets quando necessário.
`artifacts.py` oferece escrita JSON atômica e hashes; `config.py` resolve a raiz
do checkout. Os caminhos de armazenamento permanecem iguais.

Os adaptadores antigos na raiz foram retirados; imports e comandos usam os módulos
da tabela. `features.py` foi substituído pelo pacote `features/`, que conserva sua
API pública de constantes e funções sem duplicar o cálculo. A
[migração de dados e features](operations/OPERATIONS.md#migração-de-dados-e-features)
explica os caminhos atuais, verificações históricas e identificação do código.

## Próximas fronteiras

`serving/request_schema.py` define o HTTP e reutiliza ordem, tipos e coerências
das features. `serving/model_release.py` verifica identidade, ambiente, tipos skops
e score de controle na inicialização. `serving/http_service.py` adapta esse runtime
ao FastAPI; não importa executores, tracking ou caminhos do checkout.

`integrations/serving_release_export.py` é o passo offline: verifica a avaliação
salva e os bytes congelados no MLflow, compara JSON/scores sobre a validação e
exporta os mesmos bytes. Não ajusta nem publica outro modelo. O recibo histórico
permanece igual; os hashes de código da época não são recalculados para aparentar
compatibilidade com a refatoração.

Poetry separa `main` (inferência), `pipeline` (dados/experimentos) e `dev` (checks).
A instalação padrão conserva todos os grupos. O wheel HTTP foi conferido em
ambiente só com `main`, fora do checkout. Isso não empacota os protocolos externos
dos workflows offline. Uma mudança para `src/` permanece decisão futura.
O autor validou o wheel com a referência real. `docker/serving/` define o build
em estágios; `scripts/serving/` contém a verificação operacional com a release
montada somente para leitura. O autor validou o build/run nativo com paridade
HTTP, recuperação e rejeição dos três cenários de carga inválida. O recibo registra
o ID da imagem, a identidade do modelo e medições de laboratório.
A verificação de serving permanece em scripts independentes. O incremento que
introduzia Prefect apenas para verificar release/serviço foi retirado: seu custo
operacional não se justifica para essa checagem isolada. O executor local está em
`streaming/replay/temporal_replay_executor.py`; contratos ficam em
`streaming/contracts/transaction_events.py` e o cálculo incremental em
`streaming/features/causal_feature_state.py`. O núcleo grava histórico, decisão e
checkpoint na mesma transação SQLite e reutiliza a pontuação de serving em processo
quando um runtime identificado é fornecido. A CLI demonstra somente fixtures
sintéticas; não lê Handbook ou a reserva. [Semântica e execução](operations/CAUSAL_REPLAY.md). Uma futura camada de orquestração deve coordenar processos
com dependências e recorrência demonstradas, preservando a lógica independente.
`streaming/contracts/customer_day_alerts.py` identifica o limiar, orçamento e
release; `streaming/decisions/customer_day_alert_policy.py` recebe somente transação
e score e aplica a regra causal de cliente/dia em memória. A função pura de
decisão é compartilhada com `streaming/replay/durable_triage_replay.py`, que grava
score, decisão, alerta e checkpoint em uma transação SQLite. A política não importa
loaders, SQLite, treino ou transporte; o executor recebe feedback separado. O schema
durável tem identidade e caminhos próprios, sem migrar o estado anterior.
`data/datasets/native_replay_dataset.py` restringe as partições e materializa eventos
ordenados com rótulos somente no feedback. `streaming/replay/native_causal_replay.py`
compõe artefatos identificados, oracle Gold independente e executor durável;
`streaming/evaluation/native_triage_assessment.py` confere paridade e avalia os
alertas efetivamente gravados. O código está conferido com fixtures; resultados
nativos e publicador externo ainda estão pendentes. O protocolo executável separado
preserva os bytes dos protocolos e checkpoints anteriores.
O [protocolo operacional](operations/CAUSAL_REPLAY.md#protocolo-antes-do-acesso-nativo)
define seleção do limiar e critérios de comparação antes do acesso à reserva.
`streaming/preparation/development_alert_threshold.py` compõe release congelada,
seleção de capacidade e publicação/verificação do limiar; não ajusta modelos.
`data/datasets/development_scoring_dataset.py` projeta somente features e identidades
nas 21 partições de desenvolvimento. `streaming/decisions/development_threshold_selection.py`
aplica a mediana dos cortes diários sem receber alvos. A Gold e sua proveniência
histórica permanecem preservadas; o verificador confere o resultado salvo, sem
abrir datasets ou carregar o modelo.
O [mural](project/ROADMAP.md) define essa ordem.

## Documentação e testes

`docs/project/`, `data/`, `modeling/` e `operations/` têm um
[índice](README.md). README resume; arquitetura explica dependências; contratos
definem regras; runbooks mantêm procedimentos. O contrato HTTP e sua execução ficam
em [SERVING_CONTRACT](operations/SERVING_CONTRACT.md).

Os testes acompanham as áreas do pacote. `tests/conftest.py` fornece fontes
sintéticas compartilhadas; `tests/experiment_fixtures.py` contém auxiliares de
comparação. Arquivos de teste não importam outros arquivos de teste. Inicialização
e opções das CLIs atuais são conferidas em `tests/modeling/test_entry_points.py` e
`tests/data/test_entry_points.py`; a API pública de features continua coberta em sua área.
Checks e limites ficam em [Testing](operations/TESTING.md).

A CLI de plano conserva seu escopo de metadados. O executor de setembro aplica
outro snapshot de autorização, verifica o modelo antes das partições e persiste
resultados sem novas runs. `evaluation/temporal_reference_metrics.py` recebe scores
e metadados; não lê dados nem carrega modelos. [Execução e limites](modeling/EVALUATION_PROTOCOL.md#executor-auditável-da-janela-fixada).

## Correção numérica e evidência operacional

`features/exact_binary_mean.py` define `exact_binary64_mean_v1`;
`features/deterministic_causal_history.py` calcula o oráculo com janelas DuckDB
independentes. `streaming/features/deterministic_feature_state.py` aplica a mesma
regra numérica às consultas SQLite, mantendo as fronteiras causais existentes.
`streaming/replay/numerical_corrective_replay.py` vincula os hashes originais,
prepara oráculo próprio sobre eventos materializados e mantém checkpoint separado.
A identidade da execução inclui a regra e a proveniência do código.

O autor executou e verificou o replay corretivo de 16–30/09: os 19 atributos
coincidem exatamente e o gate de scores foi aprovado, sem mudar os 1.453 alertas.
Os cálculos/executores e protocolos anteriores não foram reescritos. A atualização
documental posterior acrescenta evidência, preservando os bytes de código e dos
contratos usados na execução. [Runbook e limites](operations/NUMERICAL_CORRECTIVE_REPLAY.md).
O relato prova o ensaio acelerado local; transporte, pacing e monitoramento
continuam como fronteiras operacionais pendentes.

## Replay programado e observabilidade local — preparação de 2026-10-15

`scripts/replay/` envolve o núcleo congelado; `scripts/monitoring/` mantém journal,
coleta read-only e HTTP agregado sem dependência de ML no exporter.
`infra/observability/` provisiona Prometheus, Grafana e perfil RustFS opcional.
Os scripts operacionais e seu protocolo têm identidade própria, exigida antes do
ensaio nativo. O pacote original, dependências, protocolos e evidências históricas
não mudam; o verificador corretivo continua usando suas identidades congeladas.

O produtor programa eventos com relógio monotônico e aplica backpressure numa fila
limitada; o consumidor confirma decisões pelo executor durável existente. O
checkpoint governa retomada. Telemetria separada reconhece o intervalo entre
commit de decisão e registro de tempo, marcando lacunas sem fabricar duração.
O exporter não monta oráculo/Silver/release e não pontua. As métricas individuais
não saem do ledger: HTTP expõe agregados com cardinalidade limitada.

Prometheus conserva séries locais por 48 h/512 MB; Grafana provisiona operação e
qualidade madura/scores. RustFS não é backend do SQLite ativo: seu próximo marco
é arquivamento de artefatos e backups consistentes com restauração verificada.
[Contrato de métricas e execução](operations/OBSERVABILITY.md).


## Inventário do tracking antes da migração

`scripts/storage/mlflow_inventory.py` é um CLI operacional stdlib: lê banco/WAL
como arquivos, consulta uma cópia temporária sem inicializar MLflow, inventaria
artefatos e referências JSON e grava relatório novo em data/storage/inventory.
Não modifica a integração científica atual. A configuração atual força SQLite
e alguns consumidores exigem mlflow.db; a futura integração HTTP deve cobrir
essas fronteiras. O autor aprovou a coleta corrigida: 70 runs/66 logged models,
627 arquivos/253.128.963 bytes, zero referências não resolvidas ou localizações
gerenciadas ausentes. O Default tem zero runs/modelos e diretório ausente;
preservar sua linha/URI. Inventário revisado; backup e migração ainda não implementados.
[Evidência nativa](../references/evidence/mlflow_inventory_author_validation_2026-10-16.json).
[Cobertura e critérios de migração](operations/MLFLOW.md#inventário-antes-da-migração-postgresql--s3).


## Snapshot offline do tracking MLflow

`scripts/storage/mlflow_backup.py` reutiliza o inventário e helpers stdlib de
arquivo/path/hash, com perfil local próprio de 1.024 arquivos/256 MiB.
O transporte S3 compartilhado agora aceita esse perfil, sem ampliar os anteriores. Banco/WAL são copiados com fonte quiescente para
staging gerenciado e normalizados via Online Backup API na cópia. Todos os
artefatos/auxiliares regulares e diretórios do tracking têm manifesto e verificação;
colisão, banco fora dos stores principal/interface, mudança da fonte ou cobertura URI incompleta impedem
publicação. Inventário pode receber diretório temporário explícito para esse uso;
a CLI anterior mantém comportamento. Código científico permanece preservado.
Preparo/verify e ciclo S3 nativos aprovados para o snapshot dos dois stores;
recarga operacional/migração ainda abertas. [Contrato](operations/MLFLOW.md#backup-offline-do-tracking-preparo-e-verificação).


O preparo nativo detectou o segundo store documentado em
`data/tracking/interface-smoke`: 1 run concluída, 1 logged model, 6 arquivos/32.088
bytes, sem localizações gerenciadas ausentes. A correção inclui os dois stores
com bancos/WAL e artefatos conferidos independentemente e limites agregados,
preservando a separação do smoke sintético. Bancos desconhecidos continuam
bloqueados. Retentativa de preparo e verificação nativos aprovadas para os dois stores;
publicação, reenvio idempotente e recuperação S3 aprovados pelo autor. Migração,
recarga operacional e limpeza permanecem abertas. [Cobertura e comando](operations/MLFLOW.md#cobertura-dos-dois-stores-mlflow--correção-de-2026-10-16).
[Registro do diagnóstico e preparação](../references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).


O backup MLflow teve **preparo e verificação locais aprovados pelo autor**:
dois stores, 71 runs, 67 logged models, 633 artefatos; pacote de 640 arquivos e
256.354.588 bytes. O verificador conferiu os dois bancos e artefatos sem ler
originais. Tox nativo: 70 testes aprovados, um aviso, Ruff/formatação/lockfile
aprovados. [Registro nativo](../references/evidence/mlflow_backup_author_validation_2026-10-16.json). A transferência S3 agora está
implementada/testada com o job existente, incluindo restauração em diretório
separado e nova verificação por store; publicação/reenvio/restore nativos aprovados.
A recuperação sobre cópia descartável foi validada pelo autor em 2026-10-16; o resultado local via SDK está no runbook MLflow.
[Procedimento](operations/MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow).
O acesso local ao histórico e à referência pelo SDK foi validado depois desse
preparo. Migração PostgreSQL, adoção pelos consumidores e inventário/limpeza
globais mantêm critérios próprios. Código científico, contratos,
evidências e URIs históricas permanecem preservados.


**Ciclo S3 do snapshot MLflow aprovado pelo autor em 2026-10-16:** publicação com
641 objetos enviados/verificados, reenvio com zero uploads/641 objetos reutilizados
e restore em diretório separado com 640 arquivos/256.354.588 bytes. Os dois stores
mantiveram 71 runs, 67 logged models, 633 artefatos e os hashes SQLite/lógicos;
integridade/FKs/tabelas/URIs conferidas, sem leitura dos originais pelo verificador.
16 testes nativos passaram no tox. [Evidência nativa](../references/evidence/mlflow_transfer_author_validation_2026-10-16.json).
Gate de acesso ao histórico e recarga do modelo concluído em cópia descartável,
com paths históricos mapeados somente nessa cópia e sem depender dos originais.
O autor validou esse uso local pelo SDK em 2026-10-16.
O snapshot conferido permanece preservado; PostgreSQL, adoção pelos leitores,
rollback, inventário global e limpeza continuam abertos.
[Resultado e próximo critério](operations/MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16).

### Recuperação do tracking antes da conversão do backend

A prova de uso acrescenta um processo local, sem novo serviço ou dependência.
`mlflow_recovery` coordena verificação/cópia/mapeamento/limpeza; o worker usa o SDK
e a revisão skops existente. Apenas a cópia descartável recebe URIs adaptadas.
Testado com fixtures e validado pelo autor em cópia descartável. [Fronteiras e procedimento](operations/MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).

**Recuperação local MLflow aprovada pelo autor em 2026-10-16:** os dois stores
forneceram 71 runs, 67 logged models e 321 registros de histórico. A referência
recarregada reproduziu o score com erro zero; restores e tabelas foram preservados
e a cópia descartável removida. O gate fecha acesso local pelo SDK e um smoke de
inferência; serviço HTTP/PostgreSQL, migração primária e inventário global mantêm
critérios próprios. [Resultado e evidência](operations/MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

### Inventário sem alterar os produtores congelados

`persistence_inventory` lê catálogo/Git e metadados do checkout, incluindo o workspace
de archives/restores antes omitido. O módulo stdlib separado preserva os fingerprints
atuais de backup/transporte e não acrescenta serviço ou dependência. O catálogo é
uma revisão manual de produtores conhecidos, não um analisador de código ou política
de escrita imposta. [Fronteiras e critérios](operations/STORAGE.md#inventário-do-checkout-e-catálogo-de-produtores).


O inventário do checkout foi executado pelo autor: 4.990 arquivos, sem falhas de
coleta; o marcador vazio de `data/external/` foi revisado e os mounts principais
identificados. A primeira contenção exige áreas de `data/` em quatro CLIs de
exemplos, recibo Docker e inicialização de observabilidade. Não cobre todas as
APIs/CLIs nem migra consumidores. [Destinos, comandos e limites](operations/STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).


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

[Operação e próximos critérios](../infra/tracking/README.md).
[Evidência relatada e limites](../references/evidence/tracking_lab_author_validation_2026-10-17.json).

### Roteamento explícito de um produtor de modelos

A integração `integrations/mlflow_tracking.py` mantém o backend local anterior e
acrescenta contexto HTTP serial com restauração da URI e dos limites de requisição.
Somente o exemplo de contribuição expõe a opção HTTP nesta entrega. Experimentos
HTTP herdam a localização de artefatos do servidor; destinos locais/S3 diretos são
recusados nesse modo. A recarga usa o verificador skops e a assinatura existentes.
Não há fallback automático, adapter de storage novo ou alteração dos outros runners.
[Contrato do produtor](modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

### Tracking HGB: estado de busca e registro de modelos

A contribuição HTTP teve recarga/paridade e retorno local aprovados pelo autor.
O próximo incremento é o HGB: metadados MLflow pelo serviço PostgreSQL e modelos
pelo proxy para RustFS, com endpoint/experimento vinculados à identidade de novos
estudos. `study.db` continua sendo o estado local Optuna com escritor único.
O aceite nativo proposto é de um fold sintético, sem reabrir o estudo encerrado.
[Contrato operacional e fronteiras](operations/HGB_TRACKING.md).

### Trio de baselines no serviço de tracking

O runner da comparação inicial de dummy/regressão/HGB passa a aceitar HTTP
explicitamente. Ele é separado do runner Optuna, cujo fold nativo foi aprovado.
O serviço concentra metadados PostgreSQL e artefatos RustFS via proxy; manifests
e audits dos novos experimentos HTTP ficam em `data/storage/tracking-lab`.
Os destinos e formatos locais antigos continuam disponíveis. Não há migração de
runs ou alteração dos consumidores históricos nesta etapa.
[Contrato e teste do trio](modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Aceite dos três produtores de baseline

O trio dummy/regressão/HGB foi aprovado no serviço nativo com recarga independente
sem arquivos locais, erro zero e containers preservados. Contribuição e fold do
runner Optuna têm seus aceites separados. As rotas HTTP são opt-in; consumidores
históricos e padrão local permanecem. A arquitetura não foi alterada por este
fechamento. [Evidência](../references/evidence/baseline_tracking_author_validation_2026-10-17.json).

### Consumo local e recuperação como fronteiras separadas

A revisão de fontes identificou sete fronteiras de consumo local ainda ligadas
à identidade da referência congelada. O novo inventário fonte/catálogo é
explicitamente estático; não muda transporte, dados ou execução científica.
Serving já lê releases independentes do tracking e deve manter essa propriedade.
O restore usa alvo PostgreSQL/RustFS/MLflow temporário separado da fonte.
A implementação e o aceite nativo estão concluídos no recorte da bancada.
[Mapa e critérios](operations/TRACKING_RECOVERY.md).

**Recuperação da bancada aprovada pelo autor (2026-10-19):** PostgreSQL e 38
objetos S3 restaurados em volumes/rede próprios; 8 runs, 6 modelos e 44 pontos
históricos conferidos pela API. O modelo fixado recarregou pelo proxy e reproduziu
20 scores com erro máximo zero. Fonte/captura preservadas, alvo e credenciais
temporários removidos; pytest live passou em 20,89 s. **Recorte de inventário e
recuperação encerrado.** Próxima prioridade: CD/rollback do serving, seguido de
monitoramento de dados/features. Migração dos sete consumidores históricos,
cutover, limpeza e inventário global seguem em backlog próprio.
[Comandos, escopo e limites](operations/TRACKING_RECOVERY.md).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte. O build e a exportação da entrega
versionada também foram aprovados nativamente; o consumo do arquivo em outro
alvo tem aceite nativo informado pelo autor; CI remota aprovada no aceite abaixo. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](operations/SERVING_ROLLOUT.md).

A bancada usa backends privados sem portas publicadas e NGINX conectado à rede
interna e à bridge de entrada com porta loopback. A decisão para o portfolio é
manter blue-green: uma réplica ativa, candidata temporária e retenção anterior
por uma janela de rollback a definir. Essa política é planejada; o ensaio atual
remove A, B e gateway ao terminar. O provisionamento Helm e o fluxo permanente
ainda serão implementados. [Decisão](operations/SERVING_ROLLOUT.md#estratégia-escolhida-para-o-portfolio).

### Imagem de serving vinculada ao commit

A CI ganhou um job após `quality` que constrói a imagem uma vez, qualifica HTTP e
rollback com release **sintética** e exporta essa mesma imagem com hashes e recibos
vinculados ao commit. Sem dados históricos, publicação em registry ou deploy no
laptop. **Build, gates e exportação aprovados nativamente pelo autor em
2026-10-19:** uma imagem do commit `0b95136`, rollback verificado, 11 arquivos e
arquivo de imagem com 175.020.032 bytes. Recursos temporários e tag própria
removidos. A execução no GitHub Actions, a recarga do arquivo em outro alvo e a
promoção de uma release aprovada têm gates próprios. O artifact da CI está
configurado com retenção de três dias; não há aceite de upload remoto ainda. [Fluxo e limites](operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

### Gate de consumo e CI entre jobs — preparação de 2026-10-19

O consumidor verifica o pacote por commit/hash externos, carrega a imagem exata
e testa HTTP em um container novo, sem rebuild ou fit. A entrega original e
as tags alheias são preservadas. A CI encadeia `quality` → `serving-delivery` →
`serving-consumer`; o último job recebe o artifact e o hash do produtor em outro
runner. Implementação, aceite nativo e **GitHub Actions aprovados**; detalhes no aceite remoto abaixo.
O teste local usa o arquivo já aprovado e não exige repetir o build.
[Procedimento, pins e limites](operations/SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Três jobs do GitHub Actions aprovados; próximo: promoção explícita e provisionamento**, sem repetir
os gates locais encerrados. [Resultado e limites](operations/SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).

### CI de entrega e consumo aprovada — 2026-10-19

A [execução 39306262564](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
passou nos três jobs do commit `6a3782b0ccb9d6c0a098251743483aed73e49079`:
qualidade → build/qualificação → consumo do artifact em outro runner. A API
confirma sucesso e runners distintos; o artifact pequeno do consumidor foi
baixado, teve digest conferido e seus recibos de recarga/HTTP/cleanup lidos.
**Gate remoto encerrado.** Próximo: promoção explícita e provisionamento
reproduzível KinD/Helm; registry e deploy permanente ainda têm critérios próprios.
[Resultado e limites](operations/SERVING_ROLLOUT.md#aceite-do-fluxo-no-github-actions--2026-10-19).

### Monitoramento mínimo das features persistidas — preparação de 2026-10-19

O exporter existente ganhou schema/ausência e relógios das 19 features, além de
quatro distribuições com suporte e bins fixos. Compara primeiras e últimas
1.000 decisões do checkpoint, sem sobreposição; baseline operacional, não de
treino. Acrescenta painel Grafana e alertas sem gatilho de CT. Não carrega modelo,
recalcula features ou altera bancos nativos. Testes controlados concluídos;
**snapshot nativo e stack/painel pendentes**. Relatórios em
`data/reports/feature-monitoring/`, sem sobrescrita.
[Comandos, interpretação e limites](operations/FEATURE_MONITORING.md).

### Aceite nativo mínimo do monitoramento de features — 2026-10-19

O autor confirmou 116 testes aprovados e um opt-in ignorado, demonstração gerada,
snapshot nativo, exporter reconstruído, oito regras válidas e coleta Prometheus.
Os valores do dashboard foram conciliados com o snapshot: 1.000 decisões por
janela sem sobreposição, contagens de qualidade/relógio zeradas e quatro
divergências entre 0,001457 e 0,004398. O limiar descritivo é 0,1.
O aceite cobre esse recorte operacional; não mede concept drift, desempenho
futuro ou frescor de ingestão externa e não dispara CT/promoção. O firing dos
novos alertas não foi ensaiado na bancada. A contagem recebe cor fixa neutra,
com conferência visual após refresh ainda pendente. Segue o provisionamento
incremental do fluxo completo em KinD + Helm.
[Procedimento, explicação conceitual e evidência](operations/FEATURE_MONITORING.md#aceite-nativo-mínimo--2026-10-19).

### Fundação KinD/Helm — preparação de 2026-10-20

Primeiro incremento do fluxo completo: cluster dedicado de um node Kubernetes
1.35.8 fixado por digest, chart com RustFS/PostgreSQL e dois PVCs novos. Imagens
dos serviços preservadas dos pins já utilizados. Kubeconfig, caches, credenciais
privadas e recibos sob `data/storage/kind-platform/`; contexto explícito.
O ensaio grava marcadores próprios SQL/S3 e exige releitura após recriar os pods
com os mesmos PVCs. A execução nativa ainda está pendente. Não migra os dados
históricos, não troca o tracking e não comprova backup ou perda do cluster.
[Runbook, limites e comandos](operations/KIND_FOUNDATION.md).

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](operations/KIND_TRACKING.md#aceite-nativo--2026-10-20).

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.
