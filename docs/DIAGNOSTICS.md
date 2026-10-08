# Diagnóstico da baseline na validação

Estado: execução local e verificação informadas pelo autor em 2026-10-07,
com sete outputs verificados. Versão `diagnostics_v1`. O modelo publicado permanece congelado,
assim como o [protocolo temporal](EVALUATION_PROTOCOL.md). Esta etapa formula
hipóteses para os próximos experimentos; não seleciona features automaticamente.

## Escopo e custo

Um módulo, `modeling/diagnostics.py`, usa as APIs nativas de scikit-learn e SHAP.
Não refaz a Bronze/Silver/Gold nem ajusta modelos. Lê os scores dos três candidatos
e somente as features da validação: 67.255 transações na execução real existente.
Os arquivos Gold de treino e teste não precisam ser abertos por este comando.

| Diagnóstico | População | Interpretação |
| --- | --- | --- |
| Métricas e erros por dia | Validação completa, três candidatos | AP, ROC AUC, prevalência e clientes encontrados/perdidos sob a política de 100 alertas |
| Importância por permutação | Validação completa, HGB | Queda de AP ao embaralhar uma coluna, cinco repetições com semente 42 |
| Importância SHAP | Amostra uniforme de 1.000 transações da validação | Média do valor SHAP absoluto em unidades de log-odds |
| Casos ilustrativos SHAP | Maior score genuíno, menor score fraudulento e maior score fraudulento | Inspeção local; não entram adicionalmente na média global |

A versão inicial explica apenas o pipeline HGB sem transformações, escolhido na
baseline real. Em outra execução, o HGB pode não ser o vencedor: o relatório
registra separadamente `model_id` e `baseline_selected_model`. Não há fallback
silencioso para outro modelo ou explicador. Os resultados diários cobrem os três.

O padrão realiza 95 embaralhamentos de features, além dos scores de referência.
`--repeats` aceita 2 a 10; `--sample-size` aceita 1 a 2.000, sem ultrapassar a
população. Usamos um processo e até quatro threads numéricas. As 19 features da
validação entram em memória. O tempo registrado descreve a execução local; não
é um benchmark de latência, memória máxima ou custo de produção.

## Executar e conferir

Após aplicar o patch e instalar seu lockfile:

```bash
poetry install
make validate

BASELINE_PATH="data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/baseline_v1/f6d7aca720f74316b4183f97b6d866ab"
MODEL_URI="runs:/d3be86000fd24dc8a053dbcab778d4b6/model"

poetry run python -m fraud_detection_mlops.modeling.diagnostics run \
  "$BASELINE_PATH" --model-uri "$MODEL_URI"

poetry run python -m fraud_detection_mlops.modeling.diagnostics verify \
  "<diagnostics_path retornado>"
```

Esses IDs identificam a execução real já informada pelo autor. Para outra execução,
use o caminho e a URI do HGB retornados por `train run`, sem escolher a última pasta
automaticamente. A CLI também aceita `--gold-root`, `--tracking-root` e `--output-root`.
Os exemplos assumem os contratos padrão do projeto; a API Python aceita os mesmos
contratos alternativos do verificador da baseline para fixtures ou investigação.

Não é necessário repetir o treinamento histórico. O modelo vem do MLflow em skops;
os joblibs da baseline v1 são apenas verificados como bytes. A recarga confere tipos,
assinatura e formato. A run deve estar `FINISHED`, vinculada ao candidato, à origem
e à Gold corretos. O export histórico também precisa apontar para o manifesto da
baseline. Antes de calcular importâncias, conferimos **todos os scores da validação**
com tolerâncias `rtol=atol=1e-12`. Diferença interrompe o diagnóstico.

Use as versões de scikit-learn, NumPy e Pandas da baseline. A inclusão de
`shap==0.52.0` não atualiza versões existentes do lockfile. SHAP adiciona dependências
de execução como Numba/LLVM; não há exigência de GPU. Os testes executam o explicador
real no Python 3.14, além de conferir a recarga do export histórico sem `run_id` no
`MLmodel`. Essa compatibilidade é leitura; o runner de migração continua removido.

## Artefatos e proveniência

Cada execução gera um UUID em
`data/processed/handbook/<source_commit>/diagnostics_v1/<run_id>/`.

