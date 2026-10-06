# Fraud Detection MLOps

Projeto de portfólio para construir um pipeline reproduzível de detecção de fraude
com dados temporais do Fraud Detection Handbook. Estrutura inicial criada com
[Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/).

O estágio atual cobre ambiente Poetry, CI, extração Bronze e verificação de integridade.
A preparação Silver, as features temporais, a avaliação e a operação do modelo são próximos passos.

## Ambiente e qualidade

Python 3.14.4 e Poetry 2.4.3. Na raiz do checkout:

```bash
poetry install
poetry check --lock
poetry run ruff check .
poetry run ruff format --check .
poetry run pytest -q
```

As dependências são fixadas em `poetry.lock`. Os testes de extração usam respostas
simuladas: a CI não baixa o dataset e não precisa de credenciais.

## Fonte e contrato da Bronze

Fonte: [Fraud-Detection-Handbook/simulated-data-raw](https://github.com/Fraud-Detection-Handbook/simulated-data-raw).
Versão fixada: `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a`.
O inventário `references/handbook_source.json` contém 183 arquivos diários `.pkl`,
de 2018-04-01 a 2018-09-30, totalizando 107.121.710 bytes (aproximadamente 107 MB).
Esse inventário foi obtido da árvore Git da versão fixada e fica sob controle de versão.
As URLs de download contêm o commit completo.

Nesta etapa, `data/raw/handbook/<commit>/` é a Bronze. Cada arquivo conserva os bytes
originais. A extração não desserializa pickle, não transforma colunas e não calcula
features. A futura Silver oferecerá dados validados em Parquet para consulta com DuckDB.

| Controle | Evidência |
| --- | --- |
| Origem reproduzível | Repositório, commit e caminho no inventário e no manifesto |
| Identidade na fonte | Tamanho e identificador Git blob SHA-1 conferidos antes de aceitar o arquivo |
| Integridade local | SHA-256 calculado na aquisição e conferido na reutilização e na verificação |
| Retomada | Manifesto atualizado após cada arquivo aceito; arquivos existentes são verificados |
| Auditoria operacional | Um JSON por execução com identificador, horários UTC, intervalo, resultado, arquivos e erro |
| Publicação de arquivo | Download temporário promovido por renomeação após conferir integridade |
| Concorrência local | Um lock por snapshot impede extrações e verificações simultâneas |

O identificador Git SHA-1 inclui o cabeçalho Git `blob <tamanho>\0`; ele não é o SHA-1
simples do arquivo. O SHA-256 é calculado localmente, e não representa um checksum
publicado pelos autores. Uma nova versão da fonte deve ter um novo inventário revisado
e produzir um diretório separado, identificado por seu commit.

## Primeira execução: sete dias

```bash
poetry run python -m fraud_detection_mlops.dataset extract \
  --start-date 2018-04-01 --end-date 2018-04-07

poetry run python -m fraud_detection_mlops.dataset verify
```

O comando `verify` trabalha offline e deve informar `Verified: 7/183; complete: False`.
Repita a extração do mesmo intervalo: o resultado esperado é `Downloaded: 0; skipped: 7`.
Isso confirma que os arquivos foram conferidos e reutilizados. `skipped` também pode
significar um download anterior recuperado após falha de gravação do manifesto.

A omissão das datas seleciona todo o inventário:

```bash
poetry run python -m fraud_detection_mlops.dataset extract
poetry run python -m fraud_detection_mlops.dataset verify --require-complete
```

Ao final, a verificação deve informar `Verified: 183/183; complete: True`.
Há opções `--output-root`, `--inventory`, `--timeout` e `--attempts`;
consulte `extract --help`. O timeout padrão é de 30 segundos por operação de rede,
e o limite é de três tentativas por arquivo. Falhas transitórias de rede e HTTP
429/500/502/503/504 recebem novas tentativas com espera crescente.

## Manifesto e auditoria

Cada snapshot contém os arquivos diários, `manifest.json` e o diretório `runs/`.
O manifesto registra os arquivos aceitos, origem, tamanho, Git blob SHA-1, SHA-256,
horário do registro e cobertura parcial ou completa. Cada execução de extração
registra o SHA-256 do inventário utilizado, arquivos baixados ou reutilizados,
contagens e resultado `success`, `failed` ou `interrupted`.

O diretório `data/` é ignorado pelo Git. Código, inventário e lockfile vão para o
repositório; dados, manifestos e auditorias permanecem no armazenamento local nesta
fase. Esses JSONs precisam ser preservados junto aos dados. Não há ainda armazenamento
remoto de artefatos, assinatura do manifesto ou auditoria imutável.

Se a extração falhar, leia o erro e o JSON mais recente de `runs/`, corrija a causa e
repita o comando. Arquivos já aceitos são conferidos e reaproveitados. Um arquivo
corrompido causa falha e permanece disponível para investigação, sem sobrescrita
automática. Um arquivo ausente pode ser recuperado se sua data estiver selecionada.
Arquivos sem registro podem ser recuperados ao selecionar seu intervalo; arquivos
estranhos à fonte exigem inspeção. O verificador exige correspondência exata entre
arquivos `.pkl` e registros do manifesto.

Se o processo for encerrado abruptamente, uma auditoria pode permanecer `running`,
e o lock `.extract.lock` pode permanecer no snapshot. Só remova esse lock depois de
confirmar que nenhum processo está usando o snapshot. Erros detectados antes de
iniciar a execução, como um intervalo inválido, não criam auditoria.

Essa auditoria cobre aquisição, origem e integridade dos arquivos. Tipos, campos
obrigatórios, unicidade de transações, nulos, distribuição de fraude e consistência
temporal serão avaliados na Silver. Os dados são simulados; a avaliação futura deve
considerar essa limitação e preservar a ordem temporal para evitar vazamento.

## Organização

| Caminho | Responsabilidade |
| --- | --- |
| `fraud_detection_mlops/bronze.py` | Aquisição, retomada, checksums e auditoria |
| `fraud_detection_mlops/dataset.py` | CLI de extração e verificação |
| `references/handbook_source.json` | Inventário da fonte fixada |
| `data/raw/handbook/<commit>/` | Bronze local |
| `data/interim/` | Reservado para preparação Silver |
| `data/processed/` | Reservado para datasets de modelagem |
| `tests/` | Verificações de integridade, recuperação e integração Parquet/SQL |
| `.github/workflows/ci.yml` | Qualidade automatizada |

Os módulos de features, modelagem e gráficos ainda são scaffolds do template.

## Referência e termos da fonte

Documentação: [Simulated Dataset, Fraud Detection Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html).
O repositório de dados consultado não apresenta uma licença separada para o dataset;
o inventário registra essa situação. Este projeto referencia e baixa a fonte, sem
incluir os arquivos de dados no Git. Revise os termos da fonte antes de redistribuir
os dados.
