# Operação do pipeline de dados

Escopo: aquisição e verificação Bronze da revisão `18242fcc8e340bad52a394c7c3624b78bd809b6c`,
mais o diagnóstico e a construção da Silver `silver_v1`.
Execute os comandos na raiz do checkout. Ambiente de referência: Python 3.14.4 e
Poetry 2.4.3. A fonte e os contratos estão em [Data pipeline](../data/DATA_PIPELINE.md).

## Preparar e conferir o ambiente

```bash
poetry install
poetry check --lock
poetry run tox -e py314
```

A CI executa a mesma configuração tox. `make validate` é um atalho; testes rápidos
continuam disponíveis com `poetry run pytest -q`. O fluxo de instalação e isolamento
está em [Testing](TESTING.md). Os testes da extração simulam a rede e não baixam o
dataset real. A verificação de uma Bronze real é um procedimento separado.

## Adquirir uma amostra e conferir reutilização

```bash
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download extract \
  --start-date 2018-04-01 --end-date 2018-04-07
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download verify
```

Num snapshot novo, o esperado é baixar sete arquivos. A verificação deve mostrar
`Verified: 7/183; complete: False`. Um snapshot que já tenha mais dias pode mostrar
cobertura maior. Para conferir a reutilização, repita a extração do mesmo intervalo;
esperamos `Downloaded: 0; skipped: 7` quando os arquivos estiverem íntegros.

## Completar o snapshot

```bash
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download extract
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download verify --require-complete
```

Omitir datas seleciona todo o inventário. Arquivos existentes são conferidos e
reutilizados. O resultado esperado final é `Verified: 183/183; complete: True`.
O comando `verify` trabalha offline e retorna erro quando a cobertura exigida ou a
integridade não é atendida. O conteúdo semântico das transações não é validado aqui.

## Localizar os artefatos

O diretório padrão do snapshot é:

```text
data/raw/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/
```

Ali ficam os arquivos `.pkl`, `manifest.json` e `runs/<run_id>.json`. O identificador
impresso no fim da extração permite encontrar sua auditoria. A leitura dos JSONs
pode ser feita no editor; para conferir a estrutura do manifesto pelo terminal:

```bash
python3 -m json.tool \
  data/raw/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/manifest.json
```

O recibo versionado em `references/evidence/` resume um marco histórico. Preserve o
manifesto e as auditorias nativas com o snapshot, pois `data/` não vai para o Git.
O comando `verify` não cria uma auditoria de extração nova.

## Opções

| Opção de `extract` | Uso e padrão |
| --- | --- |
| `--start-date`, `--end-date` | Intervalo inclusivo dentro do inventário; formato `YYYY-MM-DD` |
| `--output-root` | Diretório pai dos snapshots; padrão `data/raw/handbook/` |
| `--inventory` | Inventário revisado; padrão `references/handbook_source.json` |
| `--timeout` | Timeout por operação de rede, não da execução inteira; padrão 30 s, CLI aceita 1 a 60 s |
| `--attempts` | Limite de tentativas por arquivo; padrão 3, CLI aceita 1 a 5 |

`verify` aceita `--output-root`, `--inventory` e `--require-complete`. Use a mesma raiz
e o mesmo inventário da extração. Consulte a CLI para sua referência executável:

```bash
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download extract --help
poetry run python -m fraud_detection_mlops.data.ingestion.handbook_download verify --help
```

## Diagnóstico e recuperação

| Situação | Como agir |
| --- | --- |
| Falha transitória de rede | O extrator tenta novamente para erros transitórios e HTTP 429/500/502/503/504. Se falhar, confira a auditoria, restabeleça o acesso e repita o comando. |
| HTTP 404 | Confira URL, inventário e commit. A falha não recebe novas tentativas automáticas. |
| Download interrompido | Repita o intervalo. Downloads temporários da tentativa são removidos quando a limpeza normal é executada; arquivos já aceitos são reutilizados. |
| Arquivo esperado ausente | Selecione sua data ao repetir a extração para recuperá-lo da fonte fixada. |
| Arquivo com corrupção | Preserve uma cópia para investigação. Retire explicitamente o arquivo inválido do snapshot e repita sua data para obter uma cópia verificada. O extrator não sobrescreve a corrupção automaticamente. |
| Manifesto ausente após falha | Repetir o intervalo permite conferir e registrar arquivos válidos já presentes. |
| Arquivo conhecido sem registro | Inclua sua data na extração. O arquivo é conferido antes de ser incorporado ao manifesto. |
| Arquivo estranho à fonte | Inspecione e retire-o explicitamente do snapshot. O verificador exige correspondência exata entre `.pkl` e manifesto. |
| Lock existente | Confirme que não há extração ou verificação ativa. Um encerramento abrupto pode ter deixado `.extract.lock`; só então remova o lock residual. |
| Auditoria em `running` | Investigue possível encerramento abrupto e lock residual. Não trate essa execução como bem-sucedida. |
| Metadados inconsistentes | Preserve os documentos para investigação e compare com o inventário. Não edite checksums para contornar a verificação. |

