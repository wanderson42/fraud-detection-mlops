# Protocolo inicial de avaliação temporal

Versão: `temporal_v1`. Estado: protocolo inicial versionado e validador implementado;
features e splits implementados na [Gold](../data/GOLD_CONTRACT.md), com construção e
verificação reais informadas pelo autor. O [baseline](BASELINE.md) implementa treino
e métricas de validação. A avaliação final foi executada e verificada pelo autor;
o resultado e a fronteira consumida estão [registrados abaixo](#resultado-final-informado--2026-10-08).
Fonte: snapshot `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a` do Handbook.
Referência executável: [temporal_protocol_v1.json](../../references/temporal_protocol_v1.json).

## Objetivo e janelas

Fixar as regras antes da escolha de features/modelos, preservar o teste final e
representar o atraso de disponibilização dos rótulos. As datas foram escolhidas
por calendário, sem inspecionar a distribuição de rótulos dessas janelas.

| Papel | Datas inclusivas | Duração e uso |
| --- | --- | --- |
| Treino e EDA | 2018-04-01 a 2018-04-28 | 28 dias; ajuste das transformações e dos candidatos |
| Intervalo de feedback | 2018-04-29 a 2018-05-05 | 7 dias; não entra no ajuste supervisionado |
| Validação | 2018-05-06 a 2018-05-12 | 7 dias; escolha de features, modelo, hiperparâmetros e limiar |
| Intervalo de feedback | 2018-05-13 a 2018-05-19 | 7 dias; permite a chegada dos rótulos da validação |
| Teste final | 2018-05-20 a 2018-05-26 | 7 dias; uma avaliação após congelar as escolhas |
| Histórico futuro reservado | 2018-05-27 a 2018-09-30 | Fora da seleção inicial; futura avaliação temporal e replay |

O JSON usa intervalos `[start, end_exclusive)`: a data final não pertence à janela.
Não é um split aleatório. O treino de quatro semanas captura quatro ciclos semanais;
essa duração é uma escolha inicial do portfólio, não uma janela ótima demonstrada.
Uma única semana de teste não valida estabilidade em seis meses.

## Rótulos disponíveis e ciclo inicial

Assumimos `label_available_at = TX_DATETIME + 7 dias`. Esse timestamp é uma regra da
simulação operacional; não existe nos arquivos como data real de investigação.
No começo da validação, todos os rótulos do treino já estão disponíveis. No começo
do teste, os rótulos da validação também estão disponíveis. O gap de sete dias é
conservador nos limites de dia; a Gold aplica a disponibilidade estrita em cada evento.

O ciclo inicial **não refaz o ajuste com a validação antes do teste**. Os candidatos
são ajustados no mesmo treino; a validação seleciona suas configurações. O candidato
escolhido e as transformações ajustadas no treino seguem congelados para o teste.
Assim, a validação mede o candidato que depois será avaliado no teste. Uma política
futura de refit exige nova versão e comparação própria.

Dados dos intervalos podem futuramente atualizar históricos de transações, desde
que tenham ocorrido antes do evento avaliado; seus rótulos só podem alimentar
features quando disponíveis. Não entram automaticamente como linhas de treino.
Após consultar o teste, não ajustamos o modelo e voltamos a chamar esse mesmo teste
de evidência independente. Uma mudança exige novo protocolo e nova janela preservada.
O histórico posterior será consumido em ordem cronológica quando essa etapa existir.

## População e métricas

A primeira versão considera **todas as transações**, inclusive de clientes já
associados a fraude. A Silver não aplica bloqueios. O Handbook também apresenta uma
avaliação que remove clientes com fraude conhecida; implementar essa política é
outro contrato. Portanto, não faremos comparações diretas com suas métricas publicadas.

- Seleção principal: **Average Precision (AP)**, adequada para avaliar o ranking
  com classe positiva rara. Não é a média de acurácias nem precision num único limiar.
- Métrica secundária: ROC AUC, apresentada com AP e prevalência.
- Métrica operacional secundária: precisão diária nos 100 clientes de maior risco.
  Cada cliente terá o máximo score de suas transações no dia; seu rótulo diário será
  positivo se tiver alguma fraude. Desempate por `CUSTOMER_ID` crescente; denominador
  `min(100, clientes observados no dia)`; agregação pela média das precisões diárias.
  Esse contrato usa clientes da simulação e não inclui bloqueio de comprometidos.

Métricas, regras de score e cálculo operacional estão implementados no [baseline](BASELINE.md),
com testes controlados e resultados reais de validação documentados pelo autor.
Precisão, recall e matriz de confusão por transação dependeriam de um limiar escolhido
exclusivamente na validação. A política atual prioriza 100 clientes por dia, sem
limiar probabilístico. Acurácia elevada não demonstra detecção de fraude. A semente
inicial será 42. Os critérios práticos para o laboratório ficam explícitos abaixo.

## Por que acurácia não é a métrica principal

> O desbalanceamento já mostra por que **acurácia não será nossa métrica principal**: prever todas as transações como genuínas produziria aproximadamente **99,44% de acurácia**, com **recall de fraude igual a zero**. Isso sustenta a escolha de Average Precision para avaliar o ranking.

O exemplo usa as contagens reais de treino informadas pelo autor: 267.163 genuínas
em 268.668 transações. A acurácia contrafactual é `267163 / 268668 ≈ 99,4398%`;
nenhuma das 1.505 fraudes seria detectada. É uma ilustração aritmética, sem ajuste
de modelo nem avaliação da validação ou do teste. Evidência:
[recibo da EDA](../../references/evidence/eda_training_2026-10-07.json).

AP avalia o ranking em diferentes pontos de precisão e recall. A justificativa
para sua escolha não fixa ainda um limiar de decisão nem demonstra a qualidade
do futuro modelo.

## Regras da Gold e do futuro pipeline de modelagem

`TRANSACTION_ID` é chave de auditoria. `CUSTOMER_ID` e `TERMINAL_ID` agrupam histórico;
não entram como inteiros brutos no modelo inicial. `TX_FRAUD` é o alvo;
`TX_FRAUD_SCENARIO` é metadado exclusivo da simulação. Nenhum dos dois será preditor.

As features temporais usam exclusivamente eventos anteriores à transação.
Eventos com timestamp igual ao atual ficam fora do histórico; IDs não comprovam a
ordem real de chegada. Features baseadas em fraude histórica respeitam
a disponibilidade do rótulo. Imputação, escala, seleção de features e qualquer
resampling serão ajustados apenas no treino. Resampling não altera validação/teste.

A EDA usa apenas o treino e gera hipóteses; ela não seleciona automaticamente
features nem fornece uma garantia de ausência de vazamento no futuro pipeline.
Os testes da Gold conferem causalidade, limites das janelas e atraso de rótulos.
Esta versão usa regras constantes, sem ajustar parâmetros nos holdouts. Os futuros
pipelines de modelagem também deverão testar onde ocorre o ajuste das transformações.

## Validação e referências

[temporal_protocol.py](../../fraud_detection_mlops/data/contracts/temporal_protocol.py) valida versão, fonte, datas ISO,
ordem, gaps mínimos de sete dias, cobertura e regras suportadas. O validador não executa avaliações. A [Gold](../data/GOLD_CONTRACT.md) materializa as
features e os splits sem alterar este protocolo; construção e verificação reais
foram informadas pelo autor no [recibo](../../references/evidence/gold_build_2026-10-07.json).
Alterar datas invalida o comparativo anterior; mudanças de política exigem nova
versão do protocolo e implementação correspondente.

Referências primárias:

- [Handbook: baseline e feedback delay](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/BaselineModeling.html).
- [Handbook: estratégias de validação temporal](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html).
- [Handbook: AP e métricas operacionais](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_4_PerformanceMetrics/Summary.html).

As fontes motivam atraso, cronologia e métricas. As datas, a duração do treino e a
população sem bloqueio são decisões próprias deste projeto. O atraso constante de
sete dias é uma simplificação, não uma regra universal de sistemas antifraude reais.

## Implementação Gold preparada

A `gold_v1` implementa janelas de 1/7 dias e atraso fixo de sete dias, com passado
estrito e exclusão de timestamps simultâneos. Isso concretiza as regras de features
do protocolo sem ajustar modelo, transformações aprendidas ou parâmetros nos
holdouts. O [contrato](../data/GOLD_CONTRACT.md) especifica fallback sem histórico e os
limites da disponibilidade por tempo de evento. A causalidade foi testada em bases
controladas. O autor informou construção e verificação da Gold real; o valor
preditivo dessas features ainda exige comparação de modelos na validação.

## Primeiro experimento de validação

O contrato `baseline_v1` compara um controle constante e dois candidatos com os
mesmos 19 preditores. Todos são ajustados no treino; scaler e pesos de classe
não usam a validação. Ela seleciona AP entre os candidatos elegíveis. O runner
não cria scores nem métricas de teste, e não faz refit. A integridade de toda a
Gold continua sendo verificada. Configurações, desempate e limitações ficam no
[baseline](BASELINE.md). Métricas de validação são evidência de seleção, não
estimativas independentes do teste final.

## Experimentos e análise estatística

O [protocolo de experimentação](EXPERIMENT_PROTOCOL.md) e seu
[contrato declarativo](../../references/experiment_protocol_v1.json) fixam três
ablações, orçamento e ganhos práticos antes dos novos fits. O executor foi
implementado e o autor concluiu o catálogo sobre `de41ee0`. A
[revisão dos resultados](EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08)
conservou a referência de 19 features; este documento mantém `temporal_v1`.

A comparação é pareada por transação, com diferenças diárias e sete análises
de influência, excluindo um dia por vez sem refit. Essa análise não é um teste de
superioridade nem um intervalo de confiança. Clientes, terminais e históricos
compartilhados impedem tratar as transações ou os sete dias como réplicas IID.
Uma análise confirmatória exigirá mais períodos e outro protocolo previamente
fixado. O gate de desenvolvimento não autoriza promoção em produção.

## Diagnóstico exploratório implementado

O [diagnóstico](DIAGNOSTICS.md) implementa permutação por AP, SHAP em amostra uniforme
e erros diários dos candidatos na validação. Usa o modelo existente, sem fit, refit,
seleção automática de features, hipótese confirmatória ou acesso ao teste.
Repetições de permutação medem variação entre embaralhamentos; não fornecem p-valores
ou intervalos de confiança para superioridade. O catálogo e o gate de
desenvolvimento foram implementados e aplicados no catálogo fechado. Nenhuma
ablação foi elegível. A decisão conserva a referência, sem retreino sobre a
validação, promoção em produção ou avaliação do teste final.

## Congelamento antes da avaliação final

Estado: congelamento real informado pelo autor e publicado em `623dc86`;
avaliação final executada e verificada pelo autor em 2026-10-08. O contrato executável é
[final_evaluation_protocol_v1.json](../../references/final_evaluation_protocol_v1.json).
O executor `modeling.experiments.candidate_freeze` reutiliza a referência **HistGradientBoostingClassifier
(HGB)** da baseline `f6d7aca720f74316b4183f97b6d866ab`, conservada após o catálogo
de ablações `21ccedf10d944092ba874153c1d21257`.

O [recibo](../../references/frozen_candidate_v1.json) foi criado e verificado localmente,
incluindo `--require-committed`. Seu SHA256 é
`6281337ac9ad872947470d94de0307d7c1934365538877376559d3dee2e57120`. Ele
fixa o modelo MLflow/skops e seus hashes, parâmetros, ordem dos 19 preditores,
ambiente, origem da Gold e as escolhas abaixo. Não cria uma nova run ou uma cópia
permanente do modelo; não refaz ajuste, calibração ou seleção.

| Escolha congelada | Contrato |
| --- | --- |
| Ajuste e preditores | Treino original; todas as 19 features, na ordem da Gold; HGB sem pré-processador |
| Score | Ranking não calibrado; não interpretado como probabilidade de perda financeira |
| População | Todas as transações; sem exclusão de clientes comprometidos |
| Alertas | Máximo score por cliente/dia; top 100; empate por ID crescente |
| Relógio | Dia completo retrospectivo; não é uma decisão online por evento |
| Histórico de fraude | Disponibilidade do rótulo após sete dias, conforme a Gold |
| Holdout | 20–26/05/2018; 66.954 transações esperadas; nenhuma escolha ajustada sobre ele |

O build confere baseline, ablações e manifesto da Gold, lê **somente as partições
de validação** e reproduz seus scores com o modelo existente. Não chama a verificação
completa da Gold, que consulta todos os splits. Reutilizar o recibo mantém seus bytes;
alterar arquivos vinculados ou a política impede sua verificação.

### Critérios práticos do laboratório

A avaliação final deverá satisfazer **ambos**:

- Average Precision global de pelo menos **0,50**.
- Média diária da precisão nos 100 clientes priorizados de pelo menos **0,45**.

São metas deliberadas do projeto, definidas após a validação e **antes de acessar
o teste**. A referência obteve AP ≈ 0,624 e precisão diária @100 ≈ 0,54 na validação;
as metas toleram deterioração limitada para seguir à demonstração de serving.
Não decorrem de um cálculo de custo de fraude, retorno financeiro ou significância
estatística. Não reutilizam o gate de ganhos relativos das ablações.

Satisfazer esses critérios habilita apenas a discussão de um candidato offline
para **serving de laboratório**. Produção exigirá critérios de serviço, segurança,
custos e risco operacional. Falhar exige registrar a falha, conservar os critérios
e não ajustar o modelo sobre esse teste. Outra escolha precisará de nova janela
preservada e protocolo previamente definido.

A futura avaliação reportará AP, ROC AUC, precisão diária @100, métricas por dia e
controle de score constante. Uma única semana e entidades dependentes não sustentam
p-valores, intervalos de confiança ou alegação formal de superioridade neste ciclo.

### Procedimento local em dois commits

Primeiro, aplique o patch, rode `make validate` e versione a implementação e o
contrato. O guard existente exige código, contratos e lockfile comprometidos no Git
antes do build. Depois, com as variáveis apontando para os artefatos já verificados:

```bash
SOURCE_COMMIT=6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a
BASELINE_PATH="data/processed/handbook/$SOURCE_COMMIT/baseline_v1/f6d7aca720f74316b4183f97b6d866ab"
EXPERIMENT_PATH="data/processed/handbook/$SOURCE_COMMIT/experiment_v1/21ccedf10d944092ba874153c1d21257"

poetry run python -m fraud_detection_mlops.modeling.experiments.candidate_freeze build \
  "$BASELINE_PATH" --experiment-path "$EXPERIMENT_PATH"
poetry run python -m fraud_detection_mlops.modeling.experiments.candidate_freeze verify

git add references/frozen_candidate_v1.json
git commit -m "docs: record frozen candidate before final evaluation"
poetry run python -m fraud_detection_mlops.modeling.experiments.candidate_freeze verify --require-committed
```

O comando `verify` confere o recibo e os hashes dos arquivos vinculados. Ele não
consulta o store MLflow nem recarrega o modelo; essa conferência ocorre no build
e deverá ocorrer novamente na futura avaliação final. `--require-committed`
também exige o recibo no Git e os insumos do guard sem divergência de HEAD.

Não altere ou remova o recibo para fazer uma nova seleção. Ele identifica a decisão
deste ciclo. O executor abaixo usa esse recibo; ele permanece como registro da
decisão anterior ao teste, com `test_evaluated: false`. A consulta posterior ao
teste é registrada nos artefatos da avaliação, sem reescrever o recibo histórico.

## Executar a avaliação final

Estado: executor implementado; **execução sobre a fonte simulada concluída e
verificada pelo autor**. As métricas diárias foram revisadas no CSV fornecido.
O módulo `modeling.experiments.final_holdout_evaluation` usa somente o
candidato congelado. Não aceita um novo modelo, limiar, catálogo ou janela na CLI.
As escolhas do [contrato](../../references/final_evaluation_protocol_v1.json) e os
critérios AP ≥ 0,50 e precisão diária @100 ≥ 0,45 permanecem iguais.

Primeiro, aplique o patch, valide e **versione a implementação**. A conferência
existente exige código, contratos, lockfile e recibo sem divergência de HEAD antes
do acesso ao teste. Não há mudança nos arquivos de código já vinculados ao recibo;
o executor é um módulo novo. Depois, na raiz do projeto:

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.candidate_freeze verify --require-committed
poetry run python -m fraud_detection_mlops.modeling.experiments.final_holdout_evaluation run
```

O comando verifica o ambiente e os bytes do modelo MLflow/skops antes da carga,
confere sua assinatura e parâmetros e lê somente os sete Parquets do teste.
Reutiliza o schema e as verificações semânticas da Gold, incluindo rótulos,
históricos válidos e atraso de feedback, e exige 66.954 IDs distintos. Não chama o
loader de treino, não refaz features e não ajusta estimadores.

O controle usa score constante `0,5`, sem fit. Seu valor constante não é uma
probabilidade estimada: serve para conferir ranking sem discriminação. AP equivale
à prevalência quando há positivos; o empate da fila segue ID crescente, conforme
o contrato. Isso não é a reprodução de uma política aleatória de alertas.

O resultado fica em
`data/processed/handbook/<source_commit>/final_evaluation_v1/<freeze_sha256>/`:

| Artefato | Responsabilidade |
| --- | --- |
| `frozen_candidate.json` | Cópia exata do recibo versionado antes do teste |
| `test_predictions.parquet` | IDs, timestamps, rótulos e score do único candidato avaliado |
| `report.json` | Métricas globais e diárias do HGB/controle; critérios fixos e decisão |
| `daily.csv` | AP, prevalência, precisão, recall de clientes e fraudes fora dos alertas por dia |
| `model_card.md` | Uso pretendido, identificação, resultados e limitações do candidato |
| `manifest.json` | Hashes dos cinco outputs, insumos, implementação e ambiente de execução |
| `state.json` | Primeiro início, tentativas, acesso ao teste e falha ou conclusão |
| `mlflow.json` | Identificador da run de avaliação e hash do relatório publicado |

Copie o caminho retornado pelo comando para verificar o resultado:

```bash
EVALUATION_PATH="data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/final_evaluation_v1/6281337ac9ad872947470d94de0307d7c1934365538877376559d3dee2e57120"
poetry run python -m fraud_detection_mlops.modeling.experiments.final_holdout_evaluation verify "$EVALUATION_PATH"
```

O `verify` é offline: confere hashes e recalcula métricas, tabela diária, decisão
e Model Card a partir dos scores salvos. Não consulta Gold ou MLflow. Essa
verificação não demonstra novamente a origem de cada score; a identificação do
modelo e dos dados é conferida pelo executor e registrada no manifesto.

### Decisão, publicação e recuperação

Uma execução íntegra retorna `status: success` mesmo quando o modelo **não passa**
o gate. O resultado substantivo está em `gate.passed` e `gate.decision`:
`eligible_for_laboratory_serving_review` ou `do_not_advance`. Nenhum dos estados
promove modelo em produção ou modifica os critérios. O controle não participa da
seleção. Uma falha do gate é um resultado a documentar.

Há uma run nativa no experimento MLflow `fraud-final-evaluation-v1`, com métricas
e artefatos da avaliação. As runs históricas dos modelos permanecem intactas; a
run nova referencia o URI congelado e não publica outro modelo treinado.

O diretório é determinado pelo hash do recibo. Uma segunda chamada verifica o
resultado pronto, retorna `reused: true` e reutiliza a run MLflow. Uma falha de
publicação após gerar o resultado pode ser retomada sem voltar a pontuar o teste.
Publicação parcial reutiliza a mesma run, identificada por recibo e relatório.

Antes da primeira leitura analítica, o executor registra `test_access_started`.
Uma interrupção não apaga esse fato. Antes de haver um resultado completo, a
retomada pode recalcular os mesmos scores, mas exige os mesmos insumos e código;
isso é recuperação operacional, não outra avaliação independente. Mesmo após
falha, não trate o teste acessado como intocado. Não apague o estado para retunar
o candidato ou tentar outra seleção sobre essa janela.

Um lock de sistema evita chamadas concorrentes para o mesmo recibo e é liberado
pelo sistema ao encerrar o processo. Esse procedimento local tem como alvo Linux;
não é um lock distribuído entre máquinas ou stores remotos. Os checksums identificam
bytes, não substituem controle de acesso ou assinatura de artefatos.

O [notebook da etapa](../../notebooks/stages/09_final_evaluation.ipynb) permite revisar
o contrato, executar ou reutilizar a avaliação e inspecionar resultados diários.
Seus outputs são gerados pelo autor. As saídas de terminal já permitiram consolidar
a avaliação abaixo. O CSV diário fornecido foi revisado; a revisão dos bytes
da Model Card gerada continua pendente.

## Resultado final informado — 2026-10-08

Na revisão `e0fddc0b4958f120f91b54f07880e9f442030f67`, o autor verificou o recibo
com `--require-committed`, executou a avaliação e verificou os cinco outputs.
Foram avaliadas 66.954 transações, com 597 fraudes (prevalência 0,892%).
O modelo original foi conservado, sem refit ou calibração.

| Métrica | HGB | Controle constante | Gate |
| --- | ---: | ---: | --- |
| Average Precision | 0,640703 | 0,008917 | ≥ 0,50: aprovado |
| ROC AUC | 0,890356 | 0,500000 | Informativa |
| Precisão diária por cliente @100 | 0,550000 | 0,018571 | ≥ 0,45: aprovado |

Decisão: `eligible_for_laboratory_serving_review`, com `production_promotion: false`.
A run de avaliação é `bf1f9faeb59442bba17708030d72263d`.
A [evidência](../../references/evidence/final_evaluation_execution_2026-10-08.json)
transcreve as saídas do autor; não equivale a uma consulta independente aos seus
artefatos locais. A [Model Card](MODEL_CARD.md) concentra a interpretação.

**Fronteira consumida:** esta janela deixa de ser um holdout intocado. Não a usamos
para orientar outra seleção, ablação, calibração ou ajuste de hiperparâmetros.
Uma nova etapa de modelagem exigirá outras janelas e protocolo próprio.

A revisão do CSV diário reconciliou as 66.954 transações e 597 fraudes. Nas filas,
385 de 527 ocorrências de cliente com fraude por dia foram identificadas, com
recall médio diário de 73,02%; 142 ocorrências ficaram fora. A precisão variou
de 47% a 61%. A [Model Card](MODEL_CARD.md#revisão-diária-da-fila-de-investigação)
concentra a tabela, unidades e interpretação, sem alterar o gate.
A conferência direta dos bytes da Model Card gerada e do vínculo do CSV ao
manifesto nativo continua pendente. A API e o wheel de serving têm checks
sintéticos; exportar e servir a referência real e medir Docker continuam
pendentes. O [contrato de serving](../operations/SERVING_CONTRACT.md) separa
essas evidências.

A consolidação documental não altera código, lockfile, contratos ou recibo
congelado. Os artefatos operacionais existentes e suas runs permanecem preservados.


## Otimização temporal do HGB com Optuna v1

Problema: comparar configurações do HGB sem confundir hiperparâmetros com a
atualização do período de treino. `hgb_optuna_protocol_v1.json` é a autorização
executável; o candidato v1 e o holdout de 20–26/05 permanecem como evidência histórica.
A busca mantém 19 features, classe positiva e contrato `model_interface_v1`.

As datas abaixo são inclusivas; o JSON usa intervalos com fim exclusivo.

| Corte | Treino (28 dias) | Gap de rótulos | Validação (7 dias) |
| --- | --- | --- | --- |
| fold_1 | 10/06–07/07/2018 | 08–14/07 | 15–21/07 |
| fold_2 | 24/06–21/07/2018 | 22–28/07 | 29/07–04/08 |
| fold_3 | 08/07–04/08/2018 | 05–11/08 | 12–18/08 |

Contexto causal: 27/05–18/08. O preparador lê somente essas 84 partições Silver,
verifica hashes/schema/origem e calcula o mesmo histórico causal Gold, inclusive
rótulos conhecidos com atraso de sete dias. Publica somente os 63 dias distintos
usados nos treinos/validações, sob `development_gold_v2/<identidade>`; não modifica
`gold_v1`. Um dia de validação anterior pode entrar em um treino posterior quando
seus rótulos já estão disponíveis; os três períodos de validação são disjuntos.
As janelas de treino e as entidades se repetem: os cortes não são amostras independentes.

| Reserva | Período | Autorização atual |
| --- | --- | --- |
| Futuro treino confirmatório | 29/07–25/08 | Sem novo fit; parte coincide com desenvolvimento |
| Confirmação | 02–15/09 | Não lida; protocolo estatístico separado antes do acesso |
| Replay operacional | 16–30/09 | Não lido; política de eventos/feedback ainda pendente |

A reserva de setembro é temporal, com os mesmos clientes/terminais possíveis.
Não representa validação em novas entidades. A confirmação de 14 dias é uma
reserva inicial, sem promessa de potência estatística; hipótese, efeito relevante,
unidade e método para dependência temporal/por cliente precisam ser definidos
antes do acesso. Não usar resultados de setembro para novas escolhas de Optuna.

### Busca, seleção e custo

Espaço fixado: `learning_rate` log-uniforme 0,03–0,20; `max_iter` em {100, 200, 300};
`max_leaf_nodes` em {7, 15, 31}; `min_samples_leaf` em {20, 50, 100};
`l2_regularization` log-uniforme 0,01–10. Seed do modelo 42, peso de classe balanced,
sem early stopping, pruning, amostragem de linhas ou ablação.

Cada trial ajusta um modelo em cada corte. A referência usa seus parâmetros
históricos, retreinados em cada um dos mesmos treinos; não é o modelo congelado de
maio. Objetivo: média não ponderada das três APs, uma AP por janela de validação.
Empates no relatório usam precisão média @100 e número do trial. A fila diária
continua retrospectiva: máximo score por cliente/dia, 100 clientes e desempate por ID.

Somente com o orçamento concluído, um candidato pode seguir à **revisão** de
confirmação: ganho absoluto médio de AP ≥ 0,01; ganho absoluto médio de precisão
@100 ≥ 0,02; perda de AP em cada corte ≤ 0,02. O melhor elegível pode diferir do
trial com maior AP. Sem elegíveis: `retain_reference`. Antes da conclusão:
`study_incomplete`. Nenhum desses estados promove um modelo ou comprova superioridade.
A busca não mede a degradação do modelo fixo: essa comparação futura é distinta.

Até 20 trials, inclusive FAIL, e 63 tentativas de fit (3 referência + 20 × 3).
Retomadas não ampliam orçamento. Um processo escritor local; quatro threads nos
ajustes. Preparação DuckDB com quatro threads e limite de memória interna de 2 GB,
que não é limite total de RAM do processo. O relatório informa tempos de fit,
predição e fit+persistência+tracking. `peak_process_rss_mib` é o pico acumulado do
processo Linux, não a memória isolada de cada modelo. Tempos agregam fits concluídos;
tentativas interrompidas permanecem no contador de orçamento. Não há timeout ou teto global
de RAM para HGB nesta versão; executar em lotes e medir custo antes de continuar.

### Execução e recuperação

Instale, valide e faça commit antes da preparação. O executor exige política,
implementação e lockfile rastreados, sem alterações; documentação pode evoluir.

```bash
poetry install
make validate
git diff --check
# Faça o commit da implementação antes dos comandos abaixo.
poetry run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization prepare
DEVELOPMENT_PATH="CAMINHO_DEVELOPMENT_PATH_RETORNADO"
poetry run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization optimize "$DEVELOPMENT_PATH"
poetry run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization verify "$DEVELOPMENT_PATH/study"
```

A primeira busca mede seis fits: três referências e três ajustes do primeiro
trial. Após revisar custo e integridade, por exemplo, continue com dois novos trials:

```bash
poetry run python -m fraud_detection_mlops.modeling.experiments.hgb_optimization optimize "$DEVELOPMENT_PATH" --new-trials 2
```

`--new-trials` limita esta chamada; nunca reinicia o teto global. No fim do orçamento,
novas chamadas verificam/reutilizam os resultados. `verify` recalcula métricas dos
scores salvos, confere alinhamento dos eventos com cada validação, hashes, recibos,
relatório e orçamento; não ajusta, não publica e não precisa acessar MLflow.
A [etapa 10](../../notebooks/stages/10_hgb_optuna.ipynb) só lê resultados salvos.

Identidade inclui protocolo, dados, código/lockfile, versão Optuna e caminho de
tracking. Mudanças recusam reutilização: não misturar estudos/ambientes. SQLite
persiste histórico; seed do sampler `42 + número do trial` torna a sequência
reproduzível entre chamadas sob o mesmo histórico, ambiente e estados de falha.
Não é reprodução do estado interno de um sampler TPE continuamente vivo.

Cada fit concluído tem recibo, modelo skops, scores, métricas e uma run principal
MLflow em `fraud-temporal-optuna-v1`. Fits completos são reutilizados sem nova run.
Uma falha interrompe a chamada. Ao retomar, trial abandonado RUNNING vira FAIL e
consome orçamento; o próximo trial usa os slots restantes. Um fit sem recibo final
não é repetido silenciosamente. Uma referência interrompida bloqueia a continuidade
até revisão do ocorrido, pois esta versão não automatiza recuperação de publicação
parcial ou reposição de referências. Preserve banco, tentativas e artefatos juntos;
não remova recibos, falhas ou banco para ganhar orçamento. Corrupção exige restaurar
o conjunto consistente a partir de backup, ou uma nova autorização documentada.

Referências: [Optuna — persistência e retomada](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/001_rdb.html)
e [Handbook — validação temporal](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html).

### Resultado da busca informado pelo autor

O relatório fornecido pelo autor em 2026-10-09 registra 20 trials concluídos,
nenhuma falha, 63 tentativas de fit e orçamento encerrado. Decisão:
`retain_reference`; nenhum candidato para confirmação. O melhor trial (12)
alcançou AP média 0,658084 contra 0,652277 da referência, e precisão diária
@100 média 56,476% contra 56,238%. Os ganhos ficaram abaixo dos gates fixados.

A confirmação e o teste original não foram usados; não houve promoção nem
alegação formal de superioridade. O [recibo do relato](../../references/evidence/hgb_optuna_author_report_2026-10-09.json)
identifica o arquivo fornecido e seu hash. A revisão de implementação não está no
relatório e não foi inferida; banco, dados e modelos reais não foram conferidos
independentemente. Uma nova hipótese exige outro protocolo e outra identidade.
