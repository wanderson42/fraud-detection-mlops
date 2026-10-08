# Síntese para stakeholders

Data do marco: 2026-10-06. Bronze avaliada: `18242fc`.
Diagnóstico completo posterior informado pelo autor; revisão desse código não
informada. A Silver foi construída e verificada no ambiente do autor, com
reconciliação completa dos registros. Revisão Silver: `acf1516`, com CI aprovada.
O autor informou validação local da integração tox; a entrega tox e EDA foi
publicada na revisão `fc22a48`, com CI aprovada. A EDA de treino foi informada pelo autor, com nove outputs verificados e quatro
tabelas interpretadas; validação e teste final permanecem reservados.

## Problema e objetivo do portfólio

Construir uma solução reproduzível de detecção de fraude em transações, acompanhando
os dados desde sua aquisição até a futura avaliação e operação do modelo. O projeto
pretende demonstrar classificação com regras temporais, engenharia de dados e
práticas de MLOps.

A base utilizada é simulada. O baseline foi comparado na validação; não há
estimativa de perdas financeiras evitadas ou resultado comprovado em operação real.

## Entrega atual

Uma base histórica foi adquirida de uma versão fixa da fonte, preservando os arquivos
originais. O processo verifica integridade, reutiliza arquivos existentes, retoma
execuções parciais e registra auditorias.

| Resultado | Evidência e alcance |
| --- | --- |
| Histórico completo de 183 dias | Verificação local informada pelo autor: `183/183; complete: True` |
| Reutilização de dados | Execução completa reutilizou sete arquivos e baixou os 176 restantes |
| Qualidade do código | 70 testes locais aprovados na etapa Silver; ambiente, lint, formatação e diff conferidos |
| Automação de qualidade | CI aprovada para Silver `acf1516` e para integração tox/EDA `fc22a48` |
| Rastreabilidade | Fonte fixada, inventário versionado, manifesto, auditorias e recibo documental |
| Diagnóstico do histórico | 1.754.155 transações com IDs globalmente únicos; auditoria local informada pelo autor |
| Frequência de fraude | 14.681 rótulos de fraude, aproximadamente 0,8369% da base; não é uma métrica de modelo |
| Política de qualidade da Silver | Preservar os 42 valores zero; tipos explícitos e reconciliação de todas as linhas |
| Silver completa | 183 partições Parquet construídas e verificadas; todas as contagens reconciliadas com a Bronze |

As evidências e suas origens constam no [recibo da Bronze](../references/evidence/bronze_2026-10-06.json).
Os resultados locais foram informados pelo autor; a CI foi conferida por consulta
à execução na plataforma. Os artefatos locais de dados não foram publicados no Git.

## Significado do marco

O projeto tem uma base reproduzível para iniciar a preparação dos dados e permite
investigar como seus arquivos foram obtidos. Essa validação cobre a aquisição e a
integridade dos arquivos. O diagnóstico posterior também investigou schema, nulos,
unicidade, datas, rótulos e valores, com resultados no
[recibo do perfil](../references/evidence/silver_profile_2026-10-06.json).
A construção e a verificação da Silver foram concluídas pelo autor. O
[recibo da execução](../references/evidence/silver_build_2026-10-06.json) registra
esse marco e sua auditoria. A base está disponível para consultas e a próxima
etapa de análise e preparação temporal das features.

## Próximos marcos e evidências necessárias

| Marco planejado | Evidência necessária para considerá-lo validado |
| --- | --- |
| Tracking dos modelos reais | Três runs independentes, scores reconciliados e artefatos persistentes |
| Diagnóstico e comparação estatística | Hipóteses registradas, tamanho de efeito, incerteza e dependência temporal tratados |
| Otimização e avaliação final | Escolhas congeladas antes do teste, critérios de aprovação e limitações documentados |
| Replay/streaming e operação | Processamento de eventos, controles de duplicidade e atraso, observabilidade e ensaios operacionais |

Streaming, feature store, serving e monitoramento fazem parte da evolução pretendida;
a arquitetura e os critérios finais serão definidos por etapa. Documentos técnicos
e evidências acompanham a implementação, conforme a [política](DOCUMENTATION_POLICY.md).

## EDA informada e próxima Gold

A [EDA](EDA.md) gera tabelas e gráficos a partir da Silver sem alterar transações.
O [protocolo temporal](EVALUATION_PROTOCOL.md) estabelece janelas e atraso de rótulos
antes de escolher o modelo. O autor informou 268.668 transações de treino, 1.505 fraudes (0,5602%) e quatro
valores zero preservados, no [recibo](../references/evidence/eda_training_2026-10-07.json).
A proporção de fraude aumenta entre os blocos semanais, motivando históricos
temporais de cliente/terminal. Acurácia de 99,44% seria possível prevendo tudo como
genuíno, sem detectar fraude; isso sustenta avaliar o ranking com Average Precision.
Nesse marco de EDA não foram avaliados modelos; o resultado posterior está abaixo.

## Gold construída e verificada pelo autor

O pipeline preparado transforma a Silver em tabelas temporais de modelagem, com
19 preditores e regras para histórico insuficiente. Preserva a população das
janelas definidas e separa alvo/IDs das features. O risco histórico do terminal
considera apenas rótulos disponíveis após sete dias. O [contrato](GOLD_CONTRACT.md)
explica as decisões. O autor informou 42 partições verificadas, 19 preditores e
402.877 linhas: 268.668 de treino, 67.255 de validação e 66.954 de teste, com
[recibo](../references/evidence/gold_build_2026-10-07.json). A preparação passou
104 testes controlados. A revisão Gold `14ab57e` foi publicada com
[CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37652509812):
104 testes em 11,37 s, lint, formatação e lockfile aprovados. O notebook publicado
mostra a verificação e a leitura das 268.668 linhas de treino. Checks locais do autor
continuam sem saída de terminal informada.
Esses resultados da engenharia de dados não demonstram eficácia de detecção. O baseline abaixo foi avaliado com
métricas de ranking e seleção exclusivamente na validação.

## Desenho do baseline

O experimento compara uma referência sem sinal e dois classificadores:
linear e não linear. Os modelos são ajustados no treino e comparados na validação
pela capacidade de ordenar transações por risco. O teste final permanece reservado.
Escala e pesos de classe são ajustados apenas no treino, com população preservada.
O [baseline](BASELINE.md) e seu notebook documentam configurações e interpretação.
O resultado real informado pelo autor está abaixo; revisão publicada e CI desse
código ainda não foram informadas.

## Baseline real e tracking

O autor informou treinamento e verificação reais: o gradient boosting atingiu AP
0,623949, com precisão média de 54% nos cem clientes priorizados por dia; regressão
logística: AP 0,435001 e 48%. A validação contém 580 fraudes em 67.255 transações,
em sete dias. [Resultados e limites](BASELINE.md#resultado-real-informado-pelo-autor).
Esse ganho observado não representa superioridade estatística nem produção validada.

O autor publicou e verificou os três pipelines reais em MLflow/skops, sem refit
ou avaliação do teste. [Evidência local](../references/evidence/mlflow_execution_2026-10-07.json).
Os 135 testes passaram em 12,91 s; tox completo em 18,52 s. Os nove avisos de
depreciação são externos aos contratos do projeto e permaneceram visíveis.
Modelos e pré-processamento ficam juntos em runs próprias. Não há promoção automática;
commit, CI e validação da UI real ainda não foram informados.
O próximo trabalho de modelagem investigará erros e importância de features, com
protocolo estatístico definido antes da otimização e do quality gate.
