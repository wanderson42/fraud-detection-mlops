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
Precisão, recall e matriz de confusão dependerão de um limiar escolhido exclusivamente
na validação. Não fixamos uma meta percentual arbitrária nesta etapa nem tratamos
acurácia elevada como demonstração de detecção de fraude. A semente inicial será 42.

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

## Evolução estatística planejada

Antes de selecionar features ou otimizar hiperparâmetros, versionaremos hipóteses,
métrica principal, ganho mínimo relevante e orçamento de experimentos. Comparações
usarão os mesmos exemplos/janelas e tratarão dependência temporal e clientes
repetidos; tamanho do efeito e intervalos acompanharão testes de hipóteses quando
seus pressupostos forem adequados. Uma semana de validação limita evidências de
estabilidade. Resultados exploratórios não serão apresentados como confirmação.

Permutação/SHAP terão propósito diagnóstico; ablações precisarão retreinamento
controlado. O quality gate combinará integridade, desempenho, estabilidade e custo,
sem estabelecer limiares depois de observar o candidato. Essas políticas são
planejadas, não implementadas nesta integração MLflow. O protocolo `temporal_v1`
e o teste reservado permanecem inalterados.

## Diagnóstico exploratório implementado

O [diagnóstico](DIAGNOSTICS.md) implementa permutação por AP, SHAP em amostra uniforme
e erros diários dos candidatos na validação. Usa o modelo existente, sem fit, refit,
seleção automática de features, hipótese confirmatória ou acesso ao teste.
Repetições de permutação medem variação entre embaralhamentos; não fornecem p-valores
ou intervalos de confiança para superioridade. A política estatística e os quality
gates continuam sendo o próximo passo antes de ablações/tuning.
