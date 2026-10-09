# Serving de laboratório: contrato e execução

`precomputed_features_v1` recebe features prontas em `POST /score`. API, exportação
sem refit e wheel fora do checkout foram conferidos com dados sintéticos.
A exportação da referência no Alienware e Docker permanecem checks pendentes.
O gate offline autoriza revisão de laboratório; não promove o modelo em produção.

O [escopo estatístico exploratório](../modeling/EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento)
foi fechado após revisar a reamostragem nativa em 2026-10-09, mantendo explícita
a ausência de cobertura de generalização demonstrada. A próxima execução é a
exportação abaixo; preservar seu recibo para conferir a identidade na API e na
imagem Docker.

## Entradas, saída e responsabilidade

| Elemento | Contrato implementado |
| --- | --- |
| Versão | `schema_version: 1` e `feature_contract_version: gold_v1` |
| Identidade do evento | `transaction_id`, `customer_id` e `terminal_id`: inteiros não negativos, metadados de correlação; não são preditores |
| Relógio | `tx_datetime` e `feature_as_of` iguais, em ISO 8601 sem offset, preservando o relógio simulado sem timezone; rejeitar conversões silenciosas |
| Features | Objeto `features` com exatamente as 19 chaves de `FEATURE_COLUMNS`; construir a matriz na ordem canônica, independentemente da ordem do JSON |
| Validação | Rejeitar ausências, extras, strings numéricas, booleanos, nulos e valores não finitos; manter tipos, domínios e coerências do contrato Gold |
| Fronteira dos rótulos | `TX_FRAUD`, `TX_FRAUD_SCENARIO` e `LABEL_AVAILABLE_AT` não pertencem à requisição de pontuação |
| Saída | `schema_version`, `transaction_id`, `score`, `score_semantics: uncalibrated_ranking`, `model_id`, `model_sha256`, `feature_contract_version` e identificador da release de serving |
| Score | Valor finito entre 0 e 1; sem classe, limiar automático, decisão de bloqueio ou seleção dos 100 clientes |
| Estado | Pontuação sem estado; históricos e rótulos atrasados são responsabilidade do produtor de features |

Domínios e relações seguem o [contrato da Gold](../data/GOLD_CONTRACT.md) e o
[dicionário](../data/DATA_DICTIONARY.md). Preservamos valores monetários zero; hora vai de
0 a 23 e dia ISO de 1 a 7. Contagens são inteiras não negativas, flags são 0/1 e
razões/taxas usam a mesma convenção de denominador zero e tolerâncias da Gold.
Não reinterpretamos unidades simuladas como moeda real.

O produtor deve usar eventos anteriores a `t` e rótulos com disponibilidade
estritamente anterior a `t`, incluindo o atraso de sete dias. Declarar
`feature_as_of = tx_datetime` não comprova causalidade: os testes de paridade do
produtor terão de demonstrá-la. Receber features prontas por HTTP demonstra o
serviço de pontuação; não demonstra ainda um pipeline de features online.

A fila de investigação diária é outra responsabilidade. Não incorporamos uma
política retrospectiva de dia completo ao endpoint como se fosse autorização
instantânea de pagamento. A futura fila por eventos terá contrato próprio.

IDs e contagens respeitam os inteiros físicos da Gold. Os relógios admitem até
nove dígitos fracionários; hora e dia da semana devem concordar com a transação.
Erro de entrada retorna HTTP 422 com campos/tipos de erro, sem ecoar os valores.

## Exportar a referência existente

Execute na raiz do projeto, depois de `poetry install` e `make validate`.
Preserve Gold, predições de validação, avaliação final e tracking originais.
O caminho abaixo identifica a avaliação já informada pelo autor:

```bash
EVALUATION_PATH="data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/final_evaluation_v1/6281337ac9ad872947470d94de0307d7c1934365538877376559d3dee2e57120"
poetry run python -m fraud_detection_mlops.integrations.serving_release_export export \
  "$EVALUATION_PATH" --project-root . --output data/serving/reference-v1 \
  > data/serving-export.json
cat data/serving-export.json
```

O exportador verifica os outputs **já salvos** da avaliação, os hashes congelados
do MLflow e a assinatura de features. Compara todas as linhas da validação fixada
após ida e volta por JSON, com tolerâncias absoluta/relativa de `1e-12` nos scores.
Preserva os bytes skops; não ajusta, não cria runs nem pontua novamente o teste.
Não consulta a reserva de setembro. Os hashes históricos de código no freeze
permanecem históricos: não executamos `verify-freeze` na árvore refatorada para
validar bytes antigos, nem alteramos o recibo para contornar essa diferença.

