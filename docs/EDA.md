# EDA da Silver

Estado: implementação testada em dados controlados. O autor informou a execução
real de treino, a verificação dos nove outputs e o resumo com quatro tabelas. A
CI e o commit da EDA ainda não foram informados.
EDA significa análise exploratória de dados. Esta etapa interpreta distribuições
e padrões na Silver; não cria uma nova camada de dados nem treina um modelo.

## Escopo e decisão

O [protocolo temporal](EVALUATION_PROTOCOL.md) é definido antes da exploração.
A EDA usa exclusivamente as partições de **treino: 2018-04-01 a 2018-04-28**.
Validação e teste não são expostos à sessão SQL exploratória. O verificador da
Silver continua conferindo integridade, schema e contagens do snapshot completo;
isso não abre suas distribuições para decidir features.

A Bronze preserva a fonte; a Silver oferece representação consistente; esta EDA
orienta as hipóteses para a futura Gold. Não removemos zeros, valores extremos,
clientes ou terminais. Os IDs de cliente/terminal são usados para resumir atividade,
e poderão agrupar históricos causais, mas não serão preditores numéricos brutos.
`TX_FRAUD_SCENARIO` serve somente à auditoria da simulação e nunca é feature.

## Executar e conferir

Na raiz do checkout, com Silver completa e dependências instaladas:

```bash
poetry run python -m fraud_detection_mlops.eda build
```

Também há o atalho `make eda`. O comando imprime `eda_path` e `audit_path`.
Confira o bundle substituindo o caminho abaixo pelo `eda_path` impresso:

```bash
poetry run python -m fraud_detection_mlops.eda verify \
  data/interim/handbook/<source_commit>/eda_v1/<run_id>
```

`verify` confere os bytes e a cobertura exata dos nove outputs; não recalcula
estatísticas nem reverifica a Silver de origem. Para a origem, use `silver verify`.
As opções de `build` são `--silver-root`, `--inventory`, `--contract` e `--protocol`.
A CLI não oferece seleção de uma janela de teste para exploração.

Cada execução publica um diretório novo em
`data/interim/handbook/<source_commit>/eda_v1/<run_id>/`. O UUID identifica a execução;
a versão `eda_v1` identifica o contrato do relatório. Repetir o comando preserva
relatórios anteriores e recalcula a análise. Nenhum download é feito.

## Ler os artefatos

| Arquivo | Pergunta respondida |
| --- | --- |
| `report.json` | Quantas transações, clientes, terminais e fraudes há no treino? Qual é o período observado? |
| `daily.csv` | Como volume e proporção de fraude variam entre os dias? |
| `hourly.csv` | Como volume e proporção de fraude variam com a hora? |
| `weekday.csv` | Como variam por dia da semana? ISO: segunda = 1, domingo = 7 |
| `amount_by_label.csv` | Como valores diferem entre classes? Mínimo, média, desvio populacional, mediana, p90, p99 e máximo |
| `amount_bins.csv` | Que fração de cada classe está nas faixas fixadas de valor? |
| `entity_activity.csv` | Qual a distribuição de transações por cliente e por terminal? |
| `scenarios_audit_only.csv` | Quais cenários da simulação aparecem? Informação exclusiva de auditoria |
| `training_overview.png` | Síntese visual do volume, prevalência e distribuição de valores no treino |
| `manifest.json` | Quais arquivos foram publicados, seus tamanhos e SHA-256? Quais versões e inputs sustentam a execução? |

CSV e JSON usam frações no intervalo [0, 1] para taxas; o gráfico exibe porcentagens.
As faixas são: zero, (0,10], (10,25], (25,50], (50,100], (100,250],
(250,500], (500,1000] e >1000. São descritivas, sem excluir extremos nem definir
limiar de classificação. O gráfico normaliza valores **dentro de cada classe**;
barras de mesma altura não significam o mesmo número de transações.
Quantis usam interpolação contínua do DuckDB. Horas seguem o timestamp sem timezone
da fonte; não reinterpretamos a simulação como horário de Belém nem atribuímos
uma moeda que o contrato não identifica. O painel usa Matplotlib com backend Agg,
sem exigir interface gráfica ou servidor de notebooks.

## Rastreabilidade e falhas

O módulo [eda.py](../fraud_detection_mlops/eda.py) verifica a Silver, seleciona apenas
os arquivos de treino, calcula agregações e reconcilia as linhas com o manifesto.
Confere cobertura diária e, antes de publicar, revalida hashes dos Parquets de
entrada, do manifesto Silver e do protocolo. Os outputs são escritos num staging
local e publicados juntos após o sucesso.

A auditoria `eda_v1/runs/<run_id>.json` registra resultado, horários UTC, hashes de
código, protocolo, contrato, inventário e manifesto de origem, além do ambiente.
`report.json` guarda o protocolo e os registros das partições efetivamente lidas.
A versão do código avaliado será associada no recibo ao fechar o marco; hashes
locais não são assinatura nem substituem o futuro commit Git do autor.

Falhas normais removem o staging e registram `failed` ou `interrupted`, preservando
bundles anteriores. Um encerramento abrupto pode deixar staging e auditoria `running`.
Confira a execução antes de usar um resultado. Não altere checksums para contornar
a verificação. Preserve os relatórios e auditorias com os dados; `data/` não vai ao Git.

## Interpretação e próximo passo

O [notebook da etapa](../notebooks/stages/03_silver_eda.ipynb) orienta a leitura e o
registro de achados reais. Primeiro observamos magnitude, denominadores e dinâmica;
depois formulamos hipóteses de features temporais de cliente/terminal.

