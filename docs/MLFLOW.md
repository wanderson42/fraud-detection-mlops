# MLflow e persistência dos modelos

Os novos treinamentos usam `baseline_v2`: pipelines completos em skops e publicação
nativa no MLflow durante a mesma execução. A política científica continua em
`baseline_protocol_v1.json`: candidatos, features, janelas e métricas não mudaram.

## Execução atual

```bash
poetry install
make validate
poetry run python -m fraud_detection_mlops.modeling.train run
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
poetry run python -m fraud_detection_mlops.modeling.train run --no-track
```

O manifesto registra `tracking: {}` nesse caso. O padrão da CLI inclui MLflow.

## Verificação e armazenamento

```bash
poetry run python -m fraud_detection_mlops.modeling.train verify "<baseline_path>"
poetry run python -m fraud_detection_mlops.modeling.tracking verify "<model_uri>"
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

O staging local só é publicado após os controles e o tracking solicitados passarem.
Se a publicação de um candidato falhar, a auditoria registra a falha e as runs que
já concluíram. Essas runs individuais podem continuar `FINISHED`; isso não significa
que o baseline completo foi publicado. Não há transação distribuída, retomada de
migração, reutilização automática de runs ou promoção automática. Investigue a
falha antes de decidir por outro treinamento; uma repetição terá novo ID.

## Experimento histórico preservado

A migração `mlflow_skops_v1` foi executada e verificada pelo autor em 2026-10-07,
sem refit e sem avaliar o teste. [Recibo](../references/evidence/mlflow_execution_2026-10-07.json).
O código que a realizou é recuperável no Git em `6ba10b5`; foi retirado da árvore
ativa, sem uma cópia alternativa de scripts legados. Não existe mais comando `publish`
nem opção `--trust-local-models` nesta versão.

O [notebook 06](../notebooks/stages/06_mlflow_tracking.ipynb) reúne o procedimento
atual e os resultados históricos. Outputs reais da migração foram preservados como
registros estáticos em Markdown; as células executáveis usam a API atual e aguardam
execução local. A versão executada anterior é recuperável no Git. Não há notebook
07 separado, nem é necessário retreinar o baseline histórico para esta refatoração.
