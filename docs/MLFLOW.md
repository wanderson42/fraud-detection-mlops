# MLflow e persistência dos modelos

Política: `mlflow_skops_v1`. Publicação e verificação do baseline real concluídas
pelo autor em 2026-10-07, com três runs próprias. Evidência:
[execução local](../references/evidence/mlflow_execution_2026-10-07.json).
Commit e CI desta integração ainda não informados; validação da UI real pendente.
Esta entrega é uma migração dos três modelos já ajustados, sem treinamento.

## Decisão e escopo

O baseline real [registrado pelo autor](../references/evidence/baseline_validation_2026-10-07.json)
é a referência histórica. Preservamos `baseline_v1`, seus modelos joblib, scores,
manifesto e auditoria. Uma run principal por candidato recebe o pipeline completo
(incluindo scaler para regressão), configuração, métricas e artefatos próprios.
Não há parent run nem mistura dos modelos. O experimento é `fraud-temporal-baseline-v1`.

A exportação produz um MLflow Model com flavors sklearn e pyfunc, formato **skops**
explícito e assinatura das 19 features float64. `predict_proba` retorna duas colunas
na ordem `[0, 1]`; a coluna 1 é o score de fraude. Esses scores ainda não são
probabilidades calibradas. Não há limiar, serving HTTP nem promoção para champion.

O código usa logging explícito pelo `MlflowClient`, sem autolog ou fit. `save_model`
cria o pacote padrão MLflow; `log_artifacts` publica esse pacote em `model/` na run.
Ele pode ser carregado por `runs:/<run_id>/model`. Não usamos um objeto pyfunc
customizado serializado em cloudpickle. Registry e promoção terão política própria.

## Backend e armazenamento

| Local | Responsabilidade |
| --- | --- |
| `data/tracking/mlflow.db` | SQLite: experimentos, runs, parâmetros, métricas e tags |
| `data/tracking/artifacts/` | Artefatos locais referenciados pelo MLflow |
| `data/tracking/exports/<baseline_manifest_sha256>.json` | Mapa do baseline para as três runs e ambiente da exportação |
| `data/tracking/publish.lock` | Exclusão mútua entre publicações/verificações neste armazenamento |

O SDK acessa SQLite diretamente: publicar não depende de um servidor/UI em execução.
A UI usa um worker em `127.0.0.1:5001`, evitando a porta 5000 do projeto
energia. Tudo fica sob `data/`, ignorado pelo Git. Esta fase não exige Docker,
PostgreSQL, S3 ou cloud; esses componentes entram quando houver execução distribuída.

MLflow 3.17.0, skops 0.16.0 e filelock 4.0.12 estão fixados no lockfile. O filelock
já existia como dependência transitiva; passa a ser dependência explícita de runtime.
As 73 versões anteriores foram preservadas. MLflow acrescentou 63 pacotes ao lockfile;
seus extras de cloud, Kubernetes, gateway e GenAI não foram solicitados. Usamos o
pacote completo para ter backend SQLite e UI suportados no mesmo ambiente.

## Publicar a execução existente

Na raiz do projeto, após instalar e validar o patch:

```bash
poetry install
make validate

BASELINE_PATH="data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/baseline_v1/f6d7aca720f74316b4183f97b6d866ab"

poetry run python -m fraud_detection_mlops.modeling.tracking publish \
  "$BASELINE_PATH" --trust-local-models

poetry run python -m fraud_detection_mlops.modeling.tracking verify \
  "$BASELINE_PATH"
```

O argumento `--trust-local-models` confirma que os joblib são os próprios modelos
criados localmente. Joblib usa pickle: **a conversão exige carregá-los uma vez e
pode executar código**. Checksums comprovam integridade em relação ao manifesto,
não autenticidade ou segurança de um arquivo recebido de terceiros. A flag existe
por essa fronteira concreta; skops no destino não torna a leitura inicial segura.

Exigimos o Python e as versões de sklearn, numpy, pandas, joblib, pyarrow e duckdb
registrados pelo baseline. As dependências adicionadas preservam essas versões.
Não removemos esse controle para contornar incompatibilidades: nesse caso, restaure
o ambiente original. Skops também exige um ambiente compatível para recarga.

A publicação copia e verifica um snapshot do baseline antes de desserializar seus
modelos. Confere o manifesto da Gold de origem e checksums das partições de validação,
compara seus metadados com os scores e carrega somente features de **validação**.
Não lê features de treino/teste, não recalcula a Gold nem revalida toda a Silver.

O pacote skops possui uma allowlist explícita de quatro tipos internos revisados
do HistGradientBoosting. Tipos adicionais impedem a exportação/verificação: não
aceitamos automaticamente a lista retornada por `get_untrusted_types`. Mudanças de
sklearn/skops precisam revisar essa compatibilidade.

