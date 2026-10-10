# Model Card — referência temporal de detecção de fraude

Atualizada em 2026-10-09. Estado: **avaliação final executada e verificada pelo autor;
gate de laboratório aprovado; avaliação de 02–15/09 e reamostragem exploratória
revisadas; referência exportada e HTTP/wheel isolado validados pelo autor**.
Docker está preparado para construção e medição no host, ainda pendentes. O
[contrato de serving](../operations/SERVING_CONTRACT.md) define esses checks.
Esta síntese curada usa as saídas de terminal, o CSV diário de maio e os relatórios
de setembro fornecidos pelo autor. A revisão direta dos bytes da Model Card gerada permanece
pendente; os scores e o manifesto nativos não foram fornecidos ao assistente.

## Uso pretendido e decisão

O modelo prioriza transações suspeitas em um laboratório de investigação com dados
sintéticos. A avaliação agrupa os scores por cliente/dia e limita a fila a 100
clientes. A finalidade do portfólio é demonstrar causalidade, reprodução e operação
controlada de ML; o [contexto do problema](../project/PROBLEM_CONTEXT.md) situa essa capacidade
nas camadas de um sistema de detecção de fraude.

A decisão é `eligible_for_laboratory_serving_review`. Os dois critérios definidos
antes do acesso ao teste foram atingidos. Não há promoção em produção, autorização
de bloqueio de cartões ou estimativa de retorno financeiro.

## Identificação e dados

| Campo | Identificação |
| --- | --- |
| Algoritmo | Histogram-based Gradient Boosting (HGB), `HistGradientBoostingClassifier` do scikit-learn |
| Modelo MLflow | `runs:/d3be86000fd24dc8a053dbcab778d4b6/model` |
| Run da avaliação final | `bf1f9faeb59442bba17708030d72263d` |
| Revisão do executor avaliado | `e0fddc0b4958f120f91b54f07880e9f442030f67` |
| Escolhas e identidade dos bytes | [Recibo congelado](../../references/frozen_candidate_v1.json) |
| Preditores | 19 features Gold v1, conservadas após três ablações sem ganho elegível |
| Dados de origem | Transações sintéticas do Fraud Detection Handbook; fonte fixada em `6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a` |
| Treino | 01–28/04/2018; 268.668 transações |
| Validação | 06–12/05/2018; 67.255 transações |
| Teste final | 20–26/05/2018; 66.954 transações e 597 fraudes |
| Avaliação posterior da referência fixa | 02–15/09/2018; 134.467 transações e 1.200 fraudes; janela consumida |
| Reserva de replay operacional | 16–30/09/2018; execução ainda pendente |
| Ajuste final | Modelo original; sem refit, calibração ou ajuste no teste |
| Ambiente do modelo | scikit-learn 1.9.1, MLflow 3.17.0, skops 0.16.0, numpy 2.5.3 e pandas 3.0.6 |

O [dicionário](../data/DATA_DICTIONARY.md) explica os campos; o [contrato da Gold](../data/GOLD_CONTRACT.md)
fixa as janelas causais, o cold start e o atraso de sete dias dos rótulos.
IDs, timestamps e rótulo atual não entram diretamente como preditores.

## Avaliação e resultado observado

| Métrica | Validação HGB | Teste HGB | Controle constante no teste |
| --- | ---: | ---: | ---: |
| Average Precision (AP) | 0,623949 | **0,640703** | 0,008917 |
| ROC AUC | 0,884953 | **0,890356** | 0,500000 |
| Precisão diária por cliente @100 | 0,540000 | **0,550000** | 0,018571 |

A AP usa todas as transações da janela. A precisão @100 é a média não ponderada
entre os dias: máximo score por cliente/dia, rótulo positivo quando houver alguma
fraude nesse cliente/dia e empate por ID crescente. O controle tem score constante;
sua fila é determinística, não uma política aleatória de investigação.

