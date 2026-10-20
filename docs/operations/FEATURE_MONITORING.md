# Monitoramento mínimo de dados e features

Implementação e aceite nativo mínimo de 2026-10-19. O autor confirmou 116 testes
aprovados e um opt-in ignorado, demonstração controlada, snapshot nativo,
exporter, oito regras válidas, coleta Prometheus e valores do dashboard Grafana.
Reutiliza a stack de observabilidade existente. A cor neutra da contagem de
observações é um ajuste posterior de apresentação, a conferir após atualização.

## Por que monitorar vai além do drift

Monitorar drift de dados, concept drift e desempenho é uma parte importante do
monitoramento de ML. Para confiar nessa análise, também precisamos observar as
condições em que os dados chegam, as features são calculadas e as decisões são
produzidas. Essa necessidade existe em batch; no streaming, fica mais evidente
porque o fluxo avança continuamente e os eventos têm seu próprio relógio.

| Camada | Pergunta que precisamos responder | Exemplos no estudo de caso |
| --- | --- | --- |
| Operação | O sistema está disponível e o processamento avança? | Saúde do serviço/coletor, progresso do replay, atraso de processamento |
| Dados e features | Os valores são válidos e representam o instante correto? | Schema, valores ausentes, coerência de razões/taxas e relógio das features |
| Distribuições | O perfil das transações mudou em relação à referência? | Comparação das quatro features selecionadas entre janelas operacionais |
| Modelo e decisão | Com rótulos disponíveis, o ranking e a seleção de alertas continuam úteis? | Métricas com feedback maduro e precisão dos alertas selecionados |

Essas camadas organizam perguntas, não exigem quatro plataformas. Reutilizamos
o coletor, Prometheus e Grafana existentes. A tabela também não declara que
todas as possibilidades de monitoramento já foram implementadas: o escopo
efetivo desta entrega está descrito nas próximas seções.

Imagine uma queda na precisão dos alertas. O padrão de fraude pode ter mudado,
mas uma feature pode ter deixado de ser atualizada, ou parte dos rótulos ainda
pode não estar disponível. O monitoramento precisa ajudar a distinguir essas
causas antes de sugerir retreinamento. Uma feature numericamente válida ainda
pode representar informação inadequada para o instante da transação.

### Dados diferentes, conceito diferente e desempenho pior

- **Data drift:** mudança na distribuição das entradas. Pode refletir
  sazonalidade ou outra composição legítima das transações, sem perda de
  desempenho. É o tipo de sinal descritivo observado pela comparação de
  distribuições desta entrega.
- **Concept drift:** mudança na relação entre as características observadas
  e a ocorrência de fraude. Comparar histogramas das entradas não comprova essa
  mudança. Também pode existir mudança nessa relação sem uma alteração evidente
  na distribuição das entradas.
- **Degradação de desempenho:** piora nas métricas relevantes das previsões ou
  decisões. Uma queda isolada não identifica sua causa: pode envolver mudança
  de dados, falha operacional, composição da amostra ou relação com o alvo.

No protocolo deste estudo, os rótulos respeitam atraso de sete dias. A avaliação
de desempenho deve usar as coortes cujo feedback já amadureceu; ausência de
rótulo recente não equivale a transação genuína. Comparamos períodos elegíveis
e preservamos a separação temporal, evitando usar informação futura.

Também distinguimos o **relógio do evento** do **relógio do processamento**.
O primeiro situa a transação no mundo simulado; o segundo informa quando o
sistema a processa. A diferença entre o relógio da transação e o das features
é outra verificação, com significado próprio. O indicador desta entrega não
mede automaticamente o atraso de ingestão de uma fonte externa.

### O que fazemos com um sinal

Um alerta de distribuição pede investigação: verificar qualidade e relógios,
contexto e sazonalidade, suporte da amostra e, quando houver rótulos maduros,
desempenho. Um novo treino só faz sentido com dados elegíveis, orçamento e
critérios de avaliação definidos. Um candidato aprovado ainda precisa passar
pelos gates de entrega e promoção. **Drift não dispara treino nem promoção
automáticos nesta implementação.**

Para o portfólio, o objetivo é demonstrar um ciclo compreensível: detectar um
problema, interpretar seu provável significado e orientar uma ação. O escopo
permanece limitado ao que conseguimos validar e explicar.

