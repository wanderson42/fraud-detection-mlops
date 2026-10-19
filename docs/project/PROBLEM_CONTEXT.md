# Detecção de fraude: contexto, propósito e decisões do projeto

Atualizado em 2026-10-16. Este projeto estuda como usar dados para ajudar uma equipe
a escolher **quais clientes precisam de investigação**, quando há mais transações
para analisar do que capacidade para revisar cada caso.

Para começar, imagine uma situação fictícia: Ana costuma fazer compras de valores
parecidos. Em certo dia, surgem várias compras em pouco tempo e uma delas tem um
valor muito maior que o habitual. Isso pode merecer atenção. Mas Ana também pode
estar fazendo compras legítimas para uma ocasião especial. O comportamento
incomum é uma pista; a confirmação depende de informação adicional.

A pergunta do projeto nasce dessa incerteza: **quais casos investigar primeiro e
como justificar a escolha com o que sabíamos naquele momento?** Para respondê-la,
construímos um laboratório com dados simulados de pagamentos por cartão. Os nomes,
valores e horários dos exemplos desta introdução são apenas ilustrações.

## Antes dos modelos: cliente, transação e terminal

É útil separar três elementos que aparecem juntos em um pagamento:

| Termo | Como entender | Identificador no dataset |
| --- | --- | --- |
| Cliente | A pessoa representada no histórico de compras; Ana pode aparecer em muitos pagamentos | `CUSTOMER_ID` |
| Transação | Um pagamento específico, com valor e data/hora próprios | `TRANSACTION_ID` |
| Terminal | O ponto em que o pagamento é aceito; uma maquininha é um exemplo intuitivo | `TERMINAL_ID` |

Esses campos fazem parte do simulador do Handbook [1]. O identificador de terminal
não informa, por si só, o equipamento físico, o canal de compra ou quais terminais
pertencem à mesma loja.

Se Ana fizer três compras, teremos três transações ligadas ao mesmo cliente.
Se outra pessoa pagar no mesmo terminal, haverá também um histórico compartilhado
desse ponto de pagamento. Podemos, assim, olhar tanto para os hábitos de Ana quanto
para o que vinha acontecendo naquele terminal.

Também precisamos distinguir a pessoa do evento: uma transação fraudulenta pode
ter sido feita por alguém usando o cartão de Ana sem autorização. Ana pode ser a
vítima. Uma fraude não torna automaticamente fraudulentas todas as compras dela.

## Como um modelo ajuda a escolher o que investigar

Um modelo de aprendizado de máquina procura padrões nos exemplos usados em seu
treinamento. Ao receber uma nova transação, usa as informações fornecidas para
produzir uma pontuação de risco, chamada **score**.

Neste projeto, parte dessas informações descreve a compra atual; outra parte
resume o histórico. Por exemplo: quantas compras o cliente fez no último dia?
Qual era seu valor médio? Que histórico de fraude já confirmado estava disponível
sobre o terminal? Essas informações de entrada são chamadas **features**. O
[dicionário de dados](../data/DATA_DICTIONARY.md) explica como cada uma é calculada.

O score ajuda a ordenar os casos: valores maiores indicam maior risco segundo o
modelo. Ele não foi calibrado como probabilidade. Portanto, um score de `0,8` não
permite afirmar que aquela compra tem exatamente 80% de chance de ser fraude.

Há três conceitos diferentes nesse processo:

| Conceito | Pergunta a que responde | Neste laboratório |
| --- | --- | --- |
| Score | Quanto risco o modelo atribuiu ao caso? | Pontuação usada para ordenar e selecionar |
| Alerta | O caso foi encaminhado para revisão? | Decisão tomada pela regra de triagem, considerando score e capacidade |
| Rótulo | O que sabemos sobre o resultado da transação? | `TX_FRAUD`: `0` para genuína e `1` para fraudulenta na simulação |

Um caso pode receber score alto e depois ser confirmado como legítimo. Outro pode
receber score baixo e ser uma fraude que o sistema deixou passar. Gerar um alerta
é uma decisão diante da incerteza; a avaliação posterior mostra onde acertamos e
onde erramos. Aqui, o resultado vem do simulador, sem investigadores reais.

## Qual decisão o laboratório apoia

