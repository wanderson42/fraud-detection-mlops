# Execução das ablações controladas

O executor de `experiment_v1` está implementado. O autor concluiu e verificou
`21ccedf10d944092ba874153c1d21257` sobre `de41ee0`; o catálogo foi encerrado com
a referência preservada. A [revisão dos resultados](EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08)
distingue testes de software, execução local e decisão de desenvolvimento.
Este documento complementa o protocolo definido em
[EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md) e o catálogo em
[experiment_protocol_v1.json](../references/experiment_protocol_v1.json).

## Escopo e decisão

São três novos ajustes de HGB (**Histogram-based Gradient Boosting**, boosting
de árvores baseado em histogramas), sequenciais, com os mesmos hiperparâmetros e no
máximo quatro threads. Cada candidato usa todas as linhas de treino e validação,
com um subconjunto ordenado das 19 features da Gold. A Gold física permanece
inalterada. Os identificadores vêm do campo `id` do protocolo; tabelas e
checkpoints os registram como `candidate_id`. Os ganhos mínimos são lidos de
`minimum_absolute_ap_gain` e
`minimum_absolute_daily_customer_precision_at_100_gain`. O baseline histórico é carregado do MLflow para conferir sua
identidade e reproduzir seus scores de validação; ele não é retreinado.

| Candidato | Remoção | Features restantes |
| --- | --- | ---: |
| `without_terminal_volume` | Contagem de transações do terminal em 1 e 7 dias | 17 |
| `without_terminal_fraud_counts` | Contagem de fraudes conhecidas do terminal em 1 e 7 dias | 17 |
| `without_terminal_volume_and_fraud_counts` | Os dois grupos anteriores | 15 |

As comparações são pareadas por `TRANSACTION_ID`; os metadados e rótulos precisam
ser idênticos. O gate exige simultaneamente os ganhos definidos no protocolo
para AP e a média diária de precisão por cliente nos primeiros 100 alertas.
Ele indica um candidato **para revisão**, sem promoção automática, sem avaliação
do teste e sem refit em validação. Não é uma estimativa de retorno financeiro.
Empates são resolvidos por AP, precisão operacional, menor número de features e ID.
Na ausência de candidato elegível, permanece a referência.

A análise exclui cada dia de validação por vez e recalcula as métricas sobre os
scores já obtidos. Não retreina modelos. AP é calculada nas transações dos dias
restantes; precisão operacional é a média das métricas diárias restantes. Dias
com apenas uma classe têm AP indefinida para esta comparação. Essa análise mede
influência, não produz intervalos de confiança nem demonstra superioridade
estatística: clientes, terminais e transações sucessivas podem ser dependentes.
O catálogo foi motivado pelo diagnóstico na mesma validação; o estudo é exploratório.

## Aplicação e preparação

A implementação e o protocolo estão no checkout publicado em `de41ee0`.
O notebook 08 registra o catálogo e a interpretação; a baseline histórica é
reutilizada. Validar o ambiente antes de iniciar um estudo novo:

```bash
poetry install
make validate
git diff --check
```

Revise e faça um commit local **antes dos novos treinos**. O executor rejeita
código de produção, lockfile e contratos/protocolos não versionados ou diferentes
do HEAD. Esse commit fixa a política e a implementação; a evidência da execução
real pode ser registrada em um commit posterior. Um push não é pré-requisito.
Não é necessário incluir dados locais no Git.

## Execução local

```bash
BASELINE_PATH="$(pwd)/data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/baseline_v1/f6d7aca720f74316b4183f97b6d866ab"

poetry run python -m fraud_detection_mlops.modeling.experiments run "$BASELINE_PATH"
```

Use o ambiente e o MLflow locais já existentes. O comando verifica Bronze/Gold,
a baseline e a paridade dos scores antes de ajustar candidatos. A validação de
integridade da Gold pode ler arquivos do teste para conferir o contrato; o loader
de modelagem aceita somente `train` e `validation`, sem treinar ou calcular
métricas de teste. Mudanças nas versões de scikit-learn, NumPy ou pandas em relação
à referência são rejeitadas para evitar uma comparação silenciosamente diferente.

O comando imprime `Experiment: <caminho>` logo no início. Guarde esse caminho:

```bash
EXPERIMENT_PATH="$PWD/data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/experiment_v1/21ccedf10d944092ba874153c1d21257"
poetry run python -m fraud_detection_mlops.modeling.experiments verify "$EXPERIMENT_PATH"
```

`verify` confere checksums e recalcula as tabelas sem carregar modelos, acessar o
MLflow ou consultar Gold. Uma execução concluída contém 18 artefatos registrados,
mais `manifest.json` e `state.json`.

| Artefato | Finalidade |
| --- | --- |
| `protocol.json` | Cópia byte a byte da política usada |
| `reference_predictions.parquet` | Scores e população da referência |
| `summary.csv` | Efeitos globais e elegibilidade para revisão |
| `daily.csv` | Métricas diárias, efeitos e clientes fraudulentos perdidos |
| `leave_one_day_out.csv` | Sensibilidade à exclusão de cada dia |
| `report.json` | Decisão de desenvolvimento e identidade da execução |
| `models/<candidato>/` | Pipeline skops, scores, métricas e checkpoint |
| `state.json` | Tentativas e consumo do orçamento de ajustes |

No MLflow cada candidato tem uma run principal no experimento
`fraud-controlled-ablation-v1`, com suas features na assinatura, política,
previsões, métricas, parâmetros, tempos e tamanho do modelo. O carregamento skops
mantém a lista explícita de tipos revisados e exige paridade de scores após
recarga. O formato não dispensa a confiança na origem do arquivo.

## Retomada

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments run \
  "$BASELINE_PATH" --resume "$EXPERIMENT_PATH"
```

A retomada exige a mesma identidade de política, código, dependências, dados,
referência e store. Modelos com checkpoints válidos são reutilizados. Runs já
finalizadas são verificadas e reutilizadas, inclusive quando o processo caiu
antes de salvar o recibo local. Runs de publicação que falharam ficam no histórico;
uma nova tentativa de publicação não repete o ajuste do modelo.

Cada ajuste iniciado consome uma das três posições do orçamento. Uma interrupção
durante o ajuste, antes do checkpoint, pode esgotar o orçamento: investigue antes
de abrir outro estudo. Não remova estados para contornar esse limite. Uma trava
local impede duas execuções simultâneas do mesmo estudo.

## Leitura no notebook 08

Estas células leem os resultados da execução escolhida, sem novos treinos:

```python
from pathlib import Path
import json
import pandas as pd

experiment = Path("/caminho/impresso/experiment_v1/<run_id>")
report = json.loads((experiment / "report.json").read_text())
print(json.dumps(report, indent=2))
summary = pd.read_csv(experiment / "summary.csv")
daily = pd.read_csv(experiment / "daily.csv")
influence = pd.read_csv(experiment / "leave_one_day_out.csv")
display(summary, daily, influence)
```

A [revisão concluída](EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08)
conserva a referência. Os resultados históricos em Markdown vêm dos arquivos
compartilhados pelo autor; não são outputs fabricados de células executadas.
A avaliação final e decisões de implantação exigem etapas próprias.
