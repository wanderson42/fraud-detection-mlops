# Replay causal: contratos e executor local

Esta etapa prepara o processamento incremental de transações e feedback. O
executor usa SQLite local para manter histórico, decisões e checkpoint; não
depende de broker, servidor de banco ou orquestrador. A CLI demonstra somente
eventos gerados. Não lê Bronze/Silver/Gold, não acessa 16–30/09 e não ajusta a
referência. Execução nativa e avaliação da política de investigação ficam pendentes.

## Eventos e relógio

`streaming/contracts/transaction_events.py` é o contrato executável. Os campos
são estritos; campos extras e valores não finitos são rejeitados. O relógio usa
ISO 8601 sem offset, com até nove casas fracionárias: preserva o calendário da
fonte, sem atribuir um fuso que ela não informa. Internamente usamos nanossegundos
inteiros. Nesta versão, o instante deve ser não negativo e permitir o atraso do
feedback dentro do tipo de timestamp.

| Evento | Campos | Identidade |
| --- | --- | --- |
| Transação | `schema_version: 1`, `event_type: "transaction"`, `transaction_id`, `tx_datetime`, `customer_id`, `terminal_id`, `amount` | `transaction:<transaction_id>` |
| Feedback | `schema_version: 1`, `event_type: "feedback"`, `transaction_id`, `available_at`, `fraud_label` | `feedback:<transaction_id>` |

A transação não aceita rótulo ou cenário de fraude. O feedback precisa se referir
a uma transação já observada e ter disponibilidade exatamente sete dias depois
do evento, conforme o Gold v1. O rótulo é inteiro binário; não é enviado ao scorer.
Correção posterior de rótulo exige outra versão/política e não substitui o original.

| Situação | Política desta versão |
| --- | --- |
| Evento novo | Relógio não decrescente; transação e feedback no mesmo instante são permitidos |
| Empate no timestamp | Históricos excluem todos os eventos com timestamp igual ao da decisão |
| Duplicata idêntica | Retorna o resultado gravado, sem recalcular atributos ou score; mesmo após o relógio avançar |
| Identidade repetida com conteúdo diferente | Rejeita; conserva a decisão e o feedback originais |
| Evento novo anterior ao checkpoint | Rejeita; não reordena retroativamente e não avança o estado |
| Feedback sem transação ou fora do atraso fixado | Rejeita; não avança o estado |
| Feedback ainda não recebido | Continua desconhecido; não infere seu valor nem lê o alvo offline |

Frações equivalentes do relógio são normalizadas para identificar duplicatas.
Esta versão não implementa buffer de reordenação, tolerância a atraso de transporte
ou watermark distribuído. Os sete dias representam disponibilidade no relógio
dos eventos; não é necessário esperar sete dias reais para executar a simulação.

## Atributos, pontuação e persistência

`streaming/features/causal_feature_state.py` produz os 19 atributos com os mesmos
nomes, ordem, domínios e coerências do [Gold v1](../data/GOLD_CONTRACT.md).
Para uma decisão em `t`, a janela de transações é `[t − d, t)`. Os históricos de
rótulos usam transações em `[t − 7 dias − d, t − 7 dias)` e feedback já recebido
com `available_at < t`, para `d` igual a um ou sete dias. Assim, um feedback
exatamente disponível em `t` só participa de decisões posteriores a `t`.

O resultado é um `ScoreRequest` do contrato de serving existente. A biblioteca
aceita opcionalmente o `ScoringRuntime` de uma release já carregada e identificada;
usa sua função de pontuação em processo, sem fit, HTTP ou nova run MLflow. A
CLI desta entrega calcula atributos e deixa `scoring: null`. A paridade de scores
foi conferida nos testes com uma release explicitamente sintética, não com a
referência do autor nem como avaliação de qualidade do ranking.

`streaming/replay/temporal_replay_executor.py` mantém:

- Transações e feedback separados, com índices para as janelas de histórico.
- Decisões contendo o request e, quando habilitada, a resposta identificada do scorer.
- Eventos aceitos com hash e resultado original, usados para repetição idempotente.
- Identidade de execução e o último relógio aceito, formando o checkpoint.

