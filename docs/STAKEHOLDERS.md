# Síntese para stakeholders

Data do marco: 2026-10-06. Implementação avaliada: `18242fc`.

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
| Qualidade do código | 39 testes locais aprovados; checks de ambiente, lint e formatação aprovados |
| Automação de qualidade | CI aprovada para o commit da implementação |
| Rastreabilidade | Fonte fixada, inventário versionado, manifesto, auditorias e recibo documental |

As evidências e suas origens constam no [recibo da Bronze](../references/evidence/bronze_2026-10-06.json).
Os resultados locais foram informados pelo autor; a CI foi conferida por consulta
à execução na plataforma. Os artefatos locais de dados não foram publicados no Git.

## Significado do marco

O projeto tem uma base reproduzível para iniciar a preparação dos dados e permite
investigar como seus arquivos foram obtidos. Essa validação cobre a aquisição e a
integridade dos arquivos; a qualidade das transações ainda será investigada.

## Próximos marcos e evidências necessárias

| Marco planejado | Evidência necessária para considerá-lo validado |
| --- | --- |
| Silver em Parquet | Contrato de schema, qualidade de dados, reconciliação de registros e consultas DuckDB |
| Features e avaliação temporal | Janelas explícitas, testes contra vazamento e comparação com baseline |
| Modelo de classificação | Métricas relevantes ao problema, escolha de limiar, limitações e Model Card |
| Replay/streaming e operação | Processamento de eventos, controles de duplicidade e atraso, observabilidade e ensaios operacionais |

Streaming, feature store, serving e monitoramento fazem parte da evolução pretendida;
a arquitetura e os critérios finais serão definidos por etapa. Documentos técnicos
e evidências acompanham a implementação, conforme a [política](DOCUMENTATION_POLICY.md).