A distribuição contém `pipeline.skops`, `smoke.json` (um caso da validação,
sem rótulos) e `manifest.json`: identidade da release, origem, hashes, contrato,
quantidade de linhas comparadas e versões exatas do runtime. Esses arquivos são
operacionais, em `data/`, fora do Git. Guarde o JSON de exportação junto ao modelo;
o SHA-256 do manifesto será a identidade fixada para a carga.

O diretório de destino deve ser novo. Uma falha não autoriza refit: confira o erro,
restaure artefatos/versões originais e use outro destino após revisar uma exportação
incompleta. Nunca sobrescreva uma release utilizada. Uma cópia byte a byte, com
seu manifesto e SHA-256 fixado, pode ser transportada para outro caminho.

## Iniciar e conferir HTTP

```bash
export FRAUD_SERVING_RELEASE="$(poetry run python -c 'import json; print(json.load(open("data/serving-export.json"))["release_path"])')"
export FRAUD_SERVING_MANIFEST_SHA256="$(poetry run python -c 'import json; print(json.load(open("data/serving-export.json"))["manifest_sha256"])')"
poetry run uvicorn fraud_detection_mlops.serving.http_service:create_app \
  --factory --host 127.0.0.1 --port 8000 --workers 1
```

Em outro terminal, com as mesmas variáveis:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8000/info
poetry run python -c 'import json, os; from pathlib import Path; print(json.dumps(json.loads((Path(os.environ["FRAUD_SERVING_RELEASE"]) / "smoke.json").read_text())["request"]))' \
  | curl --fail --json @- http://127.0.0.1:8000/score
```

O modelo carrega uma vez no lifespan do serviço; hashes dos bytes, tipos aprovados,
ordem das features, versões e score de controle são conferidos antes de ficar
pronto. SHA incorreto, modelo ausente/alterado ou ambiente incompatível abortam a
inicialização. `/health` indica processo vivo; `/ready` exige modelo carregado;
`/info` expõe identidade e escopo. Antes da carga, pontuação retorna 503.
Uma falha do contrato de saída também retorna 503. Há quatro threads numéricas
e uma predição por vez por processo. Use um worker nesta etapa.

## Wheel e runtime de inferência

Poetry conserva a instalação padrão completa. `main` contém inferência;
`pipeline` contém ferramentas offline e `dev`, checks. Nenhuma versão previamente
fixada foi alterada pela divisão. O ambiente de exportação mantém MLflow; o
runtime HTTP não depende dele nem dos arquivos de `references/`.

```bash
make validate-serving-wheel \
  RELEASE_PATH="$FRAUD_SERVING_RELEASE" \
  MANIFEST_SHA256="$FRAUD_SERVING_MANIFEST_SHA256"
```

O check constrói o wheel, cria um ambiente temporário separado, confirma o alvo
Poetry **antes** de sincronizar apenas `main`, instala o wheel sem dependências
adicionais e inicia Uvicorn fora do checkout. Confere paridade HTTP e rejeição de
ID inválido, com MLflow, SHAP, DuckDB, Matplotlib, Optuna e Typer ausentes.
Isso não empacota os protocolos externos dos workflows offline.

Para exercitar apenas a infraestrutura sem artefatos reais:

```bash
poetry run python examples/serving_smoke.py --output data/serving/synthetic-v1
```

Esse exemplo ajusta um HGB em 160 registros gerados em memória e marca a release
como `synthetic_smoke`. Não é a referência; preserve essa identificação no relato.

## Evidências e próximo marco

[Preparação](../../references/evidence/laboratory_serving_preparation_2026-10-09.json):
paridade HTTP sintética e exportação nativa MLflow/skops em 160 linhas de validação,
com fit bloqueado e partições de treino/teste removidas antes da exportação.
O wheel pontuou num runtime isolado. A avaliação final original continua preservada.

Docker não foi construído nesta preparação. A próxima entrega deve conferir imagem
identificada, usuário sem root, modelo somente leitura, recuperação e medições de
tamanho, memória e latência. O serviço atual usa loopback e não define autenticação,
TLS, fila de investigação ou objetivo de disponibilidade para produção.
