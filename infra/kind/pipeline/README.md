# Runtime dos Jobs de dados

`Dockerfile.science`: produtores/verificadores existentes e subconjunto Linux
Python 3.14 do `poetry.lock`, com hashes. Sem SDK S3, treinamento ou MLflow.
`Dockerfile.transport`: SDK já travado, download/publicação de bundles e recibos.

O controlador constrói/carrega ambas no node dedicado, registra fontes e
identidades CRI e recusa fontes/node divergentes. Contextos Docker excluem
`data/`, credenciais, Git e arquivos externos ao conjunto explícito.

Instalação dos Jobs: chart `infra/helm/fraud-data-jobs`.
Operação e limites: `docs/operations/KIND_PIPELINE.md`.