Cada chamada usa uma transação SQLite explícita: histórico, decisão, evento e
checkpoint são confirmados juntos. Falha no cálculo/pontuação desfaz a chamada.
Após commit, nova entrega do evento retorna a decisão salva. Uma falha pode repetir
uma pontuação ainda não confirmada; o scorer é local e não publica efeitos externos.
Não há garantia de publicação exatamente uma vez em uma API ou broker externo.

A identidade combina run, SHA-256 declarado da entrada, código relevante, Python,
SQLite, contrato de features e identidade do scorer opcional. O produtor deve
conferir sua entrada antes de entregá-la; a CLI gera o stream inteiro e calcula
seu hash. Mudança de identidade bloqueia retomada, sem migrar silenciosamente o
checkpoint. Preserve o estado anterior e use outra identidade/saída para uma
execução diferente. A verificação estrutural SQLite não autentica a origem dos
eventos nem protege contra edição manual válida do conteúdo do banco.

## Executar a demonstração e retomar

Na raiz do checkout, após aplicar, validar e commitar a entrega:

```bash
REPLAY_STATE="data/replay/synthetic-v1/checkpoint.sqlite"

poetry run python -m \
  fraud_detection_mlops.streaming.replay.temporal_replay_executor \
  demo --state "$REPLAY_STATE" --stop-after 6

poetry run python -m \
  fraud_detection_mlops.streaming.replay.temporal_replay_executor \
  demo --state "$REPLAY_STATE"

poetry run python -m \
  fraud_detection_mlops.streaming.replay.temporal_replay_executor \
  demo --state "$REPLAY_STATE"

poetry run python -m \
  fraud_detection_mlops.streaming.replay.temporal_replay_executor \
  inspect "$REPLAY_STATE"
```

São dez transações e dez feedbacks, mais duas repetições intencionais. O primeiro
comando deixa seis eventos únicos, cinco decisões e um feedback. A retomada
aceita os 14 eventos restantes e reaproveita oito entregas. Ao final, os contadores
são `events: 20`, `transactions: 10`, `feedback: 10`, `decisions: 10`. A repetição
completa aceita zero eventos e reaproveita 22. `inspect` abre somente para leitura
e não cria um banco ausente. Não apague o estado para aparentar uma retomada.

Para uma demonstração nova, use outro caminho em `data/replay/`. O executor não
usa a release, o tracking ou o recibo Docker existentes. Rejeições são exceções
do chamador e não ficam publicadas como eventos aceitos; sua auditoria operacional
persistente será definida junto do transporte.

## Evidência e próximo limite

Os checks usam o cálculo Gold em DuckDB como oracle independente, cobrindo
históricos vazios, valor médio zero, bordas de um/sete dias, empates e nanossegundos.
Também cobrem rótulos ausentes, duplicatas, conflitos, eventos tardios, mudança de
identidade, dois writers, falha de scorer e saída abrupta de processo com escrita
em andamento. A recuperação verifica ausência de evento/decisão/checkpoint parcial.
[Preparação](../../references/evidence/causal_replay_preparation_2026-10-13.json).

Essa evidência não estabelece throughput, latência, pico de memória, recuperação
de falha física de disco ou garantias distribuídas. O banco retém todo o histórico;
compactação, retenção e migração de estado não foram implementadas. Use um arquivo
local por execução; armazenamento compartilhado e múltiplos hosts ficam fora do escopo.

## Execução no ambiente do autor — 2026-10-13