Nossa regra de trabalho permite encaminhar até **100 clientes distintos por dia**.
Esse limite representa uma equipe com capacidade finita de revisão. É uma hipótese
do estudo de caso, escolhida para tornar a decisão e sua avaliação concretas.
Não é uma capacidade medida em uma instituição financeira.

O cliente é a unidade da fila porque pode ter várias transações relacionadas.
Na política atual, Ana ocupa no máximo uma posição no dia, mesmo que outras compras
dela também pareçam suspeitas. Em outro dia, ela pode voltar à fila. É isso que
significa **cliente-dia**: o mesmo cliente em dois dias corresponde a duas
ocorrências na avaliação.

O sistema deste projeto pontua transações e organiza encaminhamentos. Autorizar
pagamentos, bloquear cartões, contatar clientes e conduzir investigações exigiria
outros componentes e regras. Os alertas do laboratório servem para estudar a
priorização, sem executar essas ações.

### Selecionar no fim do dia e selecionar enquanto os casos chegam

Considere duas maneiras de distribuir as posições disponíveis:

- **Ranking retrospectivo:** esperamos o dia terminar, guardamos o maior score de
  cada cliente e selecionamos até 100 clientes com os maiores scores. Podemos usar isso para estudar a
  qualidade da ordenação com o dia completo.
- **Fila causal:** decidimos enquanto os eventos chegam. O cliente entra na fila
  na primeira transação que alcança um **limiar**, isto é, o score mínimo definido
  para encaminhamento, enquanto houver capacidade. O limiar do projeto foi fixado
  no desenvolvimento.

Para visualizar a diferença, imagine somente **duas posições** e um limiar de
**0,6**, escolhidos apenas para este exemplo:

| Chegada | Cliente | Score | Decisão da fila naquele instante |
| --- | --- | --- | --- |
| 09h | Ana | 0,65 | Encaminhar; resta uma posição |
| 10h | Bia | 0,75 | Encaminhar; capacidade esgotada |
| 11h | Caio | 0,95 | Não encaminhar; as duas posições já foram ocupadas |

Ao fim do dia, o ranking escolheria Caio e Bia. A fila já teria escolhido Ana e Bia;
ela mantém os encaminhamentos emitidos. Às 09h, não havia como conhecer o caso que
chegaria às 11h. Por isso, **uma boa ordenação retrospectiva e uma boa decisão ao
longo do dia são perguntas diferentes**. O projeto avalia as duas, separadamente.
O [replay causal](../operations/CAUSAL_REPLAY.md) detalha a regra executada e a
reprodução dos eventos em sua ordem de disponibilidade.

## Por que o momento da informação importa

No laboratório, o resultado de cada transação só pode ser usado pelo pipeline
**sete dias depois**. Esse retorno posterior é chamado **feedback**. O arquivo
simulado já contém o rótulo, mas o processamento respeita o instante em que ele
ficaria disponível segundo nosso protocolo.

Pense em uma compra no dia 1. Nesse dia, podemos pontuá-la com o histórico disponível.
O rótulo dessa compra só será liberado no dia 8. Se o cálculo das features do dia 1
usasse essa confirmação, estaria consultando o futuro. Esse erro é chamado
**vazamento de informação**: ele pode produzir uma avaliação excelente que não
seria possível repetir durante o uso.

Assim, separamos o momento da compra, o momento da decisão e o momento da
confirmação. Antes da confirmação, o resultado está pendente. Quando falamos em
**feedback amadurecido**, queremos dizer que o prazo de disponibilidade já passou
e os rótulos daquela população podem ser usados para avaliar o sistema. O atraso
fixo de sete dias é outra hipótese do laboratório, e não uma regra universal.

## Como saber se a priorização foi útil

Acertar a maioria das compras pode ser pouco informativo quando as fraudes são
raras. Em um exemplo com 1.000 compras, das quais 10 são fraudulentas, classificar
todas como legítimas daria 99% de acerto e deixaria passar as 10 fraudes.
Precisamos perguntar também **o que chegou à fila e o que ficou de fora**.

No nível de clientes-dia, duas medidas ajudam a responder:

