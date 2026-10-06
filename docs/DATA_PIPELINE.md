# Pipeline de dados

Revisão de implementação documentada: `18242fcc8e340bad52a394c7c3624b78bd809b6c`.
Estado em 2026-10-06: aquisição Bronze implementada; integridade da aquisição completa
validada no ambiente do autor. Silver e Gold são próximas etapas.

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
identidade. A transformação será feita a partir dessa base. Na fase atual, a carga
é no filesystem local; a Bronze não é ainda uma tabela de banco de dados.

| Camada | Caminho | Estado e contrato |
| --- | --- | --- |
| Bronze | `data/raw/handbook/<source_commit>/` | Implementada: arquivos originais, manifesto e auditorias |
| Silver | `data/interim/` | Planejada: dados validados em Parquet, consultáveis via DuckDB |
| Gold | `data/processed/` | Planejada: datasets e features para modelagem com regras temporais explícitas |

Os nomes `raw`, `interim` e `processed` preservam a organização inicial do
Cookiecutter Data Science. O contrato exato de paths e schema da Silver será definido
após a inspeção dos dados.

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

Antes de escrever a Silver, vamos inspecionar colunas, tipos, nulos, unicidade dos
identificadores, rótulos de fraude, datas e consistência entre partições. Somente
esse diagnóstico poderá sustentar o contrato semântico e a transformação Parquet.

## Referência e termos

[Simulated Dataset, Fraud Detection Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html).
O repositório de dados consultado não apresenta uma licença separada para o dataset;
o inventário registra essa situação. Os arquivos de dados não são incluídos no Git.
Revise os termos da fonte antes de redistribuir os dados.