O autor aprovou 448 testes com 93 avisos, pytest em 88,35 s e tox `py314`
aprovado em 94,24 s. Commitou a implementação em `23c863e` e executou o demo
interrompido, sua retomada, a repetição completa e `inspect`. A branch informada
é `feat/causal-replay-executor`. A implementação e o relato foram integrados pelo
[PR #4](https://github.com/wanderson42/fraud-detection-mlops/pull/4), em `721a4bb`.
[Recibo do relato](../../references/evidence/causal_replay_author_validation_2026-10-13.json).

| Fase | Eventos únicos / requests / feedbacks | Aceitos / reaproveitados na chamada |
| --- | --- | --- |
| Interrupção após seis entregas | 6 / 5 / 1 | 6 / 0 |
| Retomada completa | 20 / 10 / 10 | 14 / 8 |
| Repetição das 22 entregas | 20 / 10 / 10 | 0 / 22 |
| Inspeção somente para leitura | 20 / 10 / 10 | Sem processamento de eventos |

Os hashes de código e entrada coincidem com a preparação. SQLite foi `3.46.1`
no ambiente do autor, enquanto a preparação usou `3.50.4`; os relatórios conservam
cada versão. Isso não exige mudar o código nem transplantar o checkpoint entre
ambientes. O banco do autor não foi recebido para inspeção independente; esta
evidência registra suas saídas, sem substituir o recibo de preparação.

`scoring: null` indica que o demo gravou requests de atributos, sem pontuar um
modelo. `feature_parity_evaluated: false` e `score_parity_evaluated: false`
descrevem essa invocação: a comparação foi coberta pelos testes sintéticos da
suíte, não executada novamente pelo demo. A conclusão local é retomada e
repetição do executor com fixtures, não uma avaliação da referência do autor.
Não foram gerados alertas, avaliados períodos novos do Handbook ou consumida
a reserva. O código do executor permanece igual ao da validação informada.

## Política operacional de triagem

O objetivo é **pontuar transações conforme chegam e apoiar investigação próxima
do tempo real**. A autorização do pagamento, bloqueio automático e atendimento
ao cliente ficam fora deste laboratório. O score continua sendo ranking sem
calibração de probabilidade. HTTP é a interface de pontuação; processamento por
eventos acrescenta estado causal, decisões e feedback.

`streaming/contracts/customer_day_alerts.py` identifica configuração, release,
limiar e orçamento. `streaming/decisions/customer_day_alert_policy.py` aplica:

1. Na primeira transação cujo score seja **maior ou igual ao limiar fixado**,
   alertar o cliente, se ainda houver capacidade naquele dia.
2. Usar até **100 clientes distintos por dia**, seguindo relógio do evento e
   `TRANSACTION_ID` crescente em empates. Novas transações de um cliente já
   alertado continuam pontuadas, sem consumir outra posição.
3. Quando o orçamento acabar, registrar a supressão; continuar calculando scores
   e recebendo feedback. Não substituir um alerta anterior por um caso posterior.
4. Reiniciar a capacidade à meia-noite do calendário da fonte. Não completar
   vagas ao fim do dia nem transferir capacidade não utilizada para amanhã.

Essa escolha permite encaminhamento imediato, com custo explícito: casos que
atingem o limiar cedo podem ocupar vagas de casos mais fortes que chegam depois.
A comparação com top-100 ao fim do dia medirá esse compromisso. Ela muda tanto
o momento quanto a política de seleção; não isola o efeito causal do transporte.

O redutor em memória permanece disponível. Sua função de decisão é compartilhada
com o [novo executor durável](#scores-alertas-e-checkpoint-duráveis), que grava
as decisões localmente na mesma transação do score e checkpoint. O demo original
continua sendo de features; seus checkpoints não passam a conter alertas. Publicação
externa ainda exige um contrato de entrega próprio.

## Protocolo antes do acesso nativo

O [protocolo operacional](../../references/causal_replay_protocol_v1.json)
registra estas decisões e mantém `native_execution_authorized: false`.
O autor executou e verificou o seletor: **limiar 0,8826493808370081**.
A [configuração identificada](../../references/customer_day_alert_policy_v1.json)
registra o valor e a release; integração durável e replay nativo seguem pendentes.
Os testes usam 0,7 como valor artificial, sem recomendar esse número para a referência.

Para definir o limiar, pontuar **a mesma release congelada de abril** nas três
janelas de validação de desenvolvimento: 15–21/07, 29/07–04/08 e 12–18/08.
Não usar os scores dos modelos reajustados por fold do Optuna. Para cada um dos
21 dias, agregar máximo score por cliente e extrair o 100º maior score; selecionar
a mediana dos 21 cortes. Faltar um dia ou haver menos de 100 clientes interrompe
a seleção. A regra usa scores e capacidade, sem consultar rótulos ou setembro.
É uma heurística de ocupação; não garante cem alertas futuros nem precisão mínima.
O seletor está em `streaming/preparation/development_alert_threshold.py`. Ele
reutiliza somente as 21 partições da Gold de desenvolvimento já existente, sem
reconstruir features nem carregar os modelos de cada fold. Projeta identidades,
relógio e 19 features; não decodifica `TX_FRAUD` ou `LABEL_AVAILABLE_AT`. Os valores
de feedback passado incorporados nas features permanecem parte do contrato causal.

Registrar hashes das entradas e release, limiar numérico, configuração e código
antes de integrar o adaptador que abrirá 16–30/09. O contexto 02–15/09 alimentará
somente históricos, sem alertas nem nova avaliação de qualidade. Até 14 dias
prévios são necessários para as features de terminal. O escoamento de feedback
vai até antes de 08/10, sem ler transações de outubro: os rótulos dos eventos já
observados serão agendados pelo adaptador e liberados sete dias depois.

## Selecionar e verificar o limiar no desenvolvimento

Depois de validar e **commitar** o código, na raiz do projeto:

```bash
DEVELOPMENT_PATH="$PWD/data/processed/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/development_gold_v2/9f86edf1e8f2408098c5377456e8968e3a7ca5925a20222b8ab8547ef94cbaf9"
THRESHOLD_PATH="$PWD/data/replay/policies/development-threshold-v1"

poetry run python -m \
  fraud_detection_mlops.streaming.preparation.development_alert_threshold \
  prepare "$DEVELOPMENT_PATH" \
  --release "$PWD/data/serving/reference-v1" \
  --output "$THRESHOLD_PATH" \
  --project-root "$PWD"
```

Guardar o `manifest_sha256` retornado. Verificar usando **esse digest externo**:

```bash
poetry run python -m \
  fraud_detection_mlops.streaming.preparation.development_alert_threshold \
  verify "$THRESHOLD_PATH" \
  --manifest-sha256 "DIGEST_RETORNADO_PELO_PREPARE" \
  --project-root "$PWD"
```

O pacote de entrega também fornece `prepare-threshold.sh`, que reserva um recibo
novo, captura o digest do resultado e executa a verificação sem substituir saídas.
Preparação recusa um destino existente; repetir a verificação não pontua o modelo.
A verificação é offline sobre os artefatos salvos: não relê a Gold nem reconstrói
ou autentica o dataset original. Sua identidade/hashes foram registrados na preparação.

`policy.json` vincula limiar, release e orçamento; `scores.parquet` guarda somente
ID da transação, ID do cliente, relógio e score; `report.json` guarda os 21 cortes;
`manifest.json` vincula entradas, protocolo, código commitado, ambiente e hashes.
Mudança de entrada, release ou código durante seleção impede publicação. Um lock
local impede escritores concorrentes; a saída é publicada inteira após a verificação.
Não há ajuste de modelo, AP, precisão com rótulos, nova run MLflow ou abertura da
reserva. O JSON do protocolo continua com limiar nulo: o valor fica na configuração
identificada, e a execução nativa de replay permanece desabilitada.

## Resultado do limiar informado pelo autor — 2026-10-13

O autor aprovou **502 testes/93 avisos** (pytest em 89,06 s; tox `py314` em
94,14 s), commitou e publicou `d40d839`. O seletor pontuou **201.155 transações
nos 21 dias autorizados** e retornou sucesso na preparação e verificação. A mediana
dos cortes foi **0,8826493808370081**, ao orçamento de cem clientes/dia.
[Relato e escopo](../../references/evidence/development_alert_threshold_author_validation_2026-10-13.json).

| Vínculo | Identidade |
| --- | --- |
| Política canônica | `e9872e37ea944eb6e3322867a6c311e270e8d93bb829baaa18182da281eef578` |
| Manifesto do resultado nativo | `6fc96c1550c0c7840701215541d168928bac660d34c8fa2d06c655d95d0a1afa` |
| Configuração registrada | [customer_day_alert_policy_v1.json](../../references/customer_day_alert_policy_v1.json) |

A mediana e o hash canônico da configuração foram conferidos independentemente
com os números fornecidos. Os scores/manifesto nativos não foram recebidos para
inspeção independente; o recibo registra o relato do autor e seu digest externo.
O protocolo original permanece preservado, inclusive seu placeholder de limiar;
o valor selecionado está no arquivo de configuração próprio. Essa separação
mantém o verificador compatível com o protocolo fixado antes da seleção.

O corte é uma **heurística de capacidade**, não 88,26% de probabilidade de fraude.
Não garante cem alertas futuros, precisão mínima ou captura no momento útil.
A execução não mediu qualidade com os alvos atuais, latência de streaming nem
persistência de alertas. Não houve refit ou abertura de setembro. Não repetir
`prepare` nem apagar a saída; para rever o resultado, usar `verify` com o digest
registrado. A próxima entrega deve vincular a política e o manifesto antes de
integrar score/alerta/checkpoint e validar a recuperação com fixtures.

## Avaliar a implementação operacional

Separar três verificações:

- **Paridade técnica:** features/scores contra cálculo offline independente com
  a mesma informação disponível; decisões contra aplicação da mesma política,
  mesma ordem, limiar e orçamento. Duplicatas e recuperação devem preservar IDs.
- **Utilidade da triagem:** ocupação, precisão sobre alertas realmente emitidos,
  recall de cliente/dia, supressões, capturas/perdas e horário do alerta em relação
  à primeira transação fraudulenta. Sem alertas, precisão é indefinida; sem clientes
  fraudulentos, recall é indefinido. Manter todos os 15 dias no relatório.
- **Entrega e custo:** tempo monotônico de parede entre emissão programada e
  decisão persistida, p50/p95/p99, backlog/lag, throughput, pico de memória, CPU,
  crescimento do banco e tempo de recuperação. Medir ritmo 1x e aceleração
  separadamente; o relógio de 2018 não mede latência de processamento.

Adotamos **p95 até 1 segundo** como alvo inicial de laboratório para triagem,
incluindo espera na fila, features, score e persistência. Não é SLA de produção;
o alvo ainda não foi testado. O p95 HTTP de 4,74 ms não cobre esse percurso.
O ensaio em ritmo 1x precisa registrar o início/âncora e usar emissão programada,
para contabilizar atraso que se acumule em vez de medir só o tempo de execução.
O contexto é carregado antes dessa medição; falhas/recuperação têm ensaio próprio.

Comparar a política causal com uma lista batch produzida após o fechamento diário
usando **os mesmos scores**, máximo por cliente e top-100. Reportar diferenças de
captura, clientes escolhidos e disponibilidade do alerta. Nenhum desses resultados
provará perdas financeiras evitadas, superioridade formal ou promoção em produção.

## Scores, alertas e checkpoint duráveis

`streaming/replay/durable_triage_replay.py` compõe o runtime de serving, o cálculo
causal e a mesma função de decisão usada pelo redutor em memória. Não lê datasets,
reajusta modelos ou publica mensagens. Cada evento aceito confirma em uma única
transação SQLite:

- Transação/feedback e histórico necessário para as features.
- Request, score identificado e decisão de triagem, quando houver pontuação.
- Alerta único por cliente/dia, quando elegível e com capacidade.
- Evento com resultado original e cursor completo de retomada.

O cursor ordena `(relógio, feedback antes de transação, ID crescente)`. O produtor
precisa conservar essa ordem; o lock de escrita não ordena produtores distintos.
Duplicatas idênticas retornam o resultado confirmado, mesmo após avançar o relógio;
conflitos/eventos novos fora de ordem são rejeitados. Falha antes do commit desfaz
histórico, score, alerta e progresso. Se o processo termina depois do commit e antes
da resposta, a nova entrega retorna o mesmo alerta sem pontuar novamente.

O banco usa **schema 2** e uma identidade `durable_triage_replay_v1`, vinculando
entrada, política, release, código e ambiente. Precisa de um **caminho novo**;
recusa schema 1, sem migrar/escrever seus checkpoints. O executor anterior conserva
seus bytes e fingerprint, e pode continuar retomando seus estados. O novo estado
também inclui janela de pontuação e declaração de escopo do chamador; isso não
confere autorização para consumir datasets. Mudanças de identidade bloqueiam retomada.

A API aceita uma janela de pontuação semiaberta. Transações anteriores ao início
alimentam somente o histórico, sem chamar o modelo, emitir alertas ou gastar orçamento.
Transações no fim ou depois dele são rejeitadas. Feedback das transações já observadas
pode continuar chegando para completar os rótulos. O adaptador futuro fixará os 14
dias de contexto e restringirá as datas físicas antes de entregar os eventos.

Os testes usam release/eventos gerados: paridade com o executor anterior e redutor,
orçamento após reinício, duplicatas/conflitos, ordem/feedback, corrupção de identidade,
falhas SQL/scorer e processo encerrado antes/depois do commit. Cobrem dois escritores
entregando a mesma identidade e aquecimento sem pontuação. O ensaio não simula falha
física de disco ou estabelece throughput, pico de memória, SLA ou qualidade nativa.

### Demonstração com release gerada

O pacote fornece `demo.sh`: cria uma release explicitamente sintética em um destino
novo, grava seu digest e executa interrupção em seis entregas, retomada, repetição e
inspeção. Modelo, banco e recibo ficam em `data/replay/durable-demo-v1/`; nenhum
artefato de `data/serving/reference-v1` é usado. O limiar **0,7 é apenas fixture**;
a configuração nativa 0,8826493808370081 permanece registrada para o futuro adaptador.

A CLI recebe somente release marcada `synthetic_smoke`; rejeita a referência antes
de carregar o modelo. Também permite inspecionar o banco sem criar um arquivo ausente:

```bash
poetry run python -m \
  fraud_detection_mlops.streaming.replay.durable_triage_replay \
  inspect data/replay/durable-demo-v1/checkpoint.sqlite
```

O demo mantém os mesmos 22 recebimentos/20 eventos únicos da etapa anterior, agora
com dez scores e dez decisões de triagem persistidos. Repetir não deve alterar
orçamento, IDs ou contadores. A recuperação local não garante publicação exatamente
uma vez em um broker ou API: não há publicador externo nesta entrega.

## Adaptador nativo restrito e avaliação acelerada

O [PR #6](https://github.com/wanderson42/fraud-detection-mlops/pull/6) integrou o
núcleo durável. O novo adaptador usa esse núcleo e acrescenta restrição de acesso,
proveniência da entrada e avaliação independente. Foi validado com dados e modelo
gerados; nenhuma execução nativa foi realizada nesta preparação.

O protocolo `native_causal_replay_execution_v1.json` vincula por SHA-256 o
protocolo causal original, a configuração escolhida, o manifesto do limiar e a
release de abril. O original continua imutável para preservar o verificador do
limiar. Não há novo fit, seleção, calibração ou modelo alternativo.

| Comando | Acesso e efeito |
| --- | --- |
| `plan` | Lê contratos, inventory e manifesto Silver; não abre nem calcula hash dos Parquets ou carrega modelo |
| `prepare` | Após verificar artefatos e código commitado, lê apenas as 29 partições 02–30/09, gera eventos separados e oracle Gold DuckDB; publica entrada nova por renomeação de diretório |
| `run` | Usa a entrada materializada, sem reabrir Silver; aquece 02–15/09 sem pontuar, processa 16–30/09 e drena feedback antes de 08/10; confirma score/alerta/checkpoint localmente |
| `verify` | Lê somente entrada materializada, checkpoint e relatório; exige digests externos e reproduz paridade e métricas sem refit, scorer ou novo acesso a Silver |

A preparação já acessa a reserva: só executá-la após commit/revisão e CI do SHA
final. Alterações no código, no protocolo, na política ou na entrada bloqueiam a
retomada; preserve os artefatos. Configurações pessoais do editor podem permanecer
staged. Não se deve excluir o checkpoint para aparentar recuperação.

A entrada inclui um SQLite de eventos, Parquets diários do oracle e scores offline.
Os rótulos existem no artefato offline para avaliação, mas somente `FeedbackEvent`
os entrega ao executor, exatamente sete dias após a transação. A transação e o
request do scorer não os aceitam. Empates seguem feedback primeiro e ID crescente;
histórico no instante exato da decisão fica excluído. Nenhuma transação de outubro
é aberta para completar rótulos de setembro.

`--stop-after` conta novos eventos confirmados. A retomada lê somente o sufixo ainda
não confirmado; o banco persistido mantém o prefixo. Entradas e estados antigos não
são migrados. O resultado final e seu recibo são publicados juntos em um diretório
`assessment/` novo; falha anterior à publicação conserva o checkpoint para retomada.

### Procedimento local

Primeiro aplique/valide/commite o pacote incremental e publique a branch. Exija CI
aprovada para o SHA final **antes de `prepare`**. O pacote separa os comandos para
que a instalação/validação não abra a reserva automaticamente:

```bash
# Metadados apenas, depois do commit local:
poetry run python -m fraud_detection_mlops.streaming.replay.native_causal_replay plan

# Depois da CI aprovada, usando os artefatos nativos já existentes:
bash ~/Downloads/fraud-native-causal-replay/prepare-native.sh

# Interromper de forma controlada e depois retomar o mesmo banco:
bash ~/Downloads/fraud-native-causal-replay/run-native.sh --stop-after 5000
bash ~/Downloads/fraud-native-causal-replay/run-native.sh

# Verificação somente para leitura, usando o digest do recibo final:
bash ~/Downloads/fraud-native-causal-replay/verify-native.sh
```

O preparo usa `data/serving/reference-v1` e
`data/replay/policies/development-threshold-v1`. Publica entrada e digest em
`data/replay/native-input-v1/` e `data/replay/native-input-receipt.json`, sem
sobrescrever destinos existentes. A execução mantém
`data/replay/native-september-v1/checkpoint.sqlite`. Os scripts preservam o recibo
externo final em `data/replay/native-result-receipt.json` e recusam seu conflito.
Podem ser necessárias vários minutos de processamento; o adaptador informa cada
5.000 novos eventos confirmados. Interromper e retomar não muda a política nem
reabre partições Silver. Não considerar resultado parcial como avaliação completa.

### Interpretação do relatório

A verificação exige as 15 datas e todos os feedbacks maduros. Confere features
contra Gold DuckDB independente (inteiros exatos, floats rtol 1e-12/atol 1e-9),
scores com diferença absoluta até 1e-12 e decisões contra simulação independente da
mesma ordem/limiar/orçamento. Falha de paridade impede publicar avaliação concluída.

A precisão divide capturas por **alertas realmente emitidos**, e é nula quando
não há alertas. O recall é nulo quando não há clientes com fraude naquele dia.
Captura de cliente-dia significa qualquer transação fraudulenta do cliente no dia,
inclusive após o alerta; não equivale à fraude da transação que disparou o alerta.
O relatório inclui capacidade ociosa/esgotamento, clientes elegíveis suprimidos,
capturas/perdas e horário do alerta relativo à primeira fraude daquele cliente-dia.
Diferenças para o top-100 retrospectivo combinam momento de entrega e política de
seleção; são descritivas, sem alegação causal de superioridade do streaming.

Esta execução é acelerada e sem pacing: duração local do handler inclui commit,
mas não emissão programada, transporte ou fila. CPU e RSS são do processo; RSS é
seu pico acumulado de vida, não memória exclusiva do scorer. São guardados os custos
de cada invocação, inclusive retomadas, e o tamanho do checkpoint. O ensaio 1x,
backlog/lag e tempo de recuperação continuam pendentes; p95 local não valida o
alvo de laboratório de 1 s desde emissão nem SLA de produção. Não há publicação
externa, teste temporal formal, economia monetária ou promoção de produção.

Referências: [transações SQLite](https://www.sqlite.org/transactional.html) e
[API `sqlite3` do Python 3.14](https://docs.python.org/3.14/library/sqlite3.html).