- **Precisão:** entre os clientes encaminhados, quantos tiveram fraude naquele dia?
- **Recall, ou cobertura das fraudes:** entre os clientes que tiveram fraude naquele
  dia, quantos foram encaminhados?

Por exemplo, suponha que uma fila tenha alertado 10 clientes em um dia. Depois de
chegarem os rótulos, descobrimos que 4 deles tiveram fraude e que houve 5 clientes
com fraude no total. A precisão seria `4/10 = 40%`; o recall, `4/5 = 80%`. São
números ilustrativos: a primeira medida descreve a seleção feita; a segunda mostra
quanto do problema ela alcançou.

Para estudar a ordenação das **transações**, usamos também a **Average Precision
(AP)**. Ela resume como as fraudes se concentram nas posições de maior score ao
longo do ranking. AP e as medidas da fila têm unidades e perguntas distintas;
os resultados reais do projeto aparecem adiante, com seus respectivos protocolos.

Nossa contagem por cliente-dia pode considerar fraude ocorrida depois da transação
que disparou o alerta. Por isso, capturar um cliente-dia não prova que uma compra
fraudulenta foi impedida. Também não medimos dinheiro recuperado ou tempo gasto
na revisão. Esses resultados exigiriam dados sobre ações e consequências que a
simulação não fornece.

## O que MLOps acrescenta a essa pergunta

Além de obter um score, precisamos conseguir explicar de onde vieram os dados,
repetir a execução, acompanhar atrasos e erros e recuperar histórico e modelo após
uma falha. **MLOps** reúne as práticas de engenharia e operação usadas para manter
esse ciclo de dados, modelos e decisões organizado e verificável.

É essa a finalidade do laboratório: ligar a priorização dos casos à confiança na
execução. Os dados simulados permitem testar esses procedimentos em condições
controladas. Sua utilidade como estudo de caso depende de explicitar o que foi
verificado e quais suposições ainda limitam o resultado.

## Onde nosso modelo se encaixa

O Handbook apresenta um Sistema de Detecção de Fraudes (FDS) como combinação de
controles no terminal, regras de bloqueio, regras de pontuação, modelo orientado
por dados e investigação com feedback [2]. É uma organização conceitual; a fonte
não define arquitetura obrigatória ou SLA para este laboratório.

O **Histogram-based Gradient Boosting (HGB)** ocupa aqui o papel de modelo de
pontuação. As camadas Bronze, Silver e Gold fornecem dados e contratos; a política
transforma scores em encaminhamentos. O feedback simulado fecha a avaliação.
Autorizar uma transação não confirma sua legitimidade, e pontuar risco não resolve
a investigação. Não há investigadores reais nem um sistema de gestão de casos.

## O que os dados simulam, e o que deixam de fora

A origem é uma simulação pública do Handbook, fixada no commit
`6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a`. Nosso inventário verificou 183 partições,
1.754.155 transações e 14.681 fraudes. As datas de 2018 organizam o relógio dessa
simulação; não identificam transações bancárias reais daquele ano.

O gerador inclui três mecanismos: valor acima de 220 como padrão artificial de
teste, terminais comprometidos e aumento de valores para clientes comprometidos.
O próprio Handbook distingue o primeiro de um cenário realista [1]. Esse desenho
ajuda a interpretar sinais aprendidos, mas limita o que podemos generalizar.

A Bronze preserva bytes e proveniência; a Silver mantém as nove colunas de origem.
A Gold separa alvo/metadados dos 19 preditores. Identificadores brutos e
`TX_FRAUD_SCENARIO` não são entradas do modelo; o cenário revela informação do alvo.
Fórmulas e escolhas de janela constam no [contrato Gold](../data/GOLD_CONTRACT.md).
As ablações não passaram o gate de alteração e os 19 preditores foram mantidos,
conforme o [protocolo de experimentação](../modeling/EXPERIMENT_PROTOCOL.md).

Não temos dispositivo/IP, autenticação, canal de compra, contestações reais, grafo
de transferências ou decisões humanas. No protocolo atual, todos os rótulos acabam
disponíveis com atraso constante. Na prática, a seleção de casos para investigação
poderia determinar quais rótulos são observados; esse viés de seleção não é
simulado. Também não demonstramos resistência a adversários que adaptem seus ataques.

