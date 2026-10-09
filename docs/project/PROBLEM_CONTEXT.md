# Detecção de fraude: contexto, propósito e decisões do projeto

Atualizado em 2026-10-09. A tese deste projeto é construir um **laboratório
reproduzível de MLOps para priorização de investigação de fraude**, com histórico
temporal, feedback atrasado e evidências auditáveis. A contribuição pretendida
é permitir que outra pessoa reproduza, questione e evolua esse sistema.

A pergunta que orienta o trabalho é: **com capacidade limitada de investigação,
quais clientes devemos priorizar usando somente a informação disponível naquele
momento, e como manter essa decisão confiável quando dados e padrões mudam?**

O ponto de partida é fraude em cartões simulada pelo Handbook. O que já existe
é uma avaliação offline de ranking e um pipeline rastreável. Replay, inferência
online, monitoramento e política de promoção são próximos marcos concretos.

## Antes dos modelos: cliente, transação e terminal

Uma transação representa um pagamento de determinado valor, realizado por um
cliente em um terminal, numa data e hora [7]. `CUSTOMER_ID` identifica o cliente;
`TRANSACTION_ID` identifica cada pagamento. A mesma pessoa pode realizar várias
transações, legítimas ou fraudulentas. Uma ocorrência de fraude não transforma
automaticamente todo o seu histórico em fraude: o cliente pode ser a vítima.

**Terminal é o ponto de aceitação do pagamento representado nos dados.** Uma
maquininha de cartão é um exemplo intuitivo. No simulador, `TERMINAL_ID` identifica
esse ponto associado ao comerciante [7]. A fonte não informa o canal
de compra, o equipamento físico ou uma relação entre lojas e várias maquininhas.
Portanto, não deduzimos essas características a partir do ID.

Vários clientes podem usar o mesmo terminal, e um cliente pode usar diferentes
terminais. Isso permite olhar para dois históricos: o comportamento habitual do
cliente e o que aconteceu naquele ponto de pagamento. No cenário de terminal
comprometido do gerador, esse histórico compartilhado pode ajudar a contextualizar
novas transações. Ele não prova que todo usuário daquele terminal sofreu fraude.

O rótulo `TX_FRAUD` informa se a transação foi marcada como genuína (`0`) ou
fraudulenta (`1`) pelo simulador [7]. O **score** é a saída do modelo usada para
ordenar risco; um **alerta** é um caso encaminhado à revisão conforme uma política.
Score, alerta e fraude confirmada são conceitos diferentes. No nosso ciclo atual,
o score não foi calibrado como probabilidade, e os alertas agrupam transações por
cliente/dia. O [dicionário](../data/DATA_DICTIONARY.md) detalha as colunas e features.

## Uma história que antecede os modelos atuais

Esta é uma seleção de marcos publicados, não uma cronologia completa de adoção
industrial. Regras, autenticação, modelos e investigação coexistem: uma geração
de métodos não elimina a anterior.

| Marco | O que documenta | Relação com este projeto |
| --- | --- | --- |
| 1994 — Ghosh e Reilly [1] | Aplicação de redes neurais à detecção de fraude em cartões | Usar ML nessa área não é novidade; precisamos justificar as decisões operacionais |
| 2015 — Sculley et al. [2] | Dependências de dados, feedback e manutenção como dívida técnica de sistemas de ML | Contratos e complexidade controlada fazem parte do produto |
| 2017/2018 — Dal Pozzolo et al. [3] | Modelagem que considera desbalanceamento, atraso de verificação e mudanças temporais | Motivação para disponibilidade de rótulos, avaliação temporal e métricas coerentes |
| 2017/2018 — Carcillo et al., SCARFF [4] | Streaming de fraude combinando processamento de eventos e aprendizado | Replay precisa testar estado e causalidade; a escolha de ferramentas depende da escala |
| 2021 — Tax et al. [5] | Contexto organizacional de equipes antifraude no comércio eletrônico | O modelo deve responder a uma rotina de investigação e decisão |
| 2025 — relatório EBA/ECB [8] | Fraude em pagamentos e adaptação das estratégias dos fraudadores | Um bom resultado histórico exige vigilância e contexto para continuar útil |

