# Protocolo inicial de avaliação temporal

Versão: `temporal_v1`. Estado: protocolo inicial versionado e validador implementado;
features e splits implementados na [Gold](GOLD_CONTRACT.md), com construção e
verificação reais informadas pelo autor. O [baseline](BASELINE.md) implementa treino
e métricas de validação, com execução real informada pelo autor e teste final reservado.
Fonte: snapshot `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a` do Handbook.
Referência executável: [temporal_protocol_v1.json](../references/temporal_protocol_v1.json).

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
[recibo da EDA](../references/evidence/eda_training_2026-10-07.json).

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

[temporal.py](../fraud_detection_mlops/temporal.py) valida versão, fonte, datas ISO,
ordem, gaps mínimos de sete dias, cobertura e regras suportadas. O validador não executa avaliações. A [Gold](GOLD_CONTRACT.md) materializa as
features e os splits sem alterar este protocolo; construção e verificação reais
foram informadas pelo autor no [recibo](../references/evidence/gold_build_2026-10-07.json).
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
holdouts. O [contrato](GOLD_CONTRACT.md) especifica fallback sem histórico e os
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
[contrato declarativo](../references/experiment_protocol_v1.json) fixam três
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
avaliação do teste ainda pendente. O contrato executável é
[final_evaluation_protocol_v1.json](../references/final_evaluation_protocol_v1.json).
O executor `modeling.freeze` reutiliza a referência **HistGradientBoostingClassifier
(HGB)** da baseline `f6d7aca720f74316b4183f97b6d866ab`, conservada após o catálogo
de ablações `21ccedf10d944092ba874153c1d21257`.

O [recibo](../references/frozen_candidate_v1.json) foi criado e verificado localmente,
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

poetry run python -m fraud_detection_mlops.modeling.freeze build \
  "$BASELINE_PATH" --experiment-path "$EXPERIMENT_PATH"
poetry run python -m fraud_detection_mlops.modeling.freeze verify

git add references/frozen_candidate_v1.json
git commit -m "docs: record frozen candidate before final evaluation"
poetry run python -m fraud_detection_mlops.modeling.freeze verify --require-committed
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

Estado: executor implementado e validado com dados controlados; **execução real e
resultados de teste pendentes**. O módulo `modeling.evaluation` usa somente o
candidato congelado. Não aceita um novo modelo, limiar, catálogo ou janela na CLI.
As escolhas do [contrato](../references/final_evaluation_protocol_v1.json) e os
critérios AP ≥ 0,50 e precisão diária @100 ≥ 0,45 permanecem iguais.

Primeiro, aplique o patch, valide e **versione a implementação**. A conferência
existente exige código, contratos, lockfile e recibo sem divergência de HEAD antes
do acesso ao teste. Não há mudança nos arquivos de código já vinculados ao recibo;
o executor é um módulo novo. Depois, na raiz do projeto:

```bash
poetry run python -m fraud_detection_mlops.modeling.freeze verify --require-committed
poetry run python -m fraud_detection_mlops.modeling.evaluation run
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
poetry run python -m fraud_detection_mlops.modeling.evaluation verify "$EVALUATION_PATH"
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

O [notebook da etapa](../notebooks/stages/09_final_evaluation.ipynb) permite revisar
o contrato, executar ou reutilizar a avaliação e inspecionar resultados diários.
Seus outputs reais serão gerados pelo autor. Após essa execução, documentaremos
o resultado e revisaremos a Model Card antes de iniciar o contrato de inferência.