Referência conceitual: [MLOps: Continuous delivery and automation pipelines in
machine learning](https://docs.cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning).

## O que esta primeira cobertura observa

O checkpoint do replay guarda a requisição usada em cada decisão. O novo módulo
lê somente `transactions.tx_ns` e `decisions.request`, dentro da mesma transação
somente leitura do coletor. Não recalcula features, carrega modelos, pontua,
treina ou abre Bronze/Silver/Gold. O coletor operacional existente continua
usando o feedback já persistido para suas métricas de qualidade madura; essa
lógica não foi alterada e não é usada como entrada da comparação de features.

| Verificação | Escopo |
| --- | --- |
| Schema e ausência | Versão da requisição, presença exata das 19 features, tipos/rangos numéricos, ausência/null e coerências de razão/taxa do contrato Gold |
| Relógio das features | `feature_as_of` e `tx_datetime` contra o relógio persistido da transação, incluindo nanosegundos; hora/dia da semana coerentes |
| Distribuições | `TX_AMOUNT`, `CUSTOMER_AVG_AMOUNT_7D`, `CUSTOMER_TX_COUNT_7D` e `TERMINAL_KNOWN_FRAUD_RATE_7D`; bins fixos e suporte explícito |
| Preservação | Consultas somente leitura; relatórios novos sob `data/reports/feature-monitoring/`; sem sobrescrita de recibos |

Isso monitora features **já utilizadas e persistidas**. Não cobre a integridade
de toda a Bronze, atrasos de ingestão de uma fonte externa, logs rejeitados antes
de virar decisão ou o atraso entre todos os jobs ELT. Essas fronteiras precisam
de instrumentação própria quando forem fluxos suportados no provisionamento.

## Referência, suporte e interpretação

A referência operacional são as **primeiras 1.000 decisões** do mesmo checkpoint;
a janela recente contém as **últimas 1.000**. A ordenação é relógio da transação e
ID para desempate. O digest da janela e seus limites temporais ficam no relatório;
IDs e valores individuais não são exportados. Após as primeiras 1.000 decisões,
a referência permanece estável enquanto o ledger preserva seu prefixo.

São necessárias pelo menos 2.000 decisões, com as duas janelas completas e sem
sobreposição, e nenhuma linha inválida nas duas janelas para emitir divergência.
Sem suporte, o estado é `insufficient_support`; com dado inválido, `invalid_data`.
Em ambos os casos a série de divergência não é emitida. **Ausência não é zero.**
Não substituir o valor ausente por zero nos painéis ou regras.

Jensen–Shannon em base 2 varia de 0 a 1. O limiar `> 0.1`, persistindo por dois
minutos, gera um alerta informativo de investigação. É uma escolha inicial do
laboratório, não um teste de significância ou uma calibração de performance.
Não há correção por múltiplas comparações nem confirmação de degradação.

Esta referência **não é a distribuição de treino de abril** e não implica nova
avaliação das janelas históricas. Sazonalidade, contexto inicial e composição da
amostra podem explicar diferenças. O protocolo está em
[feature_monitoring_v1.json](../../references/feature_monitoring_v1.json).

O atraso calculado é `tx_datetime − feature_as_of`, quando não negativo. Um
valor no futuro é contado separadamente. Não se calcula `agora − 2018`: dados
históricos válidos não são automaticamente classificados como atrasados hoje.
Não se alega frescor de ingestão em tempo real com esse indicador. Para Gold
precomputada, os dois relógios devem coincidir; um atraso é violação do contrato.

## Primeiro teste no checkout do autor

Na raiz, sem iniciar novo replay e sem alterar o checkpoint:

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/monitoring \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py

poetry run python -m scripts.monitoring.feature_monitoring demo

poetry run python -m scripts.monitoring.feature_monitoring snapshot \
  --state data/replay/paced-native-v1
```

Cada comando de relatório imprime seu caminho novo. A demonstração gera quatro
cenários em memória: distribuição igual, mudança de valor, feature ausente e
relógio defasado. Verifica as respostas esperadas; não lê dados nativos nem faz
fit. O snapshot lê o estado existente e grava somente agregados. `ready` indica
comparação disponível, não ausência de alertas ou aprovação do modelo.

Se retornar `invalid_data`, preservar o relatório e investigar campos/relógios;
não corrigir ou reescrever o checkpoint para fazer o painel ficar verde.
`insufficient_support` também é resultado válido para um smoke pequeno.
O parâmetro `--output` é opcional, restrito a `data/reports/` e recusa arquivo já
existente. Um snapshot bem formado pode relatar problema de dados sem erro do
processo; é o campo `status` que diferencia a condição de qualidade.

## Atualizar a stack existente após conferir os relatórios

Reutilizar `infra/observability/.env`; não executar `init` novamente, apagar
volumes ou substituir o diretório de estado selecionado. O exporter continua
com mount somente leitura e sem dependências científicas instaladas. O build
agora inclui também o arquivo exato do contrato Gold em sua allowlist.

```bash
docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml up -d --no-deps --build exporter

docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml exec -T prometheus \
  promtool check rules /etc/prometheus/alerts.yml

docker compose --env-file infra/observability/.env \
  -f infra/observability/compose.yaml restart prometheus
```

Os comandos supõem Prometheus/Grafana da bancada já disponíveis. Se estiverem
parados, iniciar os serviços existentes, preservando volumes e credenciais.
O restart de Prometheus carrega as regras e mantém o volume existente. Grafana
já provisiona os JSONs do diretório; o terceiro dashboard aparecerá na pasta
`Fraud MLOps` após seu ciclo de atualização, sem criar outro serviço.

Conferir:

```bash
curl -fsS http://127.0.0.1:19100/readyz
curl -fsS http://127.0.0.1:19100/metrics | rg '^fraud_feature_'
```

No Grafana, abrir **Fraud — dados e features persistidas**. Confrontar contagens,
ausência, relógios e divergências com o snapshot do mesmo checkpoint. O nome
`fraud_feature_comparison_ready` aparece no painel de suporte; o suporte pode
mudar durante uma execução ativa. O reporte do snapshot e o scrape não são um
único instante se houver escritor em andamento.

As regras novas são `FraudFeatureRequestInvalid` (30 s, warning) e
`FraudFeatureDistributionChanged` (2 min, info). A segunda faz matching por
`job,instance`. As regras/painéis anteriores são preservados. As séries usam
somente nomes fixos de features/janelas, nunca IDs de clientes ou transações.
Não existe webhook de treino ou promoção ligado a esses alertas.

## Critério de aceite e sequência seguinte

1. Regressão e demonstração controlada aprovadas.
2. Snapshot nativo com suporte/qualidade interpretados e preservado no destino
   gerenciado, sem executar treino, pontuação ou uma nova janela de replay.
3. Exporter atualizado, regras validadas por promtool, scrape saudável e valores
   do novo dashboard conciliados com o relatório. Resultado observado na stack
   deve ser informado pelo autor; teste unitário não fecha esse aceite.

A demonstração prova os cálculos/resposta à alteração gerada; não injeta falhas
nos bancos nativos nem comprova firing do alerta por dois minutos na stack real.
Depois desse aceite mínimo, migrar a infraestrutura associada ao fluxo completo
para KinD + Helm em incrementos (dados/objetos, bancos/estado, jobs, tracking,
serving e observabilidade), com identidade/persistência/recuperação próprias.
CT/orquestração serão analisados depois; feature store e broker seguem
condicionais. Simplificação precede Terraform, que permanece o desfecho final.

## Aceite nativo mínimo — 2026-10-19

O snapshot e o exporter concordaram: duas janelas de 1.000 decisões, sem
sobreposição, sem ausências/valores inválidos, incoerências ou violações de
relógio; defasagem feature → evento igual a zero. As quatro divergências ficaram
entre 0,001457 e 0,004398, abaixo do limiar inicial de 0,1. Isso descreve essas
janelas e bins; não comprova ausência de concept drift ou estabilidade do modelo.

O autor reconstruiu o exporter, observou `/readyz` e as métricas, validou oito
regras com promtool e consultou `fraud_feature_comparison_ready = 1` pela API do
Prometheus (`job=fraud-replay`, `instance=exporter:19100`). A captura do dashboard
foi conferida e os valores correspondem ao snapshot, com arredondamento visual.
Prometheus/Grafana estavam parados e foram iniciados preservando os containers.

O vermelho herdado no painel de contagem foi substituído por cor fixa azul,
sem mudar consultas ou thresholds dos alertas. A conferência visual dessa cor
segue como tarefa de apresentação; não reabre o aceite dos cálculos/coleta.
O provisionamento do Grafana deve atualizar o JSON no ciclo normal; recarregar
o dashboard após esse ciclo e conferir as duas contagens em azul.

As evidências são as saídas e a captura fornecidas pelo autor. Não houve
execução independente da bancada pelo assistente. O firing/resolução dos dois
novos alertas de features não foi ensaiado na stack nativa.

[Registro e limites](../../references/evidence/feature_monitoring_author_validation_2026-10-19.json).
O próximo marco é KinD + Helm para o fluxo completo, em incrementos; este aceite
não encerra o inventário global nem migra os dados ou seus consumidores.