Após qualquer recuperação, execute novamente `verify`; para a base completa, use
`--require-complete`. Em caso de perda irrecuperável do manifesto, uma nova aquisição
em outra `--output-root` preserva o snapshot antigo para investigação.

## Fechar uma entrega

Confira o diff, documentação, evidências e os checks técnicos aplicáveis antes do
commit. Os dados permanecem locais. Após o push, consulte a execução da CI que
corresponde ao novo commit. Siga a [política de documentação](../project/DOCUMENTATION_POLICY.md)
e registre resultados observados com a origem da evidência.

## Diagnóstico preparatório da Silver

Execute na raiz do checkout após instalar as dependências:

```bash
poetry run python -m fraud_detection_mlops.data.quality.transaction_profile
```

O comando verifica a integridade e a cobertura completa da Bronze antes de ler os
pickles. Depois audita as partições sem transformar os arquivos e imprime um resumo.
O relatório padrão fica em `data/interim/handbook/<source_commit>/profile.json`.
A leitura termina antes da gravação; se ocorrer uma falha, um relatório antigo
existente pode permanecer. Confira o sucesso do comando e o horário de geração.

Opções: `--bronze-root`, `--inventory` e `--report-path`. O último permite preservar
um relatório com outro nome. Consulte `profiling --help`. A execução mantém em memória
os identificadores vistos para conferir duplicidade entre arquivos, e processa os
DataFrames uma partição por vez.

O diagnóstico completo foi informado pelo autor e interpretado no
[contrato vigente](../data/SILVER_CONTRACT.md). O rascunho foi preservado como histórico.
Os testes e a CI validam o código com bases controladas; a auditoria real é uma
execução local separada.

## Construir e verificar a Silver

Com a Bronze completa disponível, execute offline:

```bash
poetry run python -m fraud_detection_mlops.data.datasets.silver_dataset build
poetry run python -m fraud_detection_mlops.data.datasets.silver_dataset verify
```

O build verifica a integridade da Bronze e recalcula o perfil antes da conversão.
Pode demorar mais que o profiler, pois também grava, relê e confere os Parquets.
Nenhum novo download é realizado. Os parâmetros disponíveis são `--bronze-root`
(build), `--output-root`, `--inventory` e `--contract`. A raiz padrão de saída é
`data/interim/handbook`; mantenha os mesmos parâmetros ao verificar e consultar.

Resultado esperado para a fonte fixada:

O autor confirmou todas as contagens abaixo na execução de 2026-10-06, com
`build` e `verify` aprovados. O
[recibo](../../references/evidence/silver_build_2026-10-06.json) registra a auditoria
`f9fc5a5083f14d9fadf6892a531e1488`; futuras execuções devem reconciliar novamente.

| Campo | Esperado a partir do diagnóstico da Bronze |
| --- | --- |
| `verified_partitions` | 183 |
| `rows` | 1.754.155 |
| `distinct_transaction_ids` | 1.754.155 |
| `fraud_count` | 14.681 |
| `genuine_count` | 1.739.474 |
| `zero_amounts` | 42 |

O destino é `data/interim/handbook/<source_commit>/silver_v1/`, incluindo
`manifest.json` e as partições Parquet. O build imprime `audit_path`; preserve essa
auditoria junto com os dados. Repetir o build verifica e retorna `status: reused`
sem regravar um dataset íntegro.

Para consultar os arquivos com DuckDB, execute na raiz do projeto:

```bash
poetry run python - <<'PY'
from fraud_detection_mlops.data.datasets.silver_dataset import connect_silver

with connect_silver() as connection:
    print(connection.execute('''
        SELECT tx_date, count(*) AS transactions, sum(TX_FRAUD) AS frauds
        FROM transactions
        GROUP BY tx_date
        ORDER BY tx_date
        LIMIT 7
    ''').fetchdf())
PY
```

