# Protocolo de experimentação controlada

Versão: `experiment_v1`. Preparado em 2026-10-08 sobre a revisão `1975fa3`.
**Estado: executor implementado; três ablações executadas e verificadas pelo autor sobre `de41ee0`.**
Referência estruturada: [experiment_protocol_v1.json](../../references/experiment_protocol_v1.json).
A revisão dos arquivos compartilhados concluiu pela conservação da referência;
nenhum candidato passou o gate. O teste final ficou reservado durante as ablações;
a referência foi congelada e avaliada depois, conforme a [Model Card](MODEL_CARD.md).
O JSON conserva o estado registrado no congelamento da política; resultados e
decisões de cada run ficam em `report.json` e no recibo, sem modificar a política após os fits.

## Pergunta e referência

Podemos melhorar a priorização de investigação com menos preditores do terminal,
mantendo o mesmo treino, a mesma população e a capacidade de 100 clientes por dia?

A referência é o HGB (**Histogram-based Gradient Boosting**, boosting de árvores
baseado em histogramas; `HistGradientBoostingClassifier`) já treinado. Sua execução é
`f6d7aca720f74316b4183f97b6d866ab`, com modelo
`runs:/d3be86000fd24dc8a053dbcab778d4b6/model`. Na validação, AP foi
**0,6239485133** e a média diária de precisão nos 100 clientes foi **0,54**.
As evidências estão no [baseline](BASELINE.md) e no [diagnóstico](DIAGNOSTICS.md).

Antes de comparar candidatos, o runner verifica os artefatos de referência
e reconciliar seus scores com as mesmas transações da validação. Incompatibilidade
de ambiente, schema ou scores interrompe a comparação; não justifica retreinar
silenciosamente a referência. Gold, lockfile e revisão efetivamente usados serão
registrados na execução, sem antecipar hashes de arquivos futuros.

## Hipóteses e catálogo fechado

A permutação informou queda de AP negativa para volume de terminal em sete dias
e contagem de fraudes conhecidas em sete dias. SHAP também mostrou influência do
volume de terminal em um caso de fraude com score baixo. Esses achados motivam
ablações; não provam que retirar as features melhora um modelo retreinado.

| Candidato | Remoção de preditores | Restantes | Hipótese |
| --- | --- | --- | --- |
| `without_terminal_volume` | `TERMINAL_TX_COUNT_1D`, `TERMINAL_TX_COUNT_7D` | 17 | Volume pode prejudicar o ranking de risco |
| `without_terminal_fraud_counts` | `TERMINAL_KNOWN_FRAUD_COUNT_1D`, `TERMINAL_KNOWN_FRAUD_COUNT_7D` | 17 | Contagens de fraude podem acrescentar redundância prejudicial |
| `without_terminal_volume_and_fraud_counts` | As quatro features acima | 15 | A remoção conjunta pode ter efeito diferente das remoções individuais |

Preservamos as taxas de fraude e suas contagens de rótulos conhecidos: essas
contagens informam o suporte da taxa, inclusive no início do histórico.
Preservamos também os indicadores de validade das razões de valor. Importância
zero nesta semana não demonstra inutilidade em eventos futuros sem histórico.

A contagem de fraudes pode ser recuperada algebricamente da taxa e de seu suporte
quando o suporte é positivo. Isso não torna as representações equivalentes para
uma árvore com complexidade limitada; o retreinamento mede a diferença.

Cada hipótese pergunta se a diferença de AP frente à referência é positiva e
operacionalmente relevante. Como o catálogo foi motivado por diagnósticos da mesma
validação, este estudo é **exploratório**. Congelar suas regras agora limita novas
escolhas oportunistas; não transforma essa semana em confirmação independente.

## Controles e orçamento

- Usar `gold_v1` e `temporal_v1`: treino em 1–28 de abril, validação em
  6–12 de maio de 2018, com o atraso de rótulos e os gaps já definidos.
- Preservar as 268.668 linhas de treino e as 67.255 de validação. Selecionar
  colunas de `X` na ordem original; nenhuma linha ou coluna física da Gold é removida.
- Usar o HGB e exatamente os parâmetros de
  [baseline_protocol_v1.json](../../references/baseline_protocol_v1.json).
  Pesos de classe e qualquer transformação aprendida usam apenas o treino.
- Executar **três novos fits**, sequencialmente, na ordem do catálogo,
  com semente 42 e no máximo quatro threads. Nenhum fit da referência.
- Registrar duração de ajuste e predição, quantidade de features e tamanho do modelo.
  Não atribuir economia ao pipeline Gold: ele continuará calculando 19 preditores.
- Encerrar esse ciclo antes de HPO, calibração, ensembles ou novos modelos.
  Uma hipótese adicional exige nova versão documentada antes da execução.

Uma falha deve ficar registrada. Recuperar uma execução incompleta não amplia o
catálogo: candidatos concluídos e verificados são reutilizados. Não escolher um
vencedor de um catálogo parcialmente concluído. Cada ajuste iniciado consome uma das três posições do orçamento; o estado registra
as tentativas, inclusive interrupções antes do checkpoint. O [runbook](../operations/EXPERIMENT_EXECUTION.md)
explica esse limite e a reutilização dos candidatos com checkpoints válidos.

