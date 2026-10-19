# Pipeline de dados

Revisão da Bronze documentada: `18242fcc8e340bad52a394c7c3624b78bd809b6c`.
Estado em 2026-10-07: aquisição Bronze implementada; integridade da aquisição completa
validada no ambiente do autor. O diagnóstico completo foi informado pelo autor.
A Silver foi construída e verificada localmente, com 183 partições e todas as
contagens reconciliadas. Revisão Silver: `acf15169fed522751b974efae032d8683b64a036`,
com CI aprovada. A EDA de treino foi informada pelo autor e publicada na revisão `fc22a48`,
com CI aprovada. O autor informou construção e verificação da Gold real:
42 partições, 19 preditores e 402.877 linhas; o [contrato](GOLD_CONTRACT.md) define
suas features, splits e limites de evidência. Revisão Gold: `14ab57e`, com
[CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37652509812) e 104 testes.

## Fonte fixada

| Item | Valor |
| --- | --- |
| Fonte | [Fraud-Detection-Handbook/simulated-data-raw](https://github.com/Fraud-Detection-Handbook/simulated-data-raw) |
| Commit | `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a` |
| Inventário | [handbook_source.json](../../references/handbook_source.json) |
| Formato original | Pickle, um arquivo por dia |
| Período | 2018-04-01 a 2018-09-30, inclusive |
| Cobertura esperada | 183 arquivos consecutivos |
| Volume esperado | 107.121.710 bytes, aproximadamente 107 MB |
| Natureza | Dados simulados de transações e fraude |

O inventário foi obtido da árvore Git da revisão fixada. Cada registro contém data,
nome, caminho na fonte, tamanho e identificador Git blob SHA-1. O extrator valida o
inventário antes de acessar a rede ou criar a saída. As URLs de download contêm o
commit completo; não dependem do estado futuro da branch principal da fonte.

O projeto usa esse histórico como snapshot. Executar novamente o extrator confere e
reutiliza arquivos existentes; não descobre novos dias nem atualiza a fonte sozinho.
Uma mudança de fonte exige inventário revisado e produz um snapshot por novo commit.

## Organização ELT

A estratégia é extrair e carregar os bytes originais na Bronze, preservando sua
identidade. A transformação para Silver é feita a partir dessa base. Na fase atual, a carga
é no filesystem local; a Bronze não é ainda uma tabela de banco de dados.

| Camada | Caminho | Estado e contrato |
| --- | --- | --- |
| Bronze | `data/raw/handbook/<source_commit>/` | Implementada: arquivos originais, manifesto e auditorias |
| Silver | `data/interim/handbook/<source_commit>/silver_v1/` | Construída e verificada localmente: 183 Parquets, contrato, manifesto e reconciliação DuckDB |
| Gold | `data/processed/handbook/<source_commit>/gold_v1/` | Construção e verificação informadas pelo autor: 19 preditores, 42 partições e 402.877 linhas |

Os nomes `raw`, `interim` e `processed` preservam a organização inicial do
Cookiecutter Data Science. O [contrato da Silver](SILVER_CONTRACT.md) define schema,
paths e regras de aceitação, fundamentados na inspeção dos 183 arquivos.

A próxima etapa de persistência abrange **Bronze, Silver e todas as versões da
Gold**, além de artefatos e estado: [armazenamento e restauração](../operations/STORAGE.md).
O plano usa RustFS/S3 para conjuntos publicados com manifestos, preservando os
bytes/paths relativos; DuckDB consulta Parquet e os leitores atuais continuam
locais enquanto a migração não é entregue. O inventário completo e a cobertura
efetivamente arquivada serão registrados; este documento não afirma migração
S3 já executada nem substituição das camadas por tabelas PostgreSQL.

## Contrato da Bronze

- Cada partição é identificada por `YYYY-MM-DD.pkl` e corresponde ao inventário.
- O arquivo aceito mantém os bytes da fonte fixada.
- O tamanho e o Git blob SHA-1 devem corresponder ao inventário.
- O SHA-256 é calculado localmente e registrado para verificação posterior.
- O manifesto descreve exatamente os arquivos registrados no snapshot, com cobertura
  parcial ou completa.
- Cada extração iniciada sob o lock registra uma auditoria de execução.

O Git blob SHA-1 é calculado sobre o cabeçalho `blob <tamanho>\0` seguido dos bytes
do arquivo; não é o SHA-1 simples do arquivo. O SHA-256 é uma impressão digital local,
e não um checksum publicado pelos autores.

A extração não desserializa pickle, transforma colunas, elimina duplicatas nem
calcula features. Os testes de aquisição usam bytes controlados e respostas de rede
simuladas, pois este contrato trata de arquivos.

## Leitura do algoritmo por responsabilidade

| Implementação | Responsabilidade |
| --- | --- |
| `handbook_inventory.load_inventory`, `select_files` | Validar origem, paths, datas e intervalo solicitado |
| `_download`, `_file_checksums` | Baixar temporariamente e conferir os bytes antes de aceitar |
| `_load_manifest`, `_checkpoint` | Validar metadados e atualizar a cobertura após cada sucesso |
| `_snapshot_lock`, `artifacts.write_json` | Controlar concorrência local e substituir JSONs atomicamente |
| `extract_bronze` | Coordenar aquisição, reutilização, retomada e auditoria |
| `verify_bronze` | Conferir offline arquivos, metadados, checksums e cobertura |

A aquisição e os controles de integridade estão em
[handbook_bronze.py](../../fraud_detection_mlops/data/ingestion/handbook_bronze.py);
o inventário fica em
[handbook_inventory.py](../../fraud_detection_mlops/data/ingestion/handbook_inventory.py).
A CLI em [handbook_download.py](../../fraud_detection_mlops/data/ingestion/handbook_download.py)
expõe `extract` e `verify`. Os procedimentos completos estão em [Operations](../operations/OPERATIONS.md).

Os contratos físicos ficam em `data/contracts/`, os builders em `data/datasets/`
e o perfil e a EDA em `data/quality/`, dentro de `fraud_detection_mlops/`.
As fórmulas e o schema das features ficam no pacote `features/`. A
[arquitetura](../ARCHITECTURE.md#dados-e-features) explica essas dependências.

## Manifesto e auditoria

`manifest.json` registra versão de schema, repositório e commit, arquivos aceitos,
URL, tamanho, Git blob SHA-1, SHA-256, horário UTC de registro, contagens e cobertura.
É atualizado após cada arquivo aceito, permitindo retomar aquisições parciais.

`runs/<run_id>.json` registra origem, SHA-256 do inventário, intervalo solicitado,
timeout, limite de tentativas, horários UTC, resultado, arquivos baixados ou
reutilizados, contagens e erro quando capturado. Os resultados previstos são
`success`, `failed` e `interrupted`; uma interrupção abrupta pode deixar `running`.
Erros anteriores ao início da execução, como intervalo inválido ou lock existente,
não geram uma nova auditoria de extração.

O manifesto e as auditorias não incorporam automaticamente o commit do código.
O [recibo desta etapa](../../references/evidence/bronze_2026-10-06.json) relaciona a
implementação avaliada, as execuções informadas pelo autor e a CI consultada.

## Decisões e limites

1. **Fonte fixada por commit:** permite repetir a aquisição mesmo se a fonte mudar.
2. **Bytes originais na Bronze:** preserva a origem para transformações auditáveis.
3. **Controles de integridade e retomada desde o início:** tornam a base reproduzível,
   com custo de complexidade maior que um downloader simples.
4. **Filesystem local:** mantém esta primeira etapa acessível e reproduzível.
   Armazenamento remoto de artefatos será uma decisão futura.

Não há armazenamento remoto de artefatos, assinatura de metadados nem garantia de
imutabilidade. Arquivos existentes são verificados e não têm corrupção corrigida por
sobrescrita automática. Manifestos e auditorias devem ser preservados com o snapshot.
Integridade de bytes não comprova unicidade, validade de valores nem ausência de
vazamento temporal. A simulação também limita conclusões sobre fraude real.

## Evidência e próximo contrato

A extração completa informada pelo autor baixou 176 arquivos e reutilizou sete.
A verificação offline informou `Verified: 183/183; complete: True`.
Há 39 testes locais aprovados e uma CI aprovada para a revisão documentada.
As categorias e suas origens estão no [recibo](../../references/evidence/bronze_2026-10-06.json).

A auditoria completa posterior informou 1.754.155 transações, com IDs únicos,
14.681 fraudes e 42 valores zero. Os demais controles implementados não encontraram
violações. A decisão da Silver é preservar os zeros, tipar as nove colunas e manter
todas as linhas. O [recibo do perfil](../../references/evidence/silver_profile_2026-10-06.json)
registra resultados informados pelo autor, sem antecipar a aceitação dos Parquets.

## Referência e termos

[Simulated Dataset, Fraud Detection Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html).
O repositório de dados consultado não apresenta uma licença separada para o dataset;
o inventário registra essa situação. Os arquivos de dados não são incluídos no Git.
Revise os termos da fonte antes de redistribuir os dados.

## Diagnóstico e construção da Silver

A primeira partição foi inspecionada pelo autor: 9.488 linhas, nove colunas, sem nulos
nem duplicatas observadas no dia. Quatro colunas guardam inteiros com dtype `object`.
O resultado inicial não validava todo o histórico. A auditoria completa foi então
informada pelo autor e fundamenta o [contrato vigente](SILVER_CONTRACT.md), com evidências no
[notebook da etapa](../../notebooks/stages/02_silver_data_contract.ipynb).

O builder reutiliza o profiler e verifica o snapshot completo antes de converter.
Ele associa cada Parquet ao SHA-256 do pickle lido, confirma a leitura de volta e
reconcilia contagens por SQL antes de publicar o diretório completo. Auditorias
registram os hashes dos módulos e o ambiente; o commit Git avaliado será ligado
ao resultado real no fechamento da entrega. O procedimento está no runbook.

O autor informou `build` com `status: success` e verificação independente da Silver
com as mesmas contagens: 183 partições, 1.754.155 linhas e IDs distintos, 14.681
fraudes, 1.739.474 genuínas e 42 zeros. A auditoria responsável é
`silver_f9fc5a5083f14d9fadf6892a531e1488.json`. O
[recibo da execução](../../references/evidence/silver_build_2026-10-06.json) distingue
essa saída de terminal dos artefatos nativos, que permanecem locais.

## EDA e fronteira da Gold

A EDA preparada opera offline sobre as partições de treino da Silver, preservando
os dados. Publica agregações, um painel visual, manifesto e auditoria em
`data/interim/handbook/<source_commit>/eda_v1/<run_id>/`. São artefatos de análise,
sem mudança no contrato `silver_v1`. O autor informou a execução de treino e
a verificação de nove outputs, com [recibo](../../references/evidence/eda_training_2026-10-07.json)
e [interpretação](EDA.md#achados-informados-pelo-autor-em-2026-10-07).
O [protocolo](../modeling/EVALUATION_PROTOCOL.md) fixa as janelas e o atraso simulado dos rótulos;
a Gold implementa features e materializa os splits de modelagem na entrega de preparação.

## Construção causal da Gold

A [Gold](GOLD_CONTRACT.md) mantém o protocolo `temporal_v1` e computa features antes
de filtrar os splits, usando também o histórico dos gaps. O contexto termina em
26 de maio: os dados futuros reservados não alimentam a primeira Gold.
Contagens/médias de cliente, razões de valor e volume de terminal usam passado
estrito. Fraudes conhecidas do terminal usam uma janela deslocada em sete dias,
com o mesmo denominador elegível. O alvo e os IDs ficam em metadados; o loader
seleciona os 19 preditores pela allowlist. Não há transformações aprendidas nesta
etapa. A construção e a verificação reais foram informadas pelo autor, com
[recibo](../../references/evidence/gold_build_2026-10-07.json). Integridade e contagens
aprovadas não demonstram valor preditivo; isso exigirá avaliação do baseline.

## Experimentos sobre a Gold

O [baseline](../modeling/BASELINE.md) consome os splits de treino e validação da Gold sem
alterá-los. Ajusta pipelines com os 19 preditores, persiste modelo/transformações,
scores de validação, métricas e manifesto em
`data/processed/handbook/<source_commit>/baseline_v2/<run_id>/`.
Esses outputs são artefatos de experimento; não mudam as versões Bronze/Silver/Gold.
O teste tem integridade conferida, mas não entra no fit ou na avaliação do runner.

## Arquivamento da Bronze: preparo completo

O snapshot do commit pode ser preparado junto de seu manifesto, auditorias e
inventário de origem, preservando bytes e Git blob SHA-1/SHA-256. O preparo e sua
verificação offline estão implementados/testados com fixtures, fora do pacote
congelado, em `scripts/storage/bronze_archive.py`. O autor aprovou o preparo
nativo de 183 partições: 187 arquivos do snapshot e um inventário arquivado,
107.309.243 bytes. O transporte S3 implementado em `scripts/storage/bronze_transfer.py`
passou publicação/reenvio/restauração nativos, conforme relato do autor: 189 objetos,
zero uploads no reenvio e 183 partições completas recuperadas. O restore passou
pelo verificador Bronze com o inventário baixado, sem usar os originais nessa
conferência. [Evidência](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
O arquivamento está validado; os consumidores continuam usando os paths locais. [Procedimento e orçamento](../operations/STORAGE.md#bronze-completa--preparo-offline-antes-da-transferência).
Isso não reconstrói a Silver/Gold nem altera os dados usados nos modelos.


## Arquivamento da Silver com contrato

`scripts/storage/silver_archive.py` prepara `silver_v1/`, os arquivos
`runs/silver_<run_id>.json` do mesmo commit, o inventário de origem e o contrato
`references/silver_contract_v1.json`. A auditoria do run criador é obrigatória;
os bytes históricos não são reescritos. EDA e outros conjuntos do diretório
`data/interim/` permanecem fora desse bundle.
O perfil `fraud_silver_bundle_v1` limita paths, proveniência, volume e metadados.

`verify_silver` é reaplicado no staging e na restauração, com inventário/contrato
arquivados: SHA-256/tamanhos, schema Arrow, linhas por partição, cobertura de
inventário, contagens SQL e IDs distintos. Não converte Parquet, abre Bronze ou
refaz sua aceitação/normalização; os hashes de entrada e campos históricos de
proveniência são preservados, sem recomputar os bytes da Bronze. Auditorias são
preservadas por integridade, sem nova interpretação semântica dos campos antigos.
O autor aprovou o preparo, publicação, reenvio e restauração nativos:
187 arquivos de payload/40.288.931 bytes, uma auditoria incluída e 188 objetos S3.
A cópia passou schema e reconciliação SQL com contrato/inventário baixados:
183 partições, 1.754.155 linhas/IDs distintos, 14.681 fraudes, 1.739.474 genuínas
e 42 valores zero. O reenvio fez zero uploads; o restore não leu Bronze,
fonte ou metadados originais. Os consumidores continuam nos paths locais.
[Evidência](../../references/evidence/silver_archive_author_validation_2026-10-15.json). [Procedimento](../operations/STORAGE.md#silver-completa-preparo-e-recuperação-com-contrato).


## Arquivamento do conjunto Gold original

`scripts/storage/gold_archive.py` seleciona `gold_v1/`, `runs/gold_<run_id>.json`
do mesmo commit e quatro referências: inventário, contratos Silver/Gold e
protocolo temporal. Requer a auditoria do run criador e preserva todos os bytes.
O manifesto de arquivamento `fraud_gold_bundle_v1` relaciona os hashes dessas
referências e do manifesto Gold, sem reescrever o protocolo histórico.

O staging e o restore passam por `verify_gold` existente com referências
arquivadas: integridade, schema Arrow, 19 features e suas invariantes, datas,
cobertura temporal, IDs únicos e contagens por split. Isso verifica o dataset
salvo; não recalcula features a partir da Silver, executa modelos ou produz
novas métricas/avaliação do holdout. Proveniência de entrada e auditorias são
preservadas por bytes; não são recomputadas contra uma nova fonte.

O inventário do autor encontrou 43 arquivos/25.151.651 bytes em `gold_v1`, além
da auditoria em `runs/`. A família `data/processed/` inteira soma 506 arquivos,
com Gold de desenvolvimento, modelos, estudo e avaliações que permanecem em
etapas próprias. O autor aprovou preparo e recuperação de `gold_v1` no RustFS:
48 arquivos/25.196.448 bytes, 49 objetos, reenvio sem uploads e restore com as
quatro referências baixadas, 42 partições/19 features/402.877 linhas. Splits:
268.668 treino, 67.255 validação, 66.954 teste. O restore não leu Bronze/Silver
ou fonte original, nem recomputou features. Os leitores continuam locais.
[Evidência nativa](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
[Runbook](../operations/STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo).


## Arquivamento do dataset de desenvolvimento

O inventário nativo informou 64 arquivos/38.159.334 bytes no dataset de
`development_gold_v2` e 319 arquivos/232.943.522 bytes em `study/` (incluindo
SQLite de 540.672 bytes e `.writer.lock`). São metadados; não provam que o banco
esteja íntegro, parado ou recuperável. [Inventário](../../references/evidence/development_storage_inventory_author_2026-10-15.json).

O perfil `fraud_development_gold_bundle_v1` inclui somente manifesto e Parquets
com inventário e `hgb_optuna_protocol_v1.json`. Exige run ID explícito e preserva
manifesto/proveniência, sem ler Silver, recomputar features ou executar treino.
`verify_data` existente verifica política, identidade, datas autorizadas, schema,
invariantes das 19 features, hashes, contagens e IDs únicos usando a cópia.
O autor aprovou preparo e recuperação no RustFS: 66 arquivos/38.202.741 bytes,
67 objetos publicados/conferidos, reenvio sem uploads e restore com 63 partições,
19 features e 603.669 linhas. Política/inventário foram baixados; run e hash do
manifesto se mantiveram. `study_included=false`, sem leitura dos originais,
Bronze/Silver ou estudo e sem recomputar features. Os leitores continuam locais.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).

O estudo exige backup consistente do SQLite coordenado com modelos, predições e
recibos. O perfil implementado fixa run/manifesto do dataset e identidade
de seu bundle; o ciclo nativo do estudo está registrado abaixo. A cópia do dataset não declara o estudo ou tracking recuperados.
[Runbook](../operations/STORAGE.md#gold-de-desenvolvimento-dataset-e-estudo).


O backup offline do estudo HGB referencia explicitamente o bundle verificado do
dataset, seu run ID e SHA-256 do manifesto. Inclui uma cópia desse manifesto,
inventário e protocolo HGB; não duplica os 63 Parquets no bundle do estudo.
A verificação precisa do bundle/restore do dataset disponível localmente, com
integridade e semântica aprovadas. Os Parquets entram somente na cópia temporária
usada pelo verificador existente de trials, predições, métricas e orçamento.
Os bytes de relatório/modelos/predições/recibos e referências de tracking são
preservados; o SQLite é um snapshot da API de backup com equivalência lógica
conferida. O autor aprovou o preparo nativo: 20 trials concluídos/63 tentativas,
322 arquivos/233.048.483 bytes e digest lógico preservado. O autor aprovou também
o ciclo S3 em 2026-10-16: 323 uploads/verificações, reenvio zero uploads/323
reutilizados e restore de 322 arquivos com os mesmos 20 trials/63 tentativas,
digest lógico e dataset vinculado. Fonte original não foi lida pelo restore.
[Evidência S3](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json). [Evidência](../../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
[Contrato e recuperação](../operations/STORAGE.md#backup-consistente-do-estudo-hgb-preparo-offline).