`connect_silver` confere a saída antes de criar uma view numa sessão em memória.
Use o context manager para encerrar a conexão. A coluna SQL `tx_date` é derivada
da partição; as nove colunas físicas são descritas no contrato.

## Recuperação da Silver

- Se a aceitação falhar, confira `acceptance_summary` e o erro na auditoria.
  Investigue a Bronze ou o contrato; não contorne a falha removendo linhas.
- Se uma escrita falhar, corrija a causa e repita o build. O destino só é publicado
  depois da validação completa; falhas normais limpam os arquivos temporários.
- Se houver corrupção na Silver existente, preserve uma cópia para investigação.
  Reconstrua em outra `--output-root` ou retire explicitamente o diretório inválido
  antes de reconstruir. O builder não sobrescreve uma saída corrompida.
- Um encerramento abrupto pode deixar `.silver_v1.lock`, `.silver-staging-*` e uma
  auditoria `running` no diretório do commit. Confirme que não há build ativo antes
  de retirar o lock residual e o staging. Preserve a auditoria.

Após recuperar, execute `verify`. Esse comando verifica a Silver e não a Bronze;
use `dataset verify --require-complete` para a integridade da origem. Não edite
checksums para contornar verificações. Ao fechar o marco, registre o resultado
real da conversão, a auditoria correspondente e o commit avaliado; o recibo do
profiler não comprova que os Parquets foram construídos.

A entrega local da Silver teve construção e verificação aprovadas e foi publicada
na revisão `acf1516`, com CI aprovada. Ao fechar outra alteração, associe a revisão
avaliada e a CI correspondente ao seu recibo. Preserve o manifesto e a auditoria nativa com os Parquets; o recibo
versionado é uma síntese da saída fornecida pelo autor.

## Gerar e verificar a EDA de treino

Após a instalação das novas dependências e com a Silver disponível:

```bash
make eda
```

O comando equivale a `poetry run python -m fraud_detection_mlops.data.quality.training_eda build`.
Use o `eda_path` impresso para verificar o relatório:

```bash
poetry run python -m fraud_detection_mlops.data.quality.training_eda verify <eda_path>
```

Execute a CLI com `--help` para conferir opções. O runbook específico é
[EDA](../data/EDA.md); a interpretação usa o
[notebook da etapa](../../notebooks/stages/03_silver_eda.ipynb). Preserve os nove outputs,
o manifesto e a auditoria local. Não comite `data/`. Compartilhe primeiro o resumo
impresso e os checks; resultados científicos ainda dependem da leitura das tabelas.

## Construir e verificar a Gold

Com a Silver completa e os checks aprovados:

```bash
poetry run python -m fraud_detection_mlops.data.datasets.gold_dataset build
poetry run python -m fraud_detection_mlops.data.datasets.gold_dataset verify
```

Atalhos: `make gold` e `make verify-gold`. O build trabalha offline, confere toda a
Silver e gera features no contexto até 26 de maio. Pode usar memória para ordenar
as janelas; temporários do DuckDB ficam no staging e são limpos ao terminar.
Nenhum treinamento é executado. A saída esperada para o protocolo inicial contém
42 partições e 19 preditores. Contagens reais de validação/teste serão reconciliadas
com a Silver durante a execução, sem publicar distribuições de fraude dos holdouts.

O destino é `data/processed/handbook/<source_commit>/gold_v1/`. Preserve o manifesto,
os Parquets e a auditoria `runs/gold_<run_id>.json` adjacente à Gold.
Use `build --help` e `verify --help` para conferir opções: `--output-root`,
`--inventory`, `--silver-contract`, `--protocol` e `--contract`; o build também
aceita `--silver-root`. Mantenha os mesmos contratos e raízes na verificação.

Repetir o build confere e reutiliza uma Gold compatível. Se a escrita falhar,
investigue a auditoria e corrija a causa antes de repetir. Uma Gold corrompida ou
incompatível não é sobrescrita: preserve-a para investigação e reconstrua em outra
`--output-root` ou retire explicitamente a versão inválida. Não altere checksums
para contornar falhas. Após um encerramento abrupto, confirme que nenhum build
está ativo antes de retirar `.gold_v1.lock` ou staging residual; preserve a auditoria.
Erros anteriores ao lock, como contrato inválido, não geram auditoria nova.