As datas duplas indicam divulgação do trabalho em 2017 e publicação em periódico
em 2018. Não significam que essas técnicas começaram a existir nessa data.
O SCARFF usa Kafka, Spark e Cassandra; essa referência não estabelece que o nosso
laboratório precise da mesma infraestrutura.

## Por que o problema continua atual

O relatório EBA/ECB publicado em dezembro de 2025 registrou **€4,2 bilhões de fraude
em pagamentos no Espaço Econômico Europeu em 2024**, frente a €3,5 bilhões em 2023.
Autenticação forte continuou efetiva contra os ataques para os quais foi concebida,
mas o relatório destacou a manipulação de pagadores como desafio crescente [8].
São dados europeus, de diferentes instrumentos de pagamento; não são estatísticas
do Brasil nem uma estimativa de benefício financeiro deste projeto.

No Brasil, o guia oficial do Banco Central descreve recuperação de valores no Pix
com rastreamento de transações subsequentes e bloqueio de recursos suspeitos [9].
Isso fornece um exemplo atual de fraude tratada como processo com múltiplos atores
e etapas. Nosso dataset não representa o Pix, sua rede de contas ou seu processo
de recuperação; servir esse domínio exigiria dados, rótulos e contratos próprios.

A conexão que fazemos com esses contextos é de engenharia: tempo da decisão,
tempo da confirmação, investigação, rastreabilidade e adaptação. Não inferimos
que um modelo treinado nesta simulação seja um detector adequado de golpes atuais.

## Por que uma classificação correta não resolve todo o problema

Desbalanceamento, mudança temporal e atraso de verificação aparecem juntos na
literatura de modelagem realista [3]. No nosso desenho, isso significa distinguir
três relógios: quando a transação ocorre, quando pode ser pontuada e quando seu
rótulo fica disponível. Uma avaliação pode parecer excelente se antecipar o
terceiro relógio ao construir o histórico do segundo.

Também há objetivos diferentes. Ordenar bem transações fraudulentas não garante
priorizar os clientes de maior perda, reduzir o esforço de investigação ou evitar
atrito com clientes legítimos. Nosso orçamento fixa posições na fila; não mede
minutos de revisão. AP avalia ranking; não mede dinheiro recuperado. Score alto
não é autorização automática para bloquear um pagamento.

Uma futura política poderia usar custos de revisão e de erro, valores em risco,
capacidade por turno e ações possíveis. Esses parâmetros teriam de ser definidos
com quem opera o processo. Aqui, a primeira aproximação é explícita: cem clientes
por dia e feedback após sete dias. Vamos avaliar essa política antes de ampliar
o sistema, evitando transformar uma suposição do laboratório em requisito universal.

A mudança temporal também exige separar causas: piora de métricas pode refletir
mudança de prevalência, novos padrões, atraso de rótulos ou defeito no cálculo das
features. Drift de dados é um alerta para investigar; não é, sozinho, evidência de
que um novo modelo deve ser treinado ou promovido. Essa distinção orientará CT e
monitoramento com rótulos atrasados, ainda não implementados.

## Do dado bruto à feature: o que foi preservado e escolhido

O [dicionário de dados](../data/DATA_DICTIONARY.md) explica as nove colunas de origem,
os 19 preditores e os seis metadados Gold, com significado, tipo, fórmula,
janela temporal e comportamento sem histórico.

A Bronze preserva os arquivos originais como bytes, sem selecionar colunas;
a Silver mantém as nove colunas. A Gold define candidatos de modelagem e separa
alvo/identificadores de suas entradas. `TX_FRAUD_SCENARIO` continua na Silver para
auditoria, mas fica fora da Gold: ele revela o alvo e não é um sinal preditivo
admissível. Campos ausentes da fonte, como IP/dispositivo, não foram retirados
pelo extrator.