## Comparação e análise estatística deste ciclo

Scores e rótulos são alinhados por `TRANSACTION_ID`, com igualdade de IDs,
datas, clientes e população. Usar o mesmo contrato de ranking diário do
[protocolo temporal](EVALUATION_PROTOCOL.md): máximo score por cliente, rótulo
positivo se houver fraude no dia, desempate pelo ID e até 100 clientes.

O relatório apresenta:

1. AP calculada sobre todas as transações da validação e sua diferença para a
   referência; ROC AUC e prevalência como contexto.
2. Precisão diária nos 100 clientes e sua média; diferenças diárias pareadas,
   recall por cliente e ocorrências de cliente/dia fraudulentas não priorizadas.
3. Sete análises de influência: excluir um dia inteiro por vez e recalcular a
   diferença de AP sobre as transações dos seis dias restantes e a diferença
   da média das seis precisões diárias. Os modelos permanecem congelados.

AP agregada **não é a média das APs diárias**. A exclusão de um dia serve para
identificar resultados dependentes de um período específico. Mostrar todas as
sete diferenças e sua amplitude; não escolher apenas a exclusão favorável.
Se uma métrica for indefinida por falta de classe, registrar essa condição.

**Essa análise de influência não fornece intervalo de confiança nem p-valor.**
Os dias compartilham clientes, terminais e históricos móveis; não são sete
réplicas independentes. As 67.255 transações também não são observações IID.
Não faremos teste t sobre linhas, teste de sinais assumindo dias independentes
ou bootstrap de transações individuais para declarar superioridade.

Testes de hipóteses continuam no plano do projeto. Antes de uma análise
confirmatória, será necessário um protocolo separado com mais períodos
cronológicos, candidatos congelados, efeito mínimo, tratamento de comparações
múltiplas e uma estratégia de dependência justificada. Reamostragem de blocos
temporais é uma possibilidade a avaliar; não adotaremos um comprimento de bloco
arbitrário para fabricar um intervalo nesta semana curta.

A avaliação final de 20–26 de maio seguirá uma única vez após congelar as escolhas.
O histórico posterior a 27 de maio poderá fornecer avaliação prospectiva mais
longa com outra política previamente fixada. Não usar esse futuro para selecionar
um candidato e depois apresentar maio anterior como teste prospectivo.

## Gate de desenvolvimento

Os valores abaixo são **decisões práticas deste portfólio**, fixadas antes dos
scores das ablações. Não são metas universais de antifraude nem estimativas de
retorno financeiro. Com dados sintéticos sem custos reais de investigação,
recuperação e fraude, ainda não existe função de utilidade monetária validada.

Um candidato será elegível para revisão de congelamento se:

- passar os controles de integridade, causalidade, assinatura e paridade após recarga;
- participar de um catálogo completo, com configuração e ambiente registrados;
- obter **ganho absoluto de AP de pelo menos 0,01** frente à referência;
- obter **ganho absoluto de precisão diária média nos 100 clientes de pelo menos 0,02**.

Isso corresponde aproximadamente a AP ≥ 0,6339485 e precisão diária média ≥ 0,56.
Como há 100 vagas em todos os sete dias observados, dois pontos percentuais
representam **14 ocorrências adicionais de cliente/dia fraudulentas priorizadas
na semana**. Não são 14 pessoas distintas nem perdas comprovadamente evitadas.

Entre elegíveis, ordenar por AP decrescente, precisão diária média decrescente,
menos features e ID crescente. Aplicar o desempate somente quando o critério
anterior for igual. Se nenhum for elegível, preservar a referência; o estudo
continua útil como evidência contra a hipótese.

O gate identifica um candidato para revisão, **não promove um modelo em produção**.
Antes do congelamento, revisar erros diários, análises de influência e custo.
Se a melhoria depender de um dia ou vier com degradação operacional preocupante,
registrar a dúvida e preservar a referência. Qualquer novo critério numérico
exige uma versão futura; não reescrever o gate para favorecer o resultado observado.

## Tracking implementado

O executor usa os componentes existentes, com uma run MLflow por candidato.
Registra protocolo e seu SHA-256, revisão,
lockfile, manifesto Gold, URI da referência, features exatas, parâmetros,
métricas, scores de validação e tempos de execução.

O pipeline completo é persistido em skops, com assinatura do subconjunto de
features e scores conferidos após recarga. O relatório aponta para as três runs.
O [runbook](../operations/EXPERIMENT_EXECUTION.md) descreve execução, verificação e retomada.
O notebook da etapa distingue células executáveis de resultados históricos em
Markdown; esta atualização não fabrica outputs de execução.

O contrato temporal permanece `temporal_v1`; a seleção de colunas pertence à política
do experimento, sem exigir `gold_v2`. A promoção futura terá uma política própria:
teste final, Model Card, contrato de inferência, paridade entre processamento
offline e online, monitoramento e rollback. CI aprovada é qualidade de software;
o gate de modelo precisará de evidência científica e operacional.

