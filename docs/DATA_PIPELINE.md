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
| Inventário | [handbook_source.json](../references/handbook_source.json) |
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

| Funções em `bronze.py` | Responsabilidade |
| --- | --- |
| `load_inventory`, `select_files` | Validar origem, paths, datas e intervalo solicitado |
| `_download`, `_file_checksums` | Baixar temporariamente e conferir os bytes antes de aceitar |
| `_load_manifest`, `_checkpoint` | Validar metadados e atualizar a cobertura após cada sucesso |
| `_snapshot_lock`, `_write_json` | Controlar concorrência local e substituir JSONs atomicamente |
| `extract_bronze` | Coordenar aquisição, reutilização, retomada e auditoria |
| `verify_bronze` | Conferir offline arquivos, metadados, checksums e cobertura |

A lógica do download é uma parte do módulo; os demais controles tratam das condições
de falha e reprodução. A CLI em [dataset.py](../fraud_detection_mlops/dataset.py)
expõe `extract` e `verify`. Os procedimentos completos estão em [Operations](OPERATIONS.md).

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
O [recibo desta etapa](../references/evidence/bronze_2026-10-06.json) relaciona a
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
As categorias e suas origens estão no [recibo](../references/evidence/bronze_2026-10-06.json).

A auditoria completa posterior informou 1.754.155 transações, com IDs únicos,
14.681 fraudes e 42 valores zero. Os demais controles implementados não encontraram
violações. A decisão da Silver é preservar os zeros, tipar as nove colunas e manter
todas as linhas. O [recibo do perfil](../references/evidence/silver_profile_2026-10-06.json)
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
[notebook da etapa](../notebooks/stages/02_silver_data_contract.ipynb).

O builder reutiliza o profiler e verifica o snapshot completo antes de converter.
Ele associa cada Parquet ao SHA-256 do pickle lido, confirma a leitura de volta e
reconcilia contagens por SQL antes de publicar o diretório completo. Auditorias
registram os hashes dos módulos e o ambiente; o commit Git avaliado será ligado
ao resultado real no fechamento da entrega. O procedimento está no runbook.

O autor informou `build` com `status: success` e verificação independente da Silver
com as mesmas contagens: 183 partições, 1.754.155 linhas e IDs distintos, 14.681
fraudes, 1.739.474 genuínas e 42 zeros. A auditoria responsável é
`silver_f9fc5a5083f14d9fadf6892a531e1488.json`. O
[recibo da execução](../references/evidence/silver_build_2026-10-06.json) distingue
essa saída de terminal dos artefatos nativos, que permanecem locais.

## EDA e fronteira da Gold

A EDA preparada opera offline sobre as partições de treino da Silver, preservando
os dados. Publica agregações, um painel visual, manifesto e auditoria em
`data/interim/handbook/<source_commit>/eda_v1/<run_id>/`. São artefatos de análise,
sem mudança no contrato `silver_v1`. O autor informou a execução de treino e
a verificação de nove outputs, com [recibo](../references/evidence/eda_training_2026-10-07.json)
e [interpretação](EDA.md#achados-informados-pelo-autor-em-2026-10-07).
O [protocolo](EVALUATION_PROTOCOL.md) fixa as janelas e o atraso simulado dos rótulos;
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
[recibo](../references/evidence/gold_build_2026-10-07.json). Integridade e contagens
aprovadas não demonstram valor preditivo; isso exigirá avaliação do baseline.
