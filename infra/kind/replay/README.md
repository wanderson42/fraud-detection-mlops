# Runtime do ensaio de retomada

Imagem separada com Python 3.14.4 por digest e subset de dependências do
`poetry.lock` instalado com hashes e `pip check`. Inclui o executor existente e
a fixture sintética; não contém dados, release histórica ou credenciais.
Não instala MLflow, Optuna, SHAP, DuckDB ou SDK S3.

Use `python -m scripts.platform.kind_replay build`; o controlador carrega a imagem
no node dedicado e registra suas fontes. Não reutiliza ou reconstrói imagens de
marcos anteriores. Não altere fontes vinculadas após o build antes do gate.
