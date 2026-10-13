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

Antes do replay nativo, precisamos fixar o protocolo: janela e contexto de
aquecimento, release, emissão de scores, política de investigação no instante da
decisão e comparação com o offline sob a mesma disponibilidade de informação.
O primeiro score nativo precisa de até 14 dias prévios de transações/feedback
para as features de terminal. Apenas zeros por ausência de contexto não demonstram
paridade. 16–30/09 continua reservado. A fila retrospectiva de cem clientes por
dia ainda não foi convertida em política instantânea de alerta.

Referências: [transações SQLite](https://www.sqlite.org/transactional.html) e
[API `sqlite3` do Python 3.14](https://docs.python.org/3.14/library/sqlite3.html).