Houve escolhas prévias de desenho, como janelas de 1/7 dias e representação de
calendário. Depois, o primeiro catálogo comparou três ablações por retreinamento;
nenhuma passou o gate definido, e as 19 features foram conservadas. O
[protocolo de experimentação](../modeling/EXPERIMENT_PROTOCOL.md#resultados-e-decisão--2026-10-08)
registra hipóteses, orçamento, efeitos observados e limites dessa decisão.

Uma transação é nossa unidade de observação; ela não é necessariamente uma réplica
estatística independente. Clientes repetidos, terminais compartilhados e janelas
históricas sobrepostas geram dependência. Agregar por cliente/dia atende à política
de alertas, mas não elimina a repetição da mesma pessoa entre dias. A hipótese
estatística deverá definir os agrupamentos e blocos temporais relevantes.

## Onde nosso modelo se encaixa

Um **Sistema de Detecção de Fraudes**, ou FDS (*Fraud Detection System*), reúne
controles e investigação. No desenho esquemático-o Handbook [6], há cinco camadas:

| Camada | Papel | Momento nesse desenho |
| --- | --- | --- |
| Terminal | Verificações de segurança, como PIN e situação do cartão | Antes da autorização, em milissegundos |
| Regras de bloqueio da transação | Recusar pedidos que atendam a condições definidas por especialistas | Antes da autorização, em milissegundos |
| Regras de pontuação | Atribuir risco por condições conhecidas, incluindo histórico | Após autorização, em tempo quase real |
| Modelo orientado por dados, ou DDM | Aprender padrões e produzir scores para gerar alertas | Após autorização, em tempo quase real |
| Investigadores | Revisar casos, confirmar rótulos e devolver feedback | Investigação offline |

São papéis conceituais do Handbook, não uma arquitetura obrigatória ou um SLA universal. A figura abaixo mostra como regras e modelo apoiam a investigação. O referido diagrama ilustra as camadas de controle em um FDS. O foco neste projeto decorre no modelo orientado por dados (DDM), que auxilia os investigadores ao gerar alertas sobre as **transações mais suspeitas**.

![Diagrama de detecção de fraude](https://fraud-detection-handbook.github.io/fraud-detection-handbook/_images/FDS.jpg)







Neste projeto, o HGB ocupa o papel do DDM; Bronze, Silver e Gold organizam os dados que o alimentam.

**Autorizar um pagamento não confirma sua legitimidade.** Nesse desenho, uma
investigação posterior pode motivar o bloqueio do cartão e evitar novas ocorrências.
O feedback é o resultado verificado da revisão; uma investigação pode esclarecer
outras transações do mesmo cartão [6]. No laboratório, simulamos sua disponibilidade
após sete dias, sem contato real com clientes ou decisões humanas.

Nosso escopo começa na camada de dados/modelo que **ordena risco e apoia a triagem**.
O pipeline atual não implementa autorização de pagamentos, bloqueio de contas,
contato com clientes ou gestão de casos. O orçamento de 100 clientes por dia é
uma hipótese operacional explícita do laboratório, não uma exigência do mercado.

| Pergunta operacional | Decisão já implementada | O que ainda falta demonstrar |
| --- | --- | --- |
| O que sabíamos ao pontuar a transação? | Históricos estritamente anteriores, rótulos disponíveis após sete dias e contratos Gold | Paridade entre cálculo offline e estado durante replay |
| Quais casos cabem na revisão? | Ranking diário de clientes, máximo score e até 100 alertas | Fila online, tempo de investigação e comportamento de uma política de decisão |
| Como reconhecer um experimento? | Dados fixados, hashes, protocolo, scores e modelos MLflow vinculados | Empacotamento, implantação, rollback e ensaios de falha |
| Como decidir se um candidato merece avançar? | AP para seleção e diagnóstico diário | Hipóteses prévias, incerteza, custo e quality gates |
| Como medir uma degradação? | Rótulos e disponibilidade modelados explicitamente | Monitoramento de serviço, dados e desempenho com feedback atrasado |

O ranking diário atual é calculado **offline, com o dia completo**. Sua precisão
é uma medida de priorização retrospectiva; não prova que uma fila ao vivo teria
selecionado os mesmos casos antes da liquidação. Features causais são necessárias,
mas não bastam para validar uma política de decisão em tempo real.

## O que os dados simulam, e o que deixam de fora

O gerador do Handbook define três mecanismos [7]: valor acima de 220 como sinal
artificial fácil de reconhecer; terminais temporariamente comprometidos; e aumento
de valores em parte das transações de clientes comprometidos. O próprio Handbook
identifica o primeiro como teste de implementação, não cenário realista.

Esses mecanismos ajudam a interpretar nosso diagnóstico: dependência de valor,
risco histórico do terminal e desvio do padrão de gastos é compatível com as regras
do gerador. É uma inferência exploratória sobre esta simulação. Não demonstra
descoberta de novas estratégias criminosas ou generalização para transações reais.

Não temos sinais de dispositivo/IP, canal de compra, autenticação, categoria de
comerciante, grafo de transferências, contestações reais ou decisões de investigadores.
O atraso constante de sete dias é uma hipótese, e todos os rótulos acabam disponíveis.
Em uma operação com revisão seletiva, os rótulos observados poderiam depender dos
próprios alertas; esse mecanismo de seleção ainda não é simulado aqui.

Datas de 2018 organizam o relógio do simulador. Não significam observações bancárias
de 2018 nem uma amostra do mercado atual. O valor do laboratório está no controle
de suposições e na validação de procedimentos reproduzíveis. Eficácia antifraude
real dependeria de evidências externas, dados autorizados e avaliação operacional.

## Evidência que já podemos discutir

Na validação de 6 a 12 de maio, a baseline real tem AP de 0,623949 para HGB
(**Histogram-based Gradient Boosting**, boosting de árvores baseado em histogramas) e
0,435001 para regressão logística. A precisão média nos 100 clientes diários é
54% e 48%, respectivamente. O [diagnóstico](../modeling/DIAGNOSTICS.md#resultados)
permite expressar esse resultado como capacidade de triagem:

| Resultado em sete dias | Regressão logística | HGB |
| --- | --- | --- |
| Posições de alerta | 700 | 700 |
| Ocorrências de clientes fraudulentos priorizadas | 336 | 378 |
| Ocorrências de clientes genuínos priorizadas | 364 | 322 |
| Ocorrências de clientes fraudulentos fora dos alertas | 175 | 133 |

São **ocorrências por cliente e dia**, com repetição possível da mesma pessoa em
dias diferentes. O HGB priorizou 42 ocorrências fraudulentas adicionais ao mesmo
orçamento. É um ganho observado de seleção na validação simulada; não é estimativa
de perdas evitadas, prova estatística de superioridade ou resultado de produção.

Diagnóstico e ablações foram concluídos, e a referência foi congelada pelo autor
antes da avaliação final. O [protocolo de avaliação](../modeling/EVALUATION_PROTOCOL.md#congelamento-antes-da-avaliação-final)
define as escolhas e os critérios do laboratório. A avaliação final consumiu o
teste de maio: AP 0,640703 e precisão diária @100 de 55%, com gate de laboratório
aprovado; a [Model Card](../modeling/MODEL_CARD.md) concentra a interpretação.
O ciclo Optuna de junho–agosto conservou a referência. A avaliação de 02–15/09,
o diagnóstico e a reamostragem nativa foram concluídos, com
[escopo exploratório e limites explícitos](../modeling/EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento).
16–30/09 continua reservado para replay. As faixas de reamostragem não demonstram
cobertura futura ou superioridade confirmatória. Exportação e Docker são as
próximas verificações operacionais.

## Para que este projeto pode ser útil

Para quem aprende MLOps, o laboratório permite exercitar decisões temporais e
investigar erros com uma origem conhecida. Para quem avalia um portfólio, oferece
um sistema que pode ser reproduzido e auditado, com escolhas técnicas justificadas
pelo problema. Para uma equipe de engenharia, pode servir como referência de
contratos, testes e experimentação em ambiente controlado, antes de adaptação
e validação com dados e requisitos próprios.

Essa utilidade pode ser demonstrada por ensaios: evento duplicado não produz
dupla pontuação; evento atrasado segue uma política definida; rótulo futuro não
alimenta uma feature presente; recarga preserva scores; falha de infraestrutura
não transforma publicação incompleta em sucesso. Parte desses controles existe
na etapa offline; os ensaios de eventos e operação ainda serão construídos.

O trabalho terá valor crescente quando ligar **qualidade do ranking, custo da
decisão e confiabilidade operacional**. Um dashboard deve tornar isso visível;
uma feature store deve preservar o estado das features; monitoramento deve separar
falha do serviço, mudança de dados e perda de desempenho. Nenhuma dessas ferramentas
substitui a definição do processo que pretendemos apoiar.

## Referências e uso neste projeto

As fontes fundamentam o contexto; não certificam nossos resultados. Números do
projeto derivam das evidências locais informadas pelo autor. Texto próprio, sem
reprodução de figuras ou trechos extensos. Contexto consultado em 2026-10-07;
definições do simulador e camadas do FDS conferidas em 2026-10-08.

1. **Ghosh, S.; Reilly, D. L. (1994).** *Credit card fraud detection with a neural-network.* HICSS. [DOI](https://doi.org/10.1109/HICSS.1994.323314). Marco histórico de uso de redes neurais.
2. **Sculley, D. et al. (2015).** *Hidden Technical Debt in Machine Learning Systems.* NeurIPS. [Artigo](https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf). Manutenção e controle de dependências.
3. **Dal Pozzolo, A. et al. (2018).** *Credit Card Fraud Detection: A Realistic Modeling and a Novel Learning Strategy.* IEEE TNNLS, 29(8), 3784–3797. [DOI](https://doi.org/10.1109/TNNLS.2017.2736643); [manuscrito dos autores](https://dalpozz.github.io/static/pdf/TNNLS_2017.pdf). Avaliação e atraso do feedback.
4. **Carcillo, F. et al. (2018).** *SCARFF: a Scalable Framework for Streaming Credit Card Fraud Detection with Spark.* Information Fusion, 41, 182–194. [DOI](https://doi.org/10.1016/j.inffus.2017.09.005); [preprint de 2017](https://arxiv.org/abs/1709.08920). Operação sobre streams.
5. **Tax, N. et al. (2021).** *Machine Learning for Fraud Detection in E-Commerce: A Research Agenda.* MLHat/KDD. [Preprint](https://arxiv.org/abs/2107.01979). Contexto organizacional.
6. **Fraud Detection Handbook — Machine Learning Group, ULB.** [Sistema de detecção](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_2_Background/FDS.html) e [introdução ao problema](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_2_Background/Introduction.html). Modelo como parte do processo de investigação.
7. **Fraud Detection Handbook.** [Simulador](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html). Regras e limites da nossa fonte de dados.
8. **EBA/ECB (2025).** [Relatório conjunto e comunicado de 15 de dezembro](https://www.ecb.europa.eu/press/pr/date/2025/html/ecb.pr251215~e133d9d683.en.html). Dados de 2022–2024; contexto europeu.
9. **Banco Central do Brasil.** [Guia do MED](https://www.bcb.gov.br/content/estabilidadefinanceira/pix/Guia_MED.pdf), definição de recuperação de valores. PDF observado: versão 4.3, com referência a atualização 4.4. Usado para contextualizar o processo brasileiro, sem afirmar conformidade do projeto com o Pix.
