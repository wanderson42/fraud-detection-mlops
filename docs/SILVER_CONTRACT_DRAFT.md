# Contrato da Silver — diagnóstico e rascunho

Registro histórico anterior ao diagnóstico completo. O contrato vigente está em
[Silver contract](SILVER_CONTRACT.md), que incorpora a auditoria dos 183 arquivos e
a decisão de preservar os valores zero. As propostas abaixo descrevem o estado
anterior à implementação da conversão. A [política de documentação](DOCUMENTATION_POLICY.md)
rege as evidências e a atualização desta etapa.

## Evidência inicial

O autor inspecionou `2018-04-01.pkl` com pandas 3.0.6, após verificar a integridade
completa da Bronze. Foram informadas 9.488 linhas e nove colunas, sem nulos, linhas
duplicadas ou IDs de transação repetidos nessa partição. Há 9.485 transações com
rótulo 0 e três com rótulo 1, aproximadamente 0,0316% nesse dia. Essa proporção não
representa necessariamente todo o histórico.

`CUSTOMER_ID`, `TERMINAL_ID`, `TX_TIME_SECONDS` e `TX_TIME_DAYS` têm dtype `object`,
mas todos os valores observados são inteiros Python. Os detalhes estão no
[recibo da amostra](../references/evidence/silver_first_partition_2026-10-06.json) e
no [notebook da etapa](../notebooks/stages/02_silver_data_contract.ipynb).

## Schema candidato

| Coluna | Representação candidata | Papel |
| --- | --- | --- |
| `TRANSACTION_ID` | `int64` | Identificador da transação; unicidade global a conferir |
| `CUSTOMER_ID` | `int64` | Identificador do cliente |
| `TERMINAL_ID` | `int64` | Identificador do terminal |
| `TX_DATETIME` | `datetime64[ns]` | Instante da transação conforme a fonte |
| `TX_AMOUNT` | `float64` | Valor da transação preservado |
| `TX_TIME_SECONDS` | `int64` | Segundos desde a origem temporal da simulação |
| `TX_TIME_DAYS` | `int64` | Dias desde a origem temporal da simulação |
| `TX_FRAUD` | `int8` | Rótulo binário para avaliação supervisionada |
| `TX_FRAUD_SCENARIO` | `int8` | Metadado da simulação para auditoria |

O schema é candidato: a auditoria dos 183 arquivos deve confirmar ou alterar essas
decisões. IDs permanecem identificadores, mesmo quando representados como inteiros;
a tipagem não os transforma em variáveis contínuas para o modelo.

A fonte observada contém timestamps sem timezone. O contrato deve explicitar essa
convenção; os horários UTC das auditorias Bronze não determinam o timezone dos eventos.
No gerador do [Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html),
`TX_FRAUD_SCENARIO` é marcado junto ao rótulo. Será preservado como metadado de
auditoria e excluído das entradas preditivas do futuro modelo.

## Auditoria anterior à conversão

O módulo `fraud_detection_mlops.profiling` confere primeiro a cobertura e integridade
da Bronze completa. Depois lê os arquivos um por vez, descreve tipos, nulos, rótulos
e contagens e procura:

- IDs repetidos dentro de uma partição e entre partições anteriores;
- valores não representáveis como inteiros `int64`, incluindo frações e não finitos;
- identificadores negativos, timestamps inválidos e registros fora da data do arquivo;
- diferenças entre timestamp e contadores de segundos/dias;
- rótulos fora dos conjuntos esperados e inconsistência entre fraude e cenário;
- valores monetários não finitos, negativos ou iguais a zero;
- linhas duplicadas e inversões na ordem temporal da partição.

A auditoria consulta os valores em representações temporárias para diagnóstico; não
altera a Bronze, converte arquivos, elimina linhas, imputa valores nem remove extremos.
Schema diferente das nove colunas, timezone não previsto, falha de leitura ou Bronze
incompleta interrompem o diagnóstico e exigem investigação.

Os contadores de verificações podem se sobrepor: um nulo pode aparecer em mais de
uma categoria. Não some esses contadores como se fossem quantidade de linhas ruins.
Valores zero e inversões de ordem são observações para revisão, sem decisão automática
de exclusão. As regras finais definirão se cada achado bloqueia, exige correção ou
quarentena, com reconciliação de registros.

## Relatório e reprodução

Comandos e opções estão em [Operations](OPERATIONS.md#diagnóstico-preparatório-da-silver).
O relatório padrão fica em `data/interim/handbook/<source_commit>/profile.json`, dentro
de `data/`, ignorado pelo Git. Ele contém resumo e resultados por partição, origem,
hashes do inventário e do código do profiler, ambiente e horário UTC de geração.
O comando substitui esse relatório diagnóstico ao repetir a execução; use
`--report-path` para guardar uma cópia distinta.

Esse JSON é um perfil exploratório, não um certificado de aceitação da Silver.
Resultados reais da auditoria serão sintetizados em novo recibo e no notebook,
após a execução do autor. Os testes automatizados usam pequenas bases controladas,
sem baixar o dataset.

## Próximas decisões

1. Interpretar o diagnóstico completo e fixar as regras de qualidade.
2. Definir paths, schema Parquet, política de registros inválidos e reconciliação.
3. Implementar e testar a transformação com preservação de proveniência.
4. Validar a Silver e registrar as consultas de conferência em DuckDB.

A exploração voltada a features e decisões de modelagem respeitará janelas temporais
de treinamento e avaliação. A auditoria de integridade e consistência pode cobrir
toda a fonte; os dados de avaliação final permanecerão reservados para esse fim.