Esta versão não realiza testes t, Mann–Whitney, KS ou testes de independência.
Transações compartilham clientes, terminais e tempo; p-valores que tratem todas as
linhas como observações independentes podem ser enganosos. Uma investigação
inferencial futura deve definir hipótese, unidade de análise, efeito de interesse e
tratamento dessa dependência antes do teste. Mudança descritiva diária não é, por
si só, um alarme de drift nem evidência causal.

O recibo de [preparação](../references/evidence/eda_preparation_2026-10-07.json)
registra os testes controlados. A execução informada pelo autor tem um
[recibo próprio](../references/evidence/eda_training_2026-10-07.json), com os achados abaixo.

## Achados informados pelo autor em 2026-10-07

Execução `733686de23204cd6b9a5a1ec1cfbc2e7`. A verificação informou `status: success`
e nove outputs íntegros. O autor compartilhou o resumo e quatro tabelas pelo terminal;
o [recibo](../references/evidence/eda_training_2026-10-07.json) transcreve essas saídas.
Os CSVs, o painel e a auditoria nativa não foram recebidos para inspeção direta.

O treino contém **268.668 transações, 1.505 fraudes (0,5602%)**, 4.954 clientes e
10.000 terminais. Todos os IDs de transação são distintos; os quatro valores zero
pertencem à classe genuína. Nenhuma dessas contagens é uma métrica de modelo.

### Volume e prevalência no tempo

O volume diário varia de 9.438 a 9.753 transações. Ao agregar blocos consecutivos
de sete dias, somando fraudes e transações antes de dividir, observamos:

| Bloco de abril de 2018 | Transações | Fraudes | Prevalência |
| --- | --- | --- | --- |
| 01/04 a 07/04 | 66.976 | 137 | 0,2046% |
| 08/04 a 14/04 | 67.312 | 334 | 0,4962% |
| 15/04 a 21/04 | 66.968 | 463 | 0,6914% |
| 22/04 a 28/04 | 67.412 | 571 | 0,8470% |

A prevalência cresce entre os quatro blocos, enquanto o volume diário permanece
numa faixa estreita. Isso evidencia mudança da proporção de positivos **dentro do
treino**; não comprova drift de desempenho. A dinâmica é compatível com a acumulação
inicial de clientes/terminais comprometidos do
[simulador do Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_3_GettingStarted/SimulatedDataset.html).
Essa explicação é uma inferência apoiada nas regras da fonte; as tabelas fornecidas
não atribuem causalmente o crescimento a cada mecanismo. A janela inicial ainda
pode apresentar uma fase de acumulação; não alteramos o protocolo depois dessa observação.

### Valores por classe

| Estatística de valor | Genuínas | Fraudes |
| --- | --- | --- |
| Média | 53,00 | 154,21 |
| Mediana | 44,57 | 91,92 |
| p90 | 109,36 | 375,46 |
| p99 | 166,66 | 734,18 |
| Mínimo | 0,00 | 0,49 |
| Máximo | 219,98 | 947,40 |

Valores são maiores na classe de fraude em média e nas caudas, mas há fraudes de
valor baixo. A média por classe mistura clientes e cenários; não estabelece o
comportamento de um cliente específico. A hipótese é avaliar tanto `TX_AMOUNT`
quanto seu desvio em relação ao histórico **anterior** do cliente, sem descartar
extremos nem converter as faixas descritivas em regras de classificação.

### Atividade e cenários de auditoria

Clientes têm mediana de 53 transações no período, com mínimo de uma e máximo de 130.
Terminais têm mediana de 26, com mínimo de seis e máximo de 67. Esses valores cobrem
28 dias; não garantem suporte suficiente em janelas de um ou sete dias. A Gold terá
que identificar histórico insuficiente e definir comportamento para primeiros eventos.

| Rótulo final do cenário | Fraudes | Fração das 1.505 fraudes |
| --- | --- | --- |
| 1 | 152 | 10,10% |
| 2 | 812 | 53,95% |
| 3 | 541 | 35,95% |

O cenário 2 predomina nos rótulos finais, motivando investigar histórico de terminal.
O cenário 3 motiva investigar hábitos de valor do cliente. Os cenários são metadados
finais da simulação: a geração pode sobrepor mecanismos e sobrescrever o rótulo de
cenário. Não interpretamos essa tabela como uma decomposição causal independente,
e `TX_FRAUD_SCENARIO` continua fora das features.

### Hipóteses para a próxima Gold

| Hipótese candidata | Evidência ou motivação | Regra que deverá ser testada |
| --- | --- | --- |
| Volume e média de valor do cliente em 1 e 7 dias | Heterogeneidade de atividade e diferença de valores entre classes | Usar somente eventos anteriores; indicar ausência de histórico |
| Valor atual relativo à média anterior do cliente | Fraudes têm cauda de valores maior, com sobreposição entre classes | Tratar histórico vazio e média zero sem remover transações |
| Volume de transações do terminal em 1 e 7 dias | Atividade por terminal e cenário 2 | Agregação causal, sem ID bruto como preditor |
| Fraude histórica conhecida no terminal | Regras do cenário 2, como hipótese da simulação | Usar rótulos somente depois do atraso de sete dias, com denominador elegível |

As hipóteses ainda não são features implementadas nem eficácia demonstrada.
Comparações e seleção ficam na validação. A EDA horária, semanal e o histograma
por faixa ainda não foram compartilhados, e não lhes atribuímos achados.