Para cada candidato, conferimos pipeline, classes, configuração e os scores de
toda a validação após recarga skops e após recarga pyfunc do pacote gravado no
MLflow. Tolerância numérica: `rtol=atol=1e-12`. A assinatura contém nomes/tipos, sem
input example e sem IDs ou alvo como preditores.

As métricas globais usam prefixo `validation_`; tempos `fit_seconds`/`predict_seconds`
são os históricos do baseline, não novos tempos de treinamento. O horário da run
MLflow corresponde à migração; a tag `baseline_created_at_utc` registra o marco
original. Manifesto e hashes distinguem código/lockfile de treinamento do ambiente
da exportação. Não antecipamos hashes de commits ainda não publicados.

## UI e verificação

Em outro terminal:

```bash
make mlflow-ui
```

Abra <http://127.0.0.1:5001> e o experimento `fraud-temporal-baseline-v1`. Compare
`validation_average_precision`, `validation_roc_auc` e
`validation_daily_customer_precision_at_100`. Cada run tem apenas um pipeline.
A tag `selected_on_validation` identifica o candidato do relatório, sem promoção.
Encerre a UI com Ctrl+C; isso não apaga o banco nem os modelos.

`verify` não carrega joblib: lê o baseline para reconciliar métricas/identidades,
baixa os modelos do armazenamento local e valida as recargas skops/pyfunc. Não
retreina, não calcula métricas de teste e não substitui uma avaliação estatística.

CLI: `publish`, `verify` e `ui`. `publish`/`verify` aceitam `--tracking-root`,
`--gold-root`, `--inventory` e `--protocol`. `ui` aceita `--tracking-root` e `--port`.
Use as mesmas referências da execução; não há escolha automática da última run.

## Retomada, falhas e limites

A identidade de importação é o SHA-256 do manifesto do baseline, a versão da
política e o candidato. Uma repetição confere e reutiliza runs `FINISHED`, sem
recarregar seus joblib. Runs parciais não são reutilizadas. Falhas após criar uma
run são marcadas `FAILED` ou `KILLED`; só concluímos depois de conferir metadados,
métricas e modelos publicados. O recibo completo é escrito depois dos três modelos.

Se o segundo modelo falhar, o primeiro permanece concluído. Corrija a causa e repita
o comando: o primeiro é reutilizado e os demais são publicados. Não há rollback de
runs históricas. Uma queda abrupta do processo pode deixar uma run `RUNNING`; ela
não é reutilizada. Investigue antes de encerrá-la manualmente na UI.

O lock impede publicações concorrentes neste diretório. Duplicatas concluídas ou
artefatos incompatíveis interrompem o processo; não escolhemos nem sobrescrevemos
silenciosamente uma run. O mecanismo é para um laboratório local, não uma garantia
de execução exatamente uma vez em sistemas distribuídos.

Preserve juntos banco, artefatos e recibos, além da Gold e do baseline. Para um
backup consistente nesta etapa, pare a UI e as publicações antes de copiar o
armazenamento. Os caminhos de artefatos são absolutos: mover o checkout para outra
máquina/diretório exige revisar essas referências. Não apagar `data/tracking` como
cache; `make clean` não remove esse armazenamento.

`baseline_v1` continua como runner histórico em joblib. Esta entrega converte os
modelos existentes; o próximo contrato de treinamento deverá produzir skops desde
a origem. Não refatoramos o baseline validado nem duplicamos treinamento para obter
uma interface de tracking. Otimização, SHAP, testes de hipóteses, quality gate,
Prefect e atualização de modelos estão planejados em entregas separadas.

## Evidências e referências

O [recibo de preparação](../references/evidence/mlflow_preparation_2026-10-07.json)
registra 135 testes aprovados, lint, formatação e lockfile, além de HTTP 200 na UI/API
com fixtures. Isso não é publicação do baseline real nem CI do autor.

Os [oito testes de tracking](../tests/test_tracking.py) usam modelos e SQLite reais
com dados controlados. Cobrem runs separadas, scores, reutilização, fronteira temporal,
originais preservados, confiança da migração, versões, corrupção, tipos desconhecidos,
falha parcial/retomada e exclusão mútua. Warnings de depreciação internos ao MLflow
são registrados sem ocultação; não representam falha de nossos contratos.

- [MLflow sklearn: persistência, assinatura e predict_proba](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.sklearn.html).
- [Backend stores](https://mlflow.org/docs/latest/tracking/backend-stores/).
- [Persistência sklearn](https://scikit-learn.org/stable/model_persistence.html).
- [Persistência skops e tipos confiáveis](https://skops.readthedocs.io/en/stable/persistence.html).
