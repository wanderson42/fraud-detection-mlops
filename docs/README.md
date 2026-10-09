# Índice da documentação

Leia o [README do projeto](../README.md) para começar e a
[arquitetura](ARCHITECTURE.md) para localizar responsabilidades.

| Pergunta | Referência principal |
| --- | --- |
| Qual problema e quais próximos marcos? | [Contexto](project/PROBLEM_CONTEXT.md), [mural](project/ROADMAP.md), [stakeholders](project/STAKEHOLDERS.md) |
| Como mantemos a documentação? | [Política editorial](project/DOCUMENTATION_POLICY.md) |
| Como dados e features são construídos? | [Pipeline](data/DATA_PIPELINE.md), [Silver](data/SILVER_CONTRACT.md), [Gold](data/GOLD_CONTRACT.md) |
| O que significam os campos e os achados? | [Dicionário](data/DATA_DICTIONARY.md) e [EDA](data/EDA.md) |
| Como adicionar um modelo? | [Contrato e contribuição](modeling/CONTRIBUTING_MODELS.md) |
| Como avaliamos e selecionamos? | [Baseline](modeling/BASELINE.md), [avaliação temporal/Optuna](modeling/EVALUATION_PROTOCOL.md), [ablações](modeling/EXPERIMENT_PROTOCOL.md) |
| O que concluímos em setembro e qual janela continua reservada? | [Resultado e limites](modeling/EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento); [protocolo original](modeling/EVALUATION_PROTOCOL.md#protocolo-estatístico-da-referência--v1) |
| O que dizem os diagnósticos e limites do modelo? | [Diagnóstico](modeling/DIAGNOSTICS.md) e [Model Card](modeling/MODEL_CARD.md) |
| Como executar, recuperar e migrar? | [Operações](operations/OPERATIONS.md), [ablações](operations/EXPERIMENT_EXECUTION.md), [MLflow](operations/MLFLOW.md) |
| Como verificar uma mudança? | [Testing](operations/TESTING.md) |
| Como exportar, iniciar e conferir a API de pontuação? | [Serving](operations/SERVING_CONTRACT.md) |

Os protocolos executáveis e recibos históricos estão em
[`references/`](../references/). Os notebooks mantêm narrativa e resultados
selecionados; o runbook é a referência dos procedimentos reutilizáveis.