## Resultados e decisão — 2026-10-08

O autor executou `21ccedf10d944092ba874153c1d21257` sobre a revisão
`de41ee00246b6170cc65bdf63df1808ec35277bd`. O comando `verify` informou
sucesso e 18 artefatos. Foram compartilhados `summary.csv`, `daily.csv`,
`leave_one_day_out.csv` e `report.json`; os três últimos foram reconciliados
nesta revisão. O [recibo](../../references/evidence/controlled_ablation_execution_2026-10-08.json)
registra origem, ambiente, identidades e limites da conferência.

| Modelo | Features | AP global | ΔAP | Precisão diária @100 | Cliente/dia fraudulento priorizado | Não priorizado |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Referência | 19 | 0,623949 | — | 54,00% | 378 | 133 |
| Sem volume do terminal | 17 | 0,610552 | −0,013396 | 53,43% | 374 | 137 |
| Sem contagens de fraude | 17 | 0,626240 | +0,002291 | 53,29% | 373 | 138 |
| Sem ambos os grupos | 15 | 0,628595 | +0,004647 | 53,14% | 372 | 139 |

Todos usam as mesmas 67.255 transações, 580 transações fraudulentas, sete dias e
700 vagas. Há 511 ocorrências fraudulentas de cliente/dia; uma pessoa pode aparecer
em vários dias. As diferenças de 4, 5 e 6 ocorrências não são pessoas únicas,
fraudes monetárias evitadas ou estimativas de perda financeira.

**Revisão diária.** A remoção de volume perde precisão em quatro dias, empata em
dois e ganha em um. As outras duas ablações perdem em três dias, empatam nos
outros quatro e não ganham em nenhum. No candidato de 15 features, as seis
ocorrências adicionais não priorizadas concentram-se em 9 de maio (duas),
11 de maio (três) e 12 de maio (uma). São diferenças no total de positivos
priorizados, não uma comparação individual de quais clientes mudaram de fila.

**Influência temporal.** A tabela abaixo mostra as sete exclusões de dia para
cada candidato. Os valores são mínimos e máximos observados, não intervalos de
confiança. O ΔAP de cada exclusão usa todas as transações dos seis dias restantes.

| Ablação | Amplitude de ΔAP ao excluir um dia | Amplitude de Δprecisão @100 (p.p.) |
| --- | ---: | ---: |
| Sem volume do terminal | −0,018622 a −0,005339 | −0,833 a −0,333 |
| Sem contagens de fraude | −0,001457 a +0,004918 | −0,833 a −0,500 |
| Sem ambos os grupos | +0,000464 a +0,007544 | −1,000 a −0,500 |

A perda operacional permanece nas 21 exclusões. A remoção de volume perde AP nas
sete; a remoção de contagens muda o sinal da diferença de AP em duas exclusões.
O candidato de 15 features conserva ΔAP positivo nas sete exclusões, sempre
abaixo de +0,01. Seu ganho cai de +0,004647 na semana completa para +0,000470 ao
excluir 6 de maio e +0,000464 ao excluir 11 de maio. O sinal persiste, mas a
magnitude depende do período incluído. Essa influência não demonstra causalidade.

**Decisão:** manter a referência HGB com 19 features e encerrar o catálogo de
`experiment_v1`. Nenhum candidato atingiu simultaneamente os ganhos de +0,01 em
AP e +0,02 em precisão operacional. Os limiares e o catálogo permanecem os
congelados antes dos scores. Uma importância negativa por permutação motivou
hipóteses; o retreinamento não sustentou a remoção sob este gate.

A execução não fez refit com validação, não avaliou o teste, não executou teste de
hipótese ou intervalo de confiança e não promoveu modelo em produção. A evidência
justifica a decisão de desenvolvimento nesta janela simulada, sem comprovar
superioridade estatística ou estabilidade futura. Custos de fit e predição não
foram revisados aqui; nenhuma economia computacional foi atribuída à ablação.

Após este ciclo, a referência foi congelada e avaliada, com critérios fixados
antes do teste; os resultados posteriores estão na [Model Card](MODEL_CARD.md).
Uma expansão
exploratória exige outro protocolo; não se amplia este catálogo para perseguir
um ganho na mesma validação. Preservar scores, modelos, manifestos, estado e
auditoria nativos junto aos arquivos da execução local.

## Referências e limites

- [Handbook — Validation strategies](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html):
  motivação para avaliação cronológica, atraso de feedback e múltiplos períodos.
- [scikit-learn — average_precision_score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html):
  definição e cálculo de AP sobre scores.
- [Diagnóstico local](DIAGNOSTICS.md): evidência que motivou o catálogo deste projeto.

O Handbook fundamenta a cronologia; as três ablações, o orçamento e os limiares
são escolhas próprias. Resultados do simulador não demonstram desempenho em
instituições financeiras reais. O contrato declarativo, os checks automatizados
e as comparações estão implementados; a execução local foi informada pelo autor.
