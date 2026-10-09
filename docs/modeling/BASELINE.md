# Baseline temporal de classificação

Persistência atual: `baseline_v2`; política científica: `baseline_protocol_v1.json`.
O baseline real `baseline_v1` foi preservado. Baseline/tracking publicados em
[`6ba10b5`](https://github.com/wanderson42/fraud-detection-mlops/commit/6ba10b53464500a70cfba53dc696bfbca0119492), com
[CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37706499277).
A simplificação atual foi preparada sobre essa revisão e tem validação própria.
Evidência do resultado real anterior:
[recibo da validação](../../references/evidence/baseline_validation_2026-10-07.json). Contrato: [baseline_protocol_v1.json](../../references/baseline_protocol_v1.json).

## Pergunta e desenho do primeiro experimento

**HGB = Histogram-based Gradient Boosting**, ou boosting de árvores baseado em
histogramas. Usamos `HistGradientBoostingClassifier`, do scikit-learn: um ensemble
de árvores ajustadas sequencialmente. Os parâmetros desta referência estão abaixo.

Quanto os 19 candidatos da Gold ajudam a ordenar transações futuras por risco?
O primeiro experimento usa a Gold e o [protocolo temporal](EVALUATION_PROTOCOL.md)
sem alterar janelas, população ou features. Treino: 1 a 28 de abril de 2018;
validação: 6 a 12 de maio. O teste de 20 a 26 de maio ficou reservado durante
esta etapa; foi consumido depois pela avaliação final, registrada na
[Model Card](MODEL_CARD.md). A referência também foi avaliada em 02–15/09,
com diagnóstico e reamostragem exploratória concluídos; 16–30/09 permanece
reservado para replay. [Resultado e limites](EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento).

| Referência | Implementação | Propósito |
| --- | --- | --- |
| `dummy_prior` | `DummyClassifier(strategy="prior")` | Score constante igual à prevalência do treino; controle sem sinal, excluído da seleção |
| `logistic_regression` | `StandardScaler` + `LogisticRegression` | Referência linear com L2 (`C=1`, `l1_ratio=0`), solver lbfgs e até 1.000 iterações |
| `hist_gradient_boosting` | `HistGradientBoostingClassifier` | Referência não linear: 100 iterações, 15 folhas, mínimo 50 exemplos por folha, regularização L2 de 1 e taxa de aprendizado 0,1 |

Os dois classificadores usam `class_weight="balanced"`, calculado a partir dos
rótulos do treino. Nenhuma transação é reamostrada ou removida. Esses pesos são uma
escolha prévia do primeiro contrato, não uma configuração ótima demonstrada.
A escala da regressão é ajustada somente no treino e preservada dentro do pipeline.
O gradient boosting usa `early_stopping=False`: não cria uma validação aleatória
interna. Todos os candidatos usam os mesmos exemplos de treino e validação.
A semente é 42 e o limite de threads é quatro.

Não há busca de hiperparâmetros, seleção aprendida de features, calibração ou
escolha de limiar nesta entrega. As saídas de `predict_proba` são usadas como scores
de ranking; não demonstram probabilidades calibradas de fraude, especialmente
com pesos de classes. Os nomes dos candidatos e o desempate são fixados antes da
execução. Mudanças dessa política exigem outro contrato de experimento.

## Fronteira temporal e causalidade

O builder verifica a Gold inteira, inclusive checks estruturais e semânticos das
partições de teste. Depois, o loader de treinamento permite somente `train` e
`validation`; não carrega exemplos de teste para fit, scores ou métricas.
Esta distinção é relevante: verificar integridade de um holdout não equivale a
avaliar um modelo nele.

Antes do fit, todos os rótulos do treino precisam estar disponíveis estritamente
antes do começo da validação. O treino precisa conter ambas as classes. Também
exigimos ambas na validação para evitar selecionar um ranking sem comparação
entre positivos e negativos. As features precisam ser finitas; IDs dos dois splits
não podem se sobrepor. A allowlist da Gold define exclusivamente `X`.

Os intervalos de feedback já foram incorporados às features pela Gold. O modelo
não ajusta scaler, pesos de classe ou parâmetros com dados da validação. Ela é
usada para comparar candidatos. A Gold, seu contrato e o protocolo temporal não
são modificados. O futuro teste usará o modelo já ajustado no treino, sem refit.

## Métricas e seleção

- **Average Precision (AP)** de transações é a métrica principal. Usamos a
  implementação do scikit-learn; não substituímos AP por integração trapezoidal
  da curva precision-recall.
- **ROC AUC** de transações é secundária. A função de métricas retorna `null` se
  houver somente uma classe; o runner exige duas para a comparação global.
- **Precisão diária nos 100 clientes de maior risco** segue `temporal_v1`: máximo
  score por cliente/dia e rótulo positivo se houver qualquer fraude nesse dia,
  mesmo que a fraude esteja numa transação de score menor. Desempate por ID do
  cliente crescente. Denominador `min(100, clientes observados)`; média simples
  das precisões diárias, sem ponderação pelo número de transações.

O relatório inclui linhas, fraudes e prevalência da validação para contextualizar
AP. O controle constante produz AP igual à prevalência da janela avaliada e ROC AUC
0,5 quando há ambas as classes. Sua ordem de alertas depende do desempate por ID:
não tratamos sua precisão diária como ranking aleatório esperado.

A seleção considera somente os dois modelos com features, por AP decrescente.
Empates exatos usam `model_id` crescente, sem métrica secundária de desempate.
O relatório informa se o selecionado supera a AP do controle; se não superar,
continua registrando a comparação, sem promover o resultado a produção.
"Selecionado" significa melhor AP entre estes candidatos nesta validação.
Uma única janela não demonstra estabilidade futura nem superioridade estatística.

Também registramos a acurácia contrafactual de prever tudo como genuíno e recall
zero. Não calculamos precision/recall de um classificador com limiar 0,5 por hábito.
Limiar, matriz de confusão, calibração e custos operacionais terão análise própria.

## Execução e artefatos

Na raiz do projeto, instale o lockfile atualizado e execute:

```bash
poetry install
make validate
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment run
```

Atalho: `make baseline`. O comando imprime progresso por candidato e, ao terminar,
o resultado com `baseline_path`, `run_id`, comparação, `audit_path` e URIs MLflow.
A publicação nativa dos três candidatos faz parte da execução padrão; consulte
[MLflow](../operations/MLFLOW.md). Não existe mais uma etapa posterior de migração.
Para verificar, use o caminho exato da execução, sem escolher automaticamente a
última pasta:

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.baseline_experiment verify \
  "data/processed/handbook/<source_commit>/baseline_v2/<run_id>"
```

Opções comuns: `--inventory`, `--silver-contract`, `--protocol`, `--gold-contract`
e `--config`. `run` também aceita `--gold-root`, `--output-root`,
`--tracking-root` e `--no-track` para investigação explicitamente sem tracking. Mantenha os
contratos da construção ao verificar. Não há opção de avaliação do teste nesta CLI.

Cada execução nova tem UUID e permanece independente. Dentro de
`data/processed/handbook/<source_commit>/baseline_v2/<run_id>/`:

| Artefato | Conteúdo |
| --- | --- |
| `models/<model_id>/model.skops` | Um único pipeline ajustado por modelo, incluindo o scaler quando necessário |
| `models/<model_id>/validation_predictions.parquet` | Metadados da validação, alvo e score; sem exemplos de teste |
| `models/<model_id>/metrics.json` | Configuração, métricas, resultados diários, features, linhas e tempos de fit/predict |
| `report.json` | Comparação, candidato selecionado, controle, regras de seleção e `test_evaluated: false` |
| `manifest.json` | Dez outputs com tamanhos/SHA-256, Gold de origem, contratos, ambiente, lockfile e hashes do código |

A auditoria fica em `data/processed/handbook/<source_commit>/runs/baseline_<run_id>.json`.
A publicação ocorre após completar e verificar o staging. Falhas deixam auditoria
com erro e removem o staging; não publicam comparação parcial como sucesso.
Repetir o comando cria outro experimento, sem sobrescrever o anterior. Execuções
concorrentes têm pastas próprias, mas compartilham os recursos da máquina.

Os dados e modelos ficam sob `data/`, já ignorado pelo Git. A execução atual
rastreia modelos pelo MLflow nativo e liga as URIs ao manifesto local. A migração
histórica foi retirada do código ativo; seus artefatos permanecem preservados.
Orquestração terá escopo próprio.

## Verificação, ambiente e limites

`verify` confere os dez outputs, schema dos scores, identidades, cobertura de datas,
populações idênticas entre candidatos e relações de disponibilidade. Recalcula
métricas a partir do Parquet, reconcilia o relatório e o desempate. Não desserializa
modelos, não refaz o treinamento nem consulta os Parquets Gold originais.
Também verifica artefatos históricos `baseline_v1` com joblib sem carregá-los.
Verificação de checksum não é autenticação de origem nem prova de valor preditivo.

O runner carrega ambos os splits em memória, ajusta os três pipelines e confere
scores após carregar seus próprios artefatos skops recém-gravados e o modelo nativo
MLflow. Use o lockfile; os tipos permitidos são revisados explicitamente.
A CLI de baseline `verify` lê somente bytes e Parquets, sem executar modelos.
A [API de serving](../operations/SERVING_CONTRACT.md) recebe features prontas e
tem checks sintéticos e validação nativa do wheel com a referência exportada.
Docker e o cálculo online das features ainda exigem suas verificações próprias.

Uma `ConvergenceWarning` interrompe o experimento; não registramos regressão sem
convergência como comparação bem-sucedida. Investigue a auditoria e os inputs antes
de mudar o contrato. Falhas anteriores à inicialização da auditoria, como configuração
não suportada, não produzem recibo de execução. Os tempos do fit e da previsão são
medidos, mas uso máximo de memória e custo de produção ainda não foram medidos.

Os testes controlados conferem métricas com exemplos conhecidos, cliente/dia,
empates, denominador, macro média, ausência de classes, escala exclusivamente no
treino, exclusão do teste, invariância dos modelos aos rótulos dos holdouts,
persistência, corrupção, falhas, alteração de inputs e CLI. Evidência:
[recibo de preparação](../../references/evidence/baseline_preparation_2026-10-07.json).
Não representa execução de modelo nos 402.877 exemplos reais da Gold.

Referências primárias:

- [Scikit-learn: evitar vazamento e usar pipelines](https://scikit-learn.org/stable/common_pitfalls.html).
- [StandardScaler](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.StandardScaler.html).
- [LogisticRegression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html).
- [HistGradientBoostingClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html).
- [Average Precision](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html).
- [Persistência de modelos](https://scikit-learn.org/stable/model_persistence.html).

## Resultado real informado pelo autor

Execução `f6d7aca720f74316b4183f97b6d866ab`; validação com 67.255 transações e
580 fraudes (prevalência de 0,8624%). Treinamento e `verify` concluídos com dez
outputs; teste não avaliado. Outputs reais preservados no [notebook](../../notebooks/stages/05_temporal_baseline.ipynb).

| Modelo | AP | ROC AUC | Precisão diária nos 100 clientes |
| --- | --- | --- | --- |
| Controle constante | 0,008624 | 0,500000 | 2,14% |
| Regressão logística | 0,435001 | 0,873168 | 48,00% |
| HistGradientBoosting | 0,623949 | 0,884953 | 54,00% |

O gradient boosting foi selecionado por AP. A diferença para a regressão foi de
0,188948 em AP e seis pontos percentuais na precisão diária por cliente. AP de
0,623949 não significa acurácia ou precisão de 62,39% em um limiar de classificação.
A precisão diária do selecionado variou de 45% a 60% nos sete dias: evidência
exploratória, sem teste de superioridade ou demonstração de estabilidade futura.

O autor informou 127 testes aprovados em 6,71 s e tox em 8,44 s (`make validate`),
com lockfile, lint, formatação e `git diff --check` aprovados. Isso é evidência local,
não CI consultada. Os arquivos nativos de modelos, scores, manifesto e auditoria
não foram enviados; o recibo deriva das saídas fornecidas e do notebook executado.

O [diagnóstico da validação](DIAGNOSTICS.md) inspeciona o HGB já publicado no MLflow,
conferindo scores antes da permutação e do SHAP. Naquela etapa, o modelo ficou fixo
e o teste final não foi consultado. É análise exploratória para orientar ablações
e hipóteses estatísticas; o acesso posterior ao teste tem recibo próprio.