| Gate predefinido | Mínimo | Observado | Resultado |
| --- | ---: | ---: | --- |
| AP | 0,50 | 0,640703 | Aprovado |
| Precisão diária por cliente @100 | 0,45 | 0,55 | Aprovado |

A prevalência de fraude no teste foi **0,892%**. Prever tudo como genuíno daria
**99,11% de acurácia e recall de fraude zero**. A AP do HGB é aproximadamente 72
vezes a do controle; a precisão da fila é aproximadamente 30 vezes a do controle.
Essas razões descrevem esta janela e não estimam fraudes evitadas.

Em filas com 100 clientes por dia, a precisão de 55% corresponde em média a 55
clientes com fraude e 45 sem fraude entre os alertados. Clientes podem reaparecer
em outros dias; essa contagem não representa pessoas únicas no período.
A captura dentro do orçamento é detalhada na tabela diária abaixo.

A proximidade entre validação e teste é encorajadora neste recorte temporal.
Não constitui teste de hipótese, intervalo de confiança ou demonstração de
estabilidade em outros períodos. A revisão dos sete dias abaixo mostra variação
observada; não comprova estabilidade de longo prazo.

## Revisão diária da fila de investigação

O CSV fornecido pelo autor cobre exatamente 20–26/05/2018, com uma linha por
modelo/dia. Os totais de transações e fraudes reconciliam com o terminal, assim
como a média de precisão diária de 55%. Contagens e taxas foram recalculadas a
partir do CSV; seus bytes estão identificados na evidência.

| Dia | AP HGB | Precisão @100 | Clientes com fraude no dia | Na fila | Fora da fila | Recall por cliente |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20/05 | 0.6926 | 60% | 77 | 60 | 17 | 77.92% |
| 21/05 | 0.6138 | 47% | 75 | 47 | 28 | 62.67% |
| 22/05 | 0.6355 | 55% | 74 | 55 | 19 | 74.32% |
| 23/05 | 0.6650 | 61% | 83 | 61 | 22 | 73.49% |
| 24/05 | 0.7059 | 59% | 75 | 59 | 16 | 78.67% |
| 25/05 | 0.6122 | 53% | 72 | 53 | 19 | 73.61% |
| 26/05 | 0.5668 | 50% | 71 | 50 | 21 | 70.42% |

Foram **700 alertas**, dos quais **385** associados a algum cliente com fraude
naquele dia e **315** sem fraude. Entre **527 ocorrências de cliente com fraude
por dia**, **142** ficaram fora das filas. Essas unidades são **cliente-dia**;
não são pessoas únicas nem contagens de transações fraudulentas.

O recall médio diário é **73,02%**. A proporção agregada por cliente-dia é
**385/527 = 73,06%**, que pondera os dias pelo número de clientes com fraude.
São agregações distintas, ambas descritivas. O controle identificou apenas
13 ocorrências e teve recall médio diário de 2,47%.

Em **21/05**, a precisão foi 47% e o recall 62,67%, com 28 clientes com fraude
fora da fila; esse foi o dia de menor recall. Em **26/05**, a AP foi menor
(0,5668), mas o recall @100 foi 70,42%. A ordenação global e a captura em um
orçamento específico respondem a perguntas diferentes.

A precisão variou de **47% a 61%** e a AP diária de **0,5668 a 0,7059**. Essa
variação orienta a futura observabilidade. O gate continua usando AP global e
média diária de precisão, conforme definido antes do teste; não criamos um gate
por dia após observar os resultados. A AP global de 0,640703 também não é a
média simples das APs diárias (0,641672).

## Avaliação posterior e sensibilidade exploratória

O mesmo modelo de abril foi avaliado em 02–15/09, sem refit ou novas runs:
**AP 0,621312**, precisão média diária @100 de **54,93%** e recall médio diário
de **72,89%**. Capturou 769 ocorrências de cliente-dia com fraude, entre 1.055;
286 ficaram fora da fila. São unidades repetíveis, não pessoas únicas.