A simulação permite testar causalidade, integridade, repetição e recuperação em
condições controladas. O desempenho obtido nela não estima eficácia em outra
instituição, no Pix ou em golpes atuais. Generalização exigiria dados autorizados,
contratos e avaliação próprios do domínio de destino.

## Evidência científica e operacional

Os resultados abaixo têm populações e objetivos distintos. Seus detalhes e
limites pertencem à [Model Card](../modeling/MODEL_CARD.md) e aos protocolos.

| Evidência do projeto | Resultado observado | Limite de interpretação |
| --- | --- | --- |
| Teste final de maio, referência congelada antes do acesso | AP 0,640703; precisão diária top 100 de 55% | Teste já consumido; não reutilizar para selecionar candidatos |
| Avaliação de 02–15/09 | AP 0,621312; precisão diária top 100 de 54,93% | Avaliação e reamostragem exploratórias |
| Fila causal de 16–30/09 | 1.453 alertas; 768 clientes-dia fraudulentos capturados e 358 perdidos; precisão 52,86%, recall 68,21% | Reutilização operacional de janela consumida, sem nova confirmação de qualidade |
| Replay programado nativo a 8.640× | 556.824 eventos, 143.945 decisões; p95 processamento 0,023705 s e agendamento até commit 379,570 s | Sessões retomadas, relógio acelerado, transporte local sem broker; SLA não validado |
| Interrupção do exporter | Alerta observado em 56,36 s; recuperação em 12,29 s; estado preservado | Ensaio local inclui comandos e polling; não é garantia de disponibilidade |

No replay acelerado, o p95 de atraso de emissão foi 377,783 s. A diferença entre
esse atraso e o tempo de processamento mostra por que medir apenas a inferência
ocultaria o acúmulo na emissão. Os percentis não são somáveis. O ensaio não
caracteriza operação contínua a 1×, e retomar uma sessão estabelece nova âncora de
agendamento. Ver [observabilidade](../operations/OBSERVABILITY.md) para o escopo completo.

Precisão observada de 52,86% significa cerca de 47,14% dos alertas sem fraude
na unidade cliente-dia desse ensaio. O custo dessa revisão não foi medido. Comparar
com o ranking retrospectivo na mesma janela descreve políticas diferentes; não
constitui experimento aleatorizado nem demonstra superioridade confirmatória.
Mudanças de prevalência, rótulos atrasados, defeitos de features e mudança do
comportamento podem afetar métricas de formas diferentes.

As transações não são observações estatisticamente independentes: clientes e
terminais reaparecem e suas janelas históricas se sobrepõem. Agregar por cliente-dia
atende à política da fila, mas não elimina essas dependências. A interpretação
estatística segue os agrupamentos e limites declarados no protocolo.

## Maturidade atual e por que a infraestrutura existe

| Necessidade | Já exercitado no ambiente do autor | Critério ainda aberto |
| --- | --- | --- |
| Decisão reproduzível | Fonte/contratos fixados, tracking, modelo exportado, paridade e serving HTTP/Docker | Entrega gerenciada e rollback operacional |
| Estado e falhas | Replay durável, repetição sem novas decisões e retomada após interrupção | Operação contínua e transporte externo, se justificado |
| Visibilidade | Prometheus/Grafana, métricas de emissão/fila/processamento, qualidade madura e alerta de indisponibilidade | Validar limites de carga e respostas operacionais; ampliar suporte de métricas de features |
| Persistência recuperável | Publicação, repetição idempotente e restauração verificada no RustFS de Bronze/Silver/Gold/desenvolvimento, estudo HGB e snapshot MLflow; recarga da release restaurada e uso do histórico/modelo pelo SDK MLflow | Adotar destinos nos consumidores, ampliar a cobertura do inventário e provar rollback |
| Implantação reproduzível | Stack local em Compose com volumes e imagens fixadas | KinD/Helm/PVCs após critérios de persistência e recuperação |

