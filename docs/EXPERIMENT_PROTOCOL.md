# Protocolo de experimentação controlada

Versão: `experiment_v1`. Preparado em 2026-10-08 sobre a revisão `1975fa3`.
**Estado: política definida para revisão e congelamento no Git; runner ainda não implementado.**
Referência estruturada: [experiment_protocol_v1.json](../references/experiment_protocol_v1.json).
Nenhuma ablação foi executada nesta entrega. O teste final permanece reservado.

## Pergunta e referência

Podemos melhorar a priorização de investigação com menos preditores do terminal,
mantendo o mesmo treino, a mesma população e a capacidade de 100 clientes por dia?

A referência é o HGB já treinado, não uma nova baseline. Sua execução é
`f6d7aca720f74316b4183f97b6d866ab`, com modelo
`runs:/d3be86000fd24dc8a053dbcab778d4b6/model`. Na validação, AP foi
**0,6239485133** e a média diária de precisão nos 100 clientes foi **0,54**.
As evidências estão no [baseline](BASELINE.md) e no [diagnóstico](DIAGNOSTICS.md).

Antes de comparar candidatos, o runner deverá verificar os artefatos de referência
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
  [baseline_protocol_v1.json](../references/baseline_protocol_v1.json).
  Pesos de classe e qualquer transformação aprendida usam apenas o treino.
- Executar **três novos fits**, sequencialmente, na ordem do catálogo,
  com semente 42 e no máximo quatro threads. Nenhum fit da referência.
- Registrar duração de ajuste e predição, quantidade de features e tamanho do modelo.
  Não atribuir economia ao pipeline Gold: ele continuará calculando 19 preditores.
- Encerrar esse ciclo antes de HPO, calibração, ensembles ou novos modelos.
  Uma hipótese adicional exige nova versão documentada antes da execução.

Uma falha deve ficar registrada. Recuperar uma execução incompleta não amplia o
catálogo: candidatos concluídos e verificados são reutilizados. Não escolher um
vencedor de um catálogo parcialmente concluído. O orçamento conta três ajustes
concluídos; tentativas interrompidas e seu custo também serão reportados.

## Comparação e análise estatística deste ciclo

Scores e rótulos serão alinhados por `TRANSACTION_ID`, com igualdade de IDs,
datas, clientes e população. Usar o mesmo contrato de ranking diário do
[protocolo temporal](EVALUATION_PROTOCOL.md): máximo score por cliente, rótulo
positivo se houver fraude no dia, desempate pelo ID e até 100 clientes.

O relatório deverá apresentar:

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

## Tracking e implementação seguinte

A próxima entrega implementará esse catálogo sobre os componentes existentes,
com uma run MLflow por candidato. Registrar protocolo e seu SHA-256, revisão,
lockfile, manifesto Gold, URI da referência, features exatas, parâmetros,
métricas, scores de validação e tempos de execução.

Persistir o pipeline completo em skops, com assinatura correspondente ao subconjunto
de features, e conferir scores após recarga. O relatório comparativo deve apontar
para essas runs; não criar outro sistema de tracking ou repetir recibos extensos.
O notebook da etapa lerá os artefatos gerados e preservará outputs reais.

O contrato temporal permanece `temporal_v1`; a seleção de colunas pertence à política
do experimento, sem exigir `gold_v2`. A promoção futura terá uma política própria:
teste final, Model Card, contrato de inferência, paridade entre processamento
offline e online, monitoramento e rollback. CI aprovada é qualidade de software;
o gate de modelo precisará de evidência científica e operacional.

## Referências e limites

- [Handbook — Validation strategies](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html):
  motivação para avaliação cronológica, atraso de feedback e múltiplos períodos.
- [scikit-learn — average_precision_score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html):
  definição e cálculo de AP sobre scores.
- [Diagnóstico local](DIAGNOSTICS.md): evidência que motivou o catálogo deste projeto.

O Handbook fundamenta a cronologia; as três ablações, o orçamento e os limiares
são escolhas próprias. Resultados do simulador não demonstram desempenho em
instituições financeiras reais. Este protocolo é um contrato declarativo; os
checks automatizados e as comparações serão implementados na próxima entrega.
