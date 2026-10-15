# Síntese para stakeholders

Atualizado em 2026-10-15, incorporando a execução informada em 2026-10-13.
O pipeline offline, a referência congelada, o serving de laboratório e o replay
causal acelerado foram exercitados pelo autor. O replay corretivo passou a
verificação de features, scores e decisões. [Metas e prioridades](ROADMAP.md).

## A decisão que apoiamos

Uma equipe com capacidade limitada precisa escolher quais clientes investigar
usando somente o histórico disponível naquele instante. O laboratório transforma
scores em uma fila com até 100 clientes por dia, registra a decisão e avalia o
resultado quando os rótulos chegam sete dias depois.

Os dados são simulados pelo Fraud Detection Handbook. O projeto demonstra
engenharia e governança de ML; não comprova eficácia contra golpes reais,
perdas evitadas ou autorização para bloquear pagamentos. Investigação envolve
também regras, autenticação e revisão humana. [Contexto](PROBLEM_CONTEXT.md).

## O que está demonstrado

HGB significa Histogram-based Gradient Boosting, implementado pelo
`HistGradientBoostingClassifier`. A referência foi mantida pelos resultados dos
gates, sem assumir que novos algoritmos necessariamente melhorariam a triagem.

| Capacidade | Evidência e alcance |
| --- | --- |
| Dados rastreáveis | 183 arquivos íntegros; Silver com 1.754.155 transações, 14.681 fraudes e 42 valores monetários zero preservados |
| Avaliação temporal | 19 preditores causais; treino, validação e teste separados; avaliação final de maio: AP 0,640703 e precisão diária @100 de 55%, sem refit |
| Seleção controlada | Três ablações e 20 trials Optuna; nenhum candidato passou os gates, conservando o HGB congelado |
| Modelo distribuível | MLflow/skops, release identificada, HTTP/wheel fora do checkout e Docker validados pelo autor |
| Decisão por eventos | 556.824 eventos e 143.945 decisões no replay; feedback separado, histórico causal e checkpoint durável |
| Consistência corrigida | Igualdade exata dos 19 atributos e paridade dos scores aprovada sem ampliar tolerância; execução original preservada |
| Qualidade da fila emitida | 1.453 alertas, 768 clientes-dia fraudulentos capturados e 358 perdidos na janela de 16–30/09 |
| Qualidade de implementação | Preparação da correção: 595 testes/93 avisos, tox, lint, formatação e lockfile aprovados com dados gerados |

## O resultado para a investigação

Em 15 dias, houve **1.126 clientes-dia fraudulentos**. Um mesmo cliente pode contar
em dias diferentes. Na fila causal, 768 foram capturados e 358 ficaram fora;
dos 1.453 alertas emitidos, 685 corresponderam a clientes-dia sem fraude.

| Indicador | Fila causal, no instante da decisão | Top-100, após observar o dia |
| --- | ---: | ---: |
| Alertas | 1.453 | 1.500 |
| Clientes-dia fraudulentos capturados | 768 | 800 |
| Clientes-dia fraudulentos perdidos | 358 | 326 |
| Precisão agregada | 52,86% | 53,33% |
| Recall agregado | 68,21% | 71,05% |

Precisão agregada divide capturas pelo total de alertas efetivamente emitidos;
recall agregado divide capturas pelos 1.126 clientes-dia fraudulentos. Esses
valores não são médias diárias nem usam transações como denominador.
Uma captura considera qualquer fraude naquele cliente-dia, inclusive posterior
ao alerta; não significa que a transação disparadora era fraudulenta.

A fila utilizou **96,87% das 1.500 vagas** possíveis: 47 ficaram ociosas e o
orçamento esgotou em 9 dias. Houve 91 clientes-dia elegíveis suprimidos pelo limite;
essa contagem não identifica, por si só, fraudes perdidas.
O retrospectivo capturou 32 clientes-dia fraudulentos adicionais, dispondo do dia
completo para ordenar. A comparação é descritiva, sem superioridade formal.

## O incidente de paridade e sua resolução

A execução original processou todos os eventos, mas reprovou 2/143.945 scores.
Diferenças minúsculas nas médias calculadas por SQLite/DuckDB atravessaram cortes
das árvores. A regra versionada soma exatamente os valores binários e arredonda
uma vez depois da divisão. O replay corretivo passou o verificador independente
com o mesmo modelo, limiar e tolerância.

Em relação à execução original, apenas um score persistido mudou. Não houve
mudança de elegibilidade, status de triagem ou dos 1.453 alertas. O checkpoint
original conservou o hash. Essa correção demonstra diagnóstico, regressão e
recuperação auditável, sem substituir a evidência da falha anterior.

## O que continua em aberto

| Próximo marco | Evidência necessária |
| --- | --- |
| Operação no ritmo de chegada | Emissão programada, filas/lag, latência desde emissão, erros e recuperação cronometrada |
| Observabilidade | Saúde do serviço, dados e scores; qualidade com rótulos amadurecidos; alertas e resposta a falhas |
| Entrega e reversão | Imagem/modelo identificados, gates e demonstração de rollback |
| Atualização controlada | Gatilhos, orçamento, disponibilidade de rótulos e avaliação temporal do candidato antes de promoção |

O ensaio aprovado foi serial, local e acelerado. Não comprova ritmo 1x, SLA,
transporte distribuído ou disponibilidade contínua. O p95 HTTP de 4,74 ms do
Docker é uma medição anterior de serving sequencial e não a latência ponta a
ponta do replay. Os arquivos de custos por invocação não foram fornecidos para
consolidação. Não estimamos retorno financeiro, fraude evitada ou efeito sobre
clientes reais. Todos os rótulos chegam após sete dias no cenário assumido;
investigação seletiva e atrasos variáveis de feedback continuam fora do ensaio.

Maio, 02–15/09 e 16–30/09 já foram consumidos em seus respectivos protocolos.
AP 0,578783 no replay e AP 0,621312 na primeira quinzena pertencem a janelas
diferentes; essa diferença isolada não prova degradação. Novas hipóteses de
qualidade exigem desenho próprio e uma janela de avaliação preservada.

## Evidências e leitura

A [evidência do replay](../../references/evidence/native_numerical_correction_author_validation_2026-10-13.json)
reconcilia as duas saídas de terminal (`run` e `verify`) fornecidas pelo autor.
O assistente conferiu igualdade dos relatórios, contagens e taxas agregadas;
não inspecionou os bytes dos checkpoints, scores ou medições no computador do autor.
A publicação da implementação `c819d65` e sua [CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/38487732538)
foram conferidas separadamente. O arquivo de terminal não comprova
independentemente o SHA exato do checkout usado na execução.

[Model Card](../modeling/MODEL_CARD.md),
[runbook corretivo](../operations/NUMERICAL_CORRECTIVE_REPLAY.md#resultado-nativo-informado-pelo-autor--2026-10-13),
[notebook principal](../../notebooks/fraud_detection_mlops.ipynb) e
[política de documentação](DOCUMENTATION_POLICY.md) mantêm método, origem e limites.
Os recibos históricos permanecem preservados; não há promoção em produção.