| Arquivo | Conteúdo |
| --- | --- |
| `daily.csv` | Contagens, métricas diárias e erros operacionais por modelo |
| `permutation.csv` | Queda de AP por feature, média, desvio padrão e cada repetição |
| `shap_summary.csv` | Importância absoluta média da amostra uniforme |
| `shap_values.parquet` | IDs, metadados, features, scores, contribuições e identificação dos casos/amostra |
| `importance.png` | Permutação e importância SHAP lado a lado, com escalas distintas |
| `case_lowest_scored_fraud.png` | Waterfall da fraude de menor score da validação |
| `report.json` | Baseline/modelo de origem, hashes, ambiente, orçamento e limites do diagnóstico |
| `manifest.json` | Tamanho e SHA256 dos sete outputs |

A auditoria fica em `runs/diagnostics_<run_id>.json`. Os outputs só são publicados
após a conferência; falhas registram o erro e limpam o staging. Inputs inválidos
detectados antes da auditoria não criam recibo. Repetir gera outra pasta; não modifica
o experimento nem cria novas runs no MLflow. O vínculo é registrado pela URI e pelo
ID da run. Não comite `data/` nem os artefatos binários.

`verify` confere inventário de outputs, hashes e declarações de escopo. Não recalcula
permutação ou SHAP, não recarrega o modelo e não autentica a origem. A reconciliação
dos scores e a aditividade são verificadas durante `run`.
O [notebook 07](../notebooks/stages/07_baseline_diagnostics.ipynb) lê uma execução
explícita e ajuda a registrar a interpretação. Ele tem responsabilidade diferente
do [notebook 06 de tracking](../notebooks/stages/06_mlflow_tracking.ipynb).

## Interpretar com responsabilidade

- AP é uma métrica de ranking; mudanças na prevalência dificultam comparações entre
  dias. Observe contagens e prevalência junto das métricas.
- Na política operacional, cada cliente recebe o máximo score diário e o máximo
  rótulo diário; empates seguem `CUSTOMER_ID` crescente. Cliente genuíno nos alertas
  e cliente fraudulento fora dos alertas são os erros operacionais dessa política.
  Isso não define um limiar para classificar transações.
- Permutação mede a dependência do modelo ajustado, não o valor causal da feature.
  Features correlacionadas podem substituir umas às outras. Embaralhar valores
  derivados separadamente pode produzir combinações que não ocorrem na Gold.
  A inspeção não equivale a retirar uma feature e retreinar o modelo.
- O desvio entre embaralhamentos **não é intervalo de confiança nem p-valor**.
  As repetições não representam novas amostras independentes de dias ou clientes.
- SHAP usa `tree_path_dependent`, com contagens dos caminhos das árvores como
  referência do treino. Não precisa de uma nova amostra de background. Os valores
  somam, com o valor base, o `decision_function` do HGB; aplicar a logística
  reconstrói o score. Não são contribuições em pontos percentuais de probabilidade,
  nem comprovam causalidade ou calibração.
- A amostra global é uniforme, sem balanceamento pelos rótulos. Os três casos
  extremos são ilustrativos e identificados separadamente. Se um deles já estiver
  na amostra uniforme, participa dela uma única vez.

Registre padrões recorrentes e explicações plausíveis antes de planejar ablações.
O próximo protocolo deverá fixar hipóteses, tamanho de efeito relevante, orçamento
e quality gates **antes** do tuning. Comparações pareadas precisarão considerar
dependência temporal e clientes repetidos. Apenas sete dias de validação limitam
a inferência. Esta etapa não calcula p-valores e mantém o teste final reservado.

Referências primárias:

