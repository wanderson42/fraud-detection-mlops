# Síntese para stakeholders

Data do marco: 2026-10-06. Bronze avaliada: `18242fc`.
Diagnóstico completo posterior informado pelo autor; revisão desse código não
informada. A Silver foi construída e verificada no ambiente do autor, com
reconciliação completa dos registros. Revisão Silver: `acf1516`, com CI aprovada.
O autor informou validação local da integração tox; sua nova CI ainda não foi
informada. A EDA de treino foi informada pelo autor, com nove outputs verificados e quatro
tabelas interpretadas; validação e teste final permanecem reservados.

## Problema e objetivo do portfólio

Construir uma solução reproduzível de detecção de fraude em transações, acompanhando
os dados desde sua aquisição até a futura avaliação e operação do modelo. O projeto
pretende demonstrar classificação com regras temporais, engenharia de dados e
práticas de MLOps.

A base utilizada é simulada. Ainda não há modelo avaliado, estimativa de perdas
financeiras evitadas ou resultado comprovado em operação real.

## Entrega atual

Uma base histórica foi adquirida de uma versão fixa da fonte, preservando os arquivos
originais. O processo verifica integridade, reutiliza arquivos existentes, retoma
execuções parciais e registra auditorias.

| Resultado | Evidência e alcance |
| --- | --- |
| Histórico completo de 183 dias | Verificação local informada pelo autor: `183/183; complete: True` |
| Reutilização de dados | Execução completa reutilizou sete arquivos e baixou os 176 restantes |
| Qualidade do código | 70 testes locais aprovados na etapa Silver; ambiente, lint, formatação e diff conferidos |
| Automação de qualidade | CI aprovada para a implementação Silver `acf1516`; CI da integração tox ainda pendente |
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
| EDA do período de treino | Relatório real auditado, achados interpretados e hipóteses registradas |
| Features e avaliação temporal | Janelas explícitas, testes contra vazamento e comparação com baseline |
| Modelo de classificação | Métricas relevantes ao problema, escolha de limiar, limitações e Model Card |
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
Não há nova métrica de um modelo ajustado para comunicar.