O autor validou também o acesso aos dois históricos e a recarga de uma referência
pelo SDK MLflow: 71 runs, 67 modelos com metadados consultados, 321 registros de histórico de
métricas e erro de score zero. As localizações foram adaptadas somente em cópia
descartável; os restores permaneceram preservados. O ensaio usa SQLite local,
sem servidor HTTP ou migração para PostgreSQL.
[Evidência](../../references/evidence/mlflow_recovery_author_validation_2026-10-16.json).
Os [runbooks de armazenamento](../operations/STORAGE.md) e [MLflow](../operations/MLFLOW.md)
concentram comandos e evidências; arquivamento S3 ainda não é migração do
armazenamento primário.

RustFS atende à experimentação local com a API S3; seus volumes continuam na mesma
máquina. Isso não fornece uma cópia independente do laptop. PVCs organizam
persistência no Kubernetes, mas a recuperação de perda do cluster/disco depende de
outro destino e de restauração testada. O inventário de **toda** a persistência e a
eliminação de produtores sem destino gerenciado continuam metas abertas.

PostgreSQL tem justificativa como backend do futuro serviço MLflow compartilhado;
SQLite continua adequado ao replay local e ao estudo Optuna concluído enquanto
seus requisitos forem atendidos. DuckDB serve à validação/consulta analítica;
Prometheus guarda séries operacionais; S3 guarda objetos. Trocar todos esses papéis
por uma única ferramenta não resolve suas necessidades distintas.

Uma feature store será considerada se surgirem consumidores compartilhados,
necessidade de consulta online com frescor definido ou dificuldade demonstrada de
preservar paridade. Contratos, causalidade e observação de frescor vêm primeiro.
Drift de scores é um sinal descritivo para investigação; não comprova degradação e
não aciona retreino ou promoção automática. O [roadmap](ROADMAP.md) mantém prioridades
e gates, sem tornar uma lista de ferramentas um objetivo em si.

## Relevância e referências

Fraude em cartões é estudada há décadas: Ghosh e Reilly publicaram uma aplicação de
redes neurais em 1994 [3]. A escolha de ML precisa justificar o ganho de decisão,
e sua manutenção importa tanto quanto o algoritmo. O texto sobre dívida técnica
de Sculley e colegas [4] é uma leitura complementar para essa discussão.

Como contexto externo, o comunicado EBA/ECB de 15/12/2025 registra €4,2 bilhões de
fraude em pagamentos no Espaço Econômico Europeu em 2024, ante €3,5 bilhões em 2023
[5]. São diferentes instrumentos e outro mercado. Esses números não fornecem uma
estimativa de benefício, volume ou cobertura para nosso laboratório.

O diferencial pretendido é permitir que outra pessoa **reproduza uma decisão,
explique seus limites e recupere o sistema a partir de evidência verificável**.
O valor de portfólio dependerá dessa demonstração, da clareza do código e da
justificativa das escolhas. Não há evidência de eficácia antifraude em produção.

Simulador, FDS e comunicado EBA/ECB reconferidos em 2026-10-16; os resultados do
projeto remetem a evidências locais relatadas pelo autor. Esta revisão não cria nova avaliação nem altera gates
ou resultados históricos.

1. **Fraud Detection Handbook.** [Simulador](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html). Definições e mecanismos de geração.
2. **Fraud Detection Handbook.** [Sistema de detecção de fraude](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_2_Background/FDS.html). Papéis conceituais dos controles e da investigação.
3. **Ghosh, S.; Reilly, D. L. (1994).** *Credit card fraud detection with a neural-network.* HICSS. [DOI](https://doi.org/10.1109/HICSS.1994.323314). Referência histórica.
4. **Sculley, D. et al. (2015).** *Hidden Technical Debt in Machine Learning Systems.* [NeurIPS](https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf). Leitura complementar sobre manutenção de sistemas de ML.
5. **EBA/ECB (2025).** [Comunicado de 15 de dezembro](https://www.ecb.europa.eu/press/pr/date/2025/html/ecb.pr251215~e133d9d683.en.html). Contexto europeu, dados de 2022–2024.

Leituras complementares: [Dal Pozzolo et al., avaliação realista e feedback](https://doi.org/10.1109/TNNLS.2017.2736643),
[Carcillo et al., SCARFF](https://doi.org/10.1016/j.inffus.2017.09.005) e
[Tax et al., organização da investigação](https://arxiv.org/abs/2107.01979).
A arquitetura desses trabalhos não é requisito de infraestrutura para o projeto.