Para consultar somente as features de treino:

```bash
poetry run python - <<'PYCODE'
from fraud_detection_mlops.data.datasets.gold_dataset import load_gold_split

X, y, metadata = load_gold_split("train")
print("Shapes:", X.shape, y.shape, metadata.shape)
print("Features:", list(X.columns))
PYCODE
```

Esse loader verifica toda a Gold antes de carregar o split solicitado. Use `X`
como entrada do modelo; metadados contêm IDs e alvo para auditoria. Consultar
`test` fica para a avaliação final após congelar modelo e parâmetros. Os detalhes
estão no [contrato](../data/GOLD_CONTRACT.md) e no
[notebook da etapa](../../notebooks/stages/04_gold_temporal_features.ipynb).

## Treinar e verificar o baseline temporal

O [runbook do baseline](../modeling/BASELINE.md#execução-e-artefatos) é a referência principal.
Instale o lockfile atualizado com `poetry install`, confira `make validate` e rode:

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment run
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment verify "<baseline_path>"
```

`make baseline` executa o treinamento e a comparação na validação. Preserve o
`run_id`, `baseline_path` e `audit_path` retornados. Uma repetição cria outra
execução independente. Falhas geram auditoria e limpam o staging; não há
promoção automática. Runs de candidatos já concluídas podem permanecer no MLflow,
sem que isso represente uma execução completa do baseline. Os dois splits entram em memória; quatro threads limitam
as rotinas numéricas. Não há avaliação de teste neste comando.

Uma falha de convergência ou de integridade exige investigação do audit. Não
contorne avisos nem altere checksums. `verify` recalcula métricas a partir dos
scores e não carrega nenhum modelo como objeto executável; aceita v1 e v2. Não comite os artefatos
sob `data/`. Compartilhe inicialmente a saída completa de run/verify, associando
posteriormente revisão, checks locais e CI aos resultados reais.

## Tracking nativo e UI

O [runbook MLflow](MLFLOW.md#execução-atual) contém os comandos atuais.
O treinamento publica os novos pipelines diretamente; a migração histórica foi
removida. `make mlflow-ui` abre a mesma UI na porta 5001, incluindo runs antigas.
Preserve `data/tracking` como armazenamento persistente, não cache.
Aplique a refatoração e valide os testes antes de decidir por um novo treinamento;
os modelos reais existentes continuam disponíveis.

## Diagnosticar a baseline existente

Use o [runbook de diagnóstico](../modeling/DIAGNOSTICS.md#executar-e-conferir) com o caminho
explícito da baseline e a URI MLflow do HGB (**Histogram-based Gradient Boosting**,
boosting de árvores baseado em histogramas). `diagnostics run` gera as métricas
diárias, permutação por AP e SHAP; `diagnostics verify` confere os outputs salvos.
O comando não retreina e não abre as partições Gold de treino/teste. Scores diferentes
dos registrados interrompem a execução. Preserve a auditoria para investigação;
não altere checksums nem treine novamente apenas para contornar uma falha de recarga.


## Preparar e retomar a busca temporal

O procedimento principal, as janelas e a recuperação estão no
[protocolo Optuna](../modeling/EVALUATION_PROTOCOL.md#otimização-temporal-do-hgb-com-optuna-v1).
`make hgb-optuna-prepare`, `make hgb-optuna-optimize DEVELOPMENT_PATH="..." NEW_TRIALS=1` e
`make hgb-optuna-verify STUDY_PATH="..."` delegam ao mesmo executor. O padrão é um novo
trial por chamada; o estudo inteiro tem até 20, incluindo falhas. Verifique após a
chamada terminar; não execute verificação concorrente com o escritor.

Preserve a Gold de desenvolvimento e `study/` juntos (banco, protocolo, tentativas,
recibos, modelos e scores), além do armazenamento MLflow. Uma falha não autoriza
apagar o histórico ou reiniciar orçamento. Código, lockfile e protocolo devem estar
versionados antes da preparação.

## Migração da organização da modelagem

Esta etapa reorganiza código, testes e documentação. Dependências, identificadores
de modelos, parâmetros e protocolos executáveis permanecem iguais. Os comandos
Make conservam seus nomes; seus destinos passam a ser explícitos.

| Entrada anterior | Entrada atual após `python -m fraud_detection_mlops.` |
| --- | --- |
| `modeling.train` | `modeling.experiments.baseline_experiment` |
| `modeling.experiments` | `modeling.experiments.terminal_feature_ablation` |
| `modeling.hgb_optuna` | `modeling.experiments.hgb_optimization` |
| `modeling.freeze` | `modeling.experiments.candidate_freeze` |
| `modeling.evaluation` | `modeling.experiments.final_holdout_evaluation` |
| `modeling.diagnostics` | `evaluation.validation_diagnostics` |
| `modeling.tracking` | `integrations.mlflow_tracking` |

Os adaptadores de transição foram retirados. Os imports e comandos anteriores
deixam de ser entradas suportadas; atualize scripts externos pelos caminhos da
tabela e pelo mapa de módulos na [arquitetura](../ARCHITECTURE.md). Os atalhos Make
e as opções das CLIs atuais continuam iguais.
O treinamento compartilhado está em `candidate_training.py`; contribuições novas
seguem o [guia de modelos](../modeling/CONTRIBUTING_MODELS.md).

Após aplicar o patch, execute `make validate` e `git diff --check`, revise e faça
commit antes de preparar dados ou ajustar candidatos. O patch não altera `data/`
nem o armazenamento MLflow. Preserve os artefatos existentes.

Para conferir o estudo já concluído, na mesma sessão em que `DEVELOPMENT_PATH`
aponta para seus dados:

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization verify \
  "$DEVELOPMENT_PATH/study"
```

O verificador usa o protocolo e os artefatos salvos; não faz fit. Uma refatoração
muda hashes de implementação, então `optimize` rejeita retomada de um estudo
preparado com outro código. Essa proteção é intencional. Não edite manifestos para
contorná-la, não reinicie orçamento nem abra setembro por causa da reorganização.
Reproduzir o ambiente histórico exige checkout da revisão registrada no manifesto
e seu lockfile. A leitura de avaliações já publicadas usa o respectivo `verify`;
congelar ou executar novamente exige as identidades originais.

Novos estudos dependem de hipótese e protocolo próprios e dados preparados com
seu código commitado. A reserva confirmatória exige protocolo separado antes do
acesso; este patch não inicia essa etapa.


## Migração de dados e features

O patch desta segunda etapa é incremental: aplique-o depois do patch de modelagem,
na raiz do checkout. Confira `git apply --check` antes de `git apply`, então execute
`make validate` e `git diff --check`. Os nomes Make e as opções das CLIs continuam
iguais; novos imports e comandos usam os caminhos abaixo.

| Entrada anterior | Entrada atual após `python -m fraud_detection_mlops.` |
| --- | --- |
| `dataset` | `data.ingestion.handbook_download` |
| `profiling` | `data.quality.transaction_profile` |
| `silver` | `data.datasets.silver_dataset` |
| `gold` | `data.datasets.gold_dataset` |
| `eda` | `data.quality.training_eda` |

Os adaptadores `bronze`, `dataset`, `silver`, `gold`, `profiling`, `eda` e `temporal`
foram retirados. Use as CLIs da tabela; funções de ingestão ficam em
`data.ingestion.handbook_bronze` e regras temporais em `data.contracts.temporal_protocol`.
`features` é um pacote: importações de constantes e de `compute_features` continuam
válidas. Novas contribuições usam
`features.feature_schema`, `features.causal_history` e `features.feature_validation`.
Os erros dos datasets ficam em `data.contracts.dataset_errors`.

As saídas mantêm schemas, ordenação, caminhos e versões. `verify` aceita datasets
históricos íntegros sem reconstrução; conferir o estudo salvo continua sendo uma
operação sem fit. Não é necessário reconstruir os dados existentes para aplicar
esta organização.

Para novas execuções, `builder_sha256`, `profiler_sha256` e `features_sha256`
incluem os arquivos das regras compartilhadas correspondentes. A identificação
combina paths relativos ao projeto e hashes dos arquivos, independentemente do
local do checkout. Manifestos e auditorias históricos não são reescritos.

Um novo `gold build` sobre uma Gold anterior pode rejeitar a identidade antiga do
código de features, embora `gold verify` continue válido. Essa rejeição protege a
reutilização: reproduza o build na revisão histórica, ou prepare uma saída em outra
raiz para uma nova execução autorizada. A refatoração também não autoriza retomar
o Optuna com código diferente, alterar recibos ou acessar a confirmação.