O diagnóstico mostrou recorrência de entidades e a reamostragem circular de dias
completos comparou blocos de 2/3/4/7 dias, com 2.000 réplicas por configuração.
Todas ficaram definidas. As faixas centrais da AP variaram aproximadamente entre
0,579 e 0,667 na grade, com massa nominal de 95% e cobertura não validada. Esses
quantis não constituem intervalo demonstrado de desempenho futuro, comparação
confirmatória ou evidência isolada de degradação entre maio e setembro.

O [resultado completo e fechamento](EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento)
mantém método, grade e limites. O escopo exploratório foi concluído; confirmação
de outro modelo exige candidato elegível, desenho próprio e janela preservada.

## Limitações que afetam o uso

- A simulação contém regras de fraude conhecidas, inclusive um limiar artificial
  de valor. Desempenho nesse domínio não demonstra eficácia contra golpes reais.
- A janela final tem sete dias. Transações de clientes e terminais repetidos têm
  dependência; linhas não formam observações independentes para inferência estatística.
- Os scores são valores de ranking sem calibração demonstrada. AP não é o recall
  no orçamento de alertas e ROC AUC não é a precisão da investigação.
- A fila usa o dia completo retrospectivamente. Não foi demonstrada uma política
  de alertas online, bloqueio de clientes comprometidos ou paridade de features online.
- Todos os rótulos chegam após sete dias no cenário assumido. Investigação seletiva,
  atrasos variáveis e qualidade de feedback real ainda não foram modelados.
- Ainda não há evidência de latência, disponibilidade, custo por requisição,
  recuperação do serviço, perdas financeiras ou impacto sobre clientes.

## Fronteira da inferência e próximos marcos

O serviço implementado recebe as 19 features já calculadas e devolve um score com
identificação do modelo. O [contrato de serving](../operations/SERVING_CONTRACT.md)
separa pontuação, cálculo de históricos e política de investigação.
O autor exportou a referência com 67.255 linhas de paridade e validou o score
HTTP fora do checkout, mantendo a identidade congelada. Docker ainda exige
construção e medições no host; este incremento usa a base integrada pelo PR #2. Prometheus/Grafana e
replay entram com o funcionamento observável, conforme o [mural](../project/ROADMAP.md).

O teste final foi consumido. Melhorias futuras exigem outra janela de avaliação
e um protocolo definido previamente. O recibo congelado conserva
`test_evaluated: false` por registrar a decisão anterior ao teste; o resultado da
avaliação conserva `test_evaluated: true` sem reescrever essa história.

## Evidências e reprodução

A [evidência da execução](../../references/evidence/final_evaluation_execution_2026-10-08.json)
registra a saída do autor, revisão, URI, hashes reportados, decisão e limites da
conferência. A verificação local aprovou cinco outputs e reconciliou as métricas.
O assistente consultou o CSV diário fornecido e reconciliou suas contagens e
taxas; não consultou os scores, a base MLflow local ou o dataset do autor. A
identidade do CSV no manifesto nativo não foi conferida independentemente.

O executor gera `model_card.md` dentro do diretório da avaliação. Esse arquivo
operacional permanece ligado ao relatório; este documento curado acrescenta
interpretação e navegação, sem substituir ou editar seus bytes.
Use [o procedimento de verificação offline](EVALUATION_PROTOCOL.md#executar-a-avaliação-final)
para consultar a evidência salva sem voltar a avaliar candidatos.

A avaliação de setembro e seus relatórios posteriores têm
[recibo próprio](../../references/evidence/reference_uncertainty_author_validation_2026-10-09.json).
Os hashes reportados de modelo/previsões e os hashes da implementação coincidiram
com a entrega. Os bytes nativos das previsões não foram recebidos; a AP reamostrada
é resultado do autor, sem recálculo independente nesta revisão.