- [Scikit-learn: importância por permutação](https://scikit-learn.org/stable/modules/permutation_importance.html).
- [Scikit-learn: API e orçamento de permutação](https://scikit-learn.org/stable/modules/generated/sklearn.inspection.permutation_importance.html).
- [SHAP: TreeExplainer, dependência e unidades de saída](https://shap.readthedocs.io/en/latest/generated/shap.TreeExplainer.html).

Evidência de preparação: [recibo](../references/evidence/diagnostics_preparation_2026-10-07.json).

## Resultados

Run `961bb0c32fb84433af922908ea58e76b`, HGB publicado em
`runs:/d3be86000fd24dc8a053dbcab778d4b6/model`, cujo o [recibo da execução](../references/evidence/diagnostics_execution_2026-10-07.json)
transcreve as três tabelas e a validação local: **142 testes aprovados, 29 avisos
externos**, com lint, formatação e lockfile aprovados.

Os números abaixo vêm do terminal. Os arquivos nativos, seus hashes e o notebook
atualizado não foram enviados nesta etapa; não houve reexecução independente sobre
os artefatos reais. As importâncias e métricas diárias foram exibidas com seis casas
decimais. Os recibos anteriores continuam sendo registros dos respectivos marcos.

### Leitura operacional, além da AP

Na validação de 6 a 12 de maio, o HGB superou a regressão logística em AP e precisão
nos cem clientes em todos os sete dias. Sua AP diária variou de 0,496161 a 0,751130;
a precisão variou de 45% a 60%. A AP da semana completa é 0,623949, conforme a
[baseline](BASELINE.md); ela não é a média das APs diárias.

| Agregado dos sete dias | Controle constante | Regressão logística | HGB |
| --- | --- | --- | --- |
| Posições de alerta | 700 | 700 | 700 |
| Ocorrências fraudulentas de cliente/dia nos alertas | 15 | 336 | 378 |
| Ocorrências genuínas de cliente/dia nos alertas | 685 | 364 | 322 |
| Ocorrências fraudulentas de cliente/dia fora dos alertas | 496 | 175 | 133 |
| Precisão média diária | 2,14% | 48% | 54% |

Há 511 ocorrências fraudulentas de cliente/dia. Encontrados = soma de
`100 × precision_at_100`; fora dos alertas = soma de `missed_fraudulent_customers`.
O orçamento e o denominador são os mesmos em cada dia. Assim, o HGB priorizou
**42 ocorrências fraudulentas adicionais**, com 42 ocorrências genuínas a menos nos
alertas, comparado à regressão. Uma pessoa pode aparecer em vários dias. Esses
números não representam pessoas únicas, transações bloqueadas ou perdas evitadas.

São resultados retrospectivos na simulação. Usamos o máximo score do cliente no
dia completo; ainda não existe uma fila online com decisões sob prazo. A vantagem
nos sete dias é descritiva: dias e clientes não são independentes, e não calculamos
significância estatística. As mudanças diárias tampouco provam drift sem investigar
composição, prevalência e incerteza.

### Dependências do ranking e limites da explicação

| Feature | Queda média de AP por permutação | Desvio entre repetições |
| --- | --- | --- |
| `TX_AMOUNT` | 0,304394 | 0,002357 |
| `TERMINAL_KNOWN_FRAUD_RATE_7D` | 0,216758 | 0,001256 |
| `CUSTOMER_AMOUNT_RATIO_7D` | 0,065655 | 0,003558 |

O modelo congelado depende fortemente de valor, risco conhecido do terminal e
desvio do padrão de gastos. Isso é compatível com os mecanismos do simulador:
limiar artificial de valor, terminais comprometidos e aumento de gastos do cliente.
É uma interpretação exploratória, não uma prova causal ou descoberta de padrões
criminosos reais. O [contexto do problema](PROBLEM_CONTEXT.md#o-que-os-dados-simulam-e-o-que-deixam-de-fora)
explica por que essa correspondência importa para avaliar o portfólio.

SHAP responde a outra pergunta: contribuição absoluta para o score em log-odds
na amostra uniforme. `CUSTOMER_TX_COUNT_7D` lidera essa medida (0,224147), mas sua
permutação aumenta a AP em média (queda de −0,005847). Isso não é uma contradição:
alterar scores não implica melhorar a ordenação das fraudes. Correlação, interações
e combinações artificiais após permutação também podem influenciar a comparação.
A amostra uniforme pode enfatizar padrões da classe genuína, majoritária; a contagem
real de fraudes nessa amostra não foi fornecida, portanto essa explicação é hipótese.

`TERMINAL_KNOWN_FRAUD_COUNT_7D` também tem queda negativa (−0,024810). Esse resultado
motiva uma ablação controlada com retreinamento; não autoriza retirar a feature.
Os dois indicadores `CUSTOMER_AMOUNT_RATIO_VALID_*` têm importância zero nas duas
medidas, mas podem servir ao contrato de cold start. Não foram inspecionados seus
valores linha a linha ou sua utilidade em outros períodos.

### Consequência para o próximo experimento

Registrar previamente hipóteses de ablação por grupos coerentes de features,
ganho operacional relevante e orçamento. Comparar candidatos pareados no tempo,
tratar clientes repetidos e a curta janela de validação, e controlar a quantidade
de escolhas exploradas. Não criar um quality gate retroativo para aprovar os números
observados. O teste final permanece reservado; nenhuma feature foi removida e
nenhum hiperparâmetro foi ajustado nesta etapa.
