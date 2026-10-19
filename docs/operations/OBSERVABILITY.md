# Replay programado e observabilidade local

Atualizado em **2026-10-15**, com execução nativa concluída pelo autor. [Roadmap](../project/ROADMAP.md),
[protocolo](../../references/paced_monitoring_protocol_v1.json) e
[configuração](../../infra/observability/README.md). Esta etapa acrescenta fila
local limitada, journal de tempos, exporter agregado, Prometheus e Grafana.
O novo ensaio possui identidade e diretório próprios. Modelo, limiar, protocolos
e resultados históricos não são reescritos.

## Fronteiras de execução

| Componente | Responsabilidade |
| --- | --- |
| `scripts/replay/paced_triage_replay.py` | CLI `demo`/`native`, produtor programado, backpressure, warmup e consumidor do núcleo durável preservado |
| `scripts/monitoring/telemetry.py` | SQLite separado, sessões e tempos; identifica commit recuperado sem medição |
| `scripts/monitoring/collector.py` | Snapshot consistente de cada banco em modo read-only; agrega somente estado e feedback já consumidos |
| `scripts/monitoring/statistics.py` | Coortes maduras, AP com empates e Jensen–Shannon dos scores |
| `scripts/monitoring/monitoring_exporter.py` | Cache de métricas/summary, health e readiness sem dependência de ML |
| `scripts/replay/assess_paced_replay.py` | Verificação final independente, paridade exata e recibo do novo ensaio |

Essas ferramentas ficam fora do pacote congelado: o verificador da correção
identifica todos os `.py` daquele pacote, `pyproject.toml`, lockfile e referências
fixadas. Alterá-los invalidaria a identidade usada pelo autor. A nova identidade
inclui os scripts operacionais e o protocolo próprio; execução nativa exige que
estejam commitados e que os bytes executados coincidam com o checkout declarado.
Os scripts não estão no wheel de inferência. As dependências permanecem as mesmas.

## Demo e painéis

Com Python/Poetry instalados e os checks aprovados, na raiz do projeto:

```bash
poetry run python -m scripts.monitoring.local_stack init --state "$PWD/data/replay/paced-demo-v1"
poetry run python -m scripts.monitoring.local_stack check
poetry run python -m scripts.monitoring.local_stack up
poetry run python -m scripts.replay.paced_triage_replay demo --output "$PWD/data/replay/paced-demo-v1" --speed 86400 --capacity 2
```

O demo usa dez transações geradas e dez feedbacks. A linha temporal de 21 dias
leva cerca de 21 s de agendamento nessa velocidade, mais processamento. É uma
fixture identificada; não é a referência nativa nem um benchmark representativo.
O exporter pode começar `not_ready` até o primeiro estado válido; isso é esperado.
Não inicialize o mesmo `.env` novamente: preservamos credenciais e seleção de estado.
Para trocar demo por nativo, pare a stack, preserve uma cópia do `.env` fora do Git
e edite apenas `STATE_DIRECTORY` no arquivo, usando um diretório novo/existente
específico do ensaio nativo; então suba a stack de novo.

- Grafana: <http://127.0.0.1:13000>, usuário `admin`, senha em `infra/observability/.env`.
- Prometheus/alertas locais: <http://127.0.0.1:19090>.
- Exporter: <http://127.0.0.1:19100/metrics>, `/summary`, `/healthz`, `/readyz`.

O `.env` recebe modo 0600 e segredos aleatórios; nunca publique seu conteúdo.
O exporter usa o UID/GID local e monta somente o diretório do ensaio em read-only.
Portas publicadas ficam no loopback. Sem configuração de envio externo de alarmes.
Dashboards e datasource são provisionados em arquivo; edite o JSON versionado,
pois alterações na UI não são persistidas como fonte de verdade.

```bash
poetry run python -m scripts.monitoring.local_stack status
poetry run python -m scripts.monitoring.local_stack down
```

`down` preserva os volumes. Não use remoção de volumes como rotina de recuperação.
A retenção de séries é limitada a 48 h/512 MB; o recibo de avaliação final conserva
os agregados do ensaio depois da retenção. Essa retenção não é backup.

## Ensaio nativo — após commit e CI

Requer a entrada corretiva já preparada, sua release/limiar e o recibo
`data/replay/native-numerical-input-receipt.json`. Não execute novamente a preparação
original, não altere manifestos e não apague qualquer checkpoint. Confira primeiro
o procedimento da [correção](NUMERICAL_CORRECTIVE_REPLAY.md).

Use o novo diretório, sem sobrepor entradas ou os dois jobs nativos anteriores:

```bash
mkdir -p data/replay/measurements/paced-native-v1
paced_cost_report="$(mktemp "$PWD/data/replay/measurements/paced-native-v1/invocation-XXXXXX")"
/usr/bin/time -v -o "$paced_cost_report" \
  timeout --signal=INT --kill-after=30s 10m \
  poetry run python -m scripts.replay.paced_triage_replay native \
  --output "$PWD/data/replay/paced-native-v1" --speed 8640 --capacity 128 \
  --max-paced-events 1000
paced_exit_code=$?
printf 'Código: %s\nRelatório: %s\n' "$paced_exit_code" "$paced_cost_report"
cat "$paced_cost_report"
```

O diretório do ensaio deve estar ausente ou vazio na primeira criação (exceto o
lock da execução); não crie o relatório de custo dentro dele antes da inicialização.
Os relatórios ficam no diretório irmão `measurements/`. A velocidade de 8.640×
corresponde à execução relatada abaixo; não troque a velocidade de um estado
já existente. `--max-paced-events` limita novos eventos de fonte por invocação,
incluindo transações e feedback, não somente decisões.

Esse primeiro lote exercita pausa/retomada e registra custo. O contexto anterior
a 16/09 é reconstruído a partir dos eventos materializados, sem pacing nem
latência. Ele pode dominar o tempo total. Antes de concluir, confira as saídas e
recursos. Retome com a mesma velocidade, capacidade, release, protocolo e diretório;
use outro nome para cada arquivo de `/usr/bin/time`. No ensaio do autor, os lotes
seguintes foram de 10 mil, 30 mil e depois até 30 mil eventos cada, com teto de
10 minutos por invocação. Saída diferente de zero interrompe a sequência; não
faça retries automáticos de falhas. Para retomar mantendo o teto:

```bash
paced_cost_report="$(mktemp "$PWD/data/replay/measurements/paced-native-v1/invocation-XXXXXX")"
/usr/bin/time -v -o "$paced_cost_report" \
  timeout --signal=INT --kill-after=30s 10m \
  poetry run python -m scripts.replay.paced_triage_replay native \
  --output "$PWD/data/replay/paced-native-v1" --speed 8640 --capacity 128 \
  --max-paced-events 30000
```

Somente após `complete: true`, com o escritor parado:

```bash
poetry run python -m scripts.replay.assess_paced_replay --state "$PWD/data/replay/paced-native-v1"
```

O verificador abre o oráculo corretivo somente depois do processamento completo;
o monitor não o consulta. Confere eventos, as 19 features exatamente, scores com
`atol=1e-12/rtol=0`, política causal, contagens e métricas maduras. Publica
`assessment/result.json` e recibo atômico; repetir verifica o resultado sem
reescrever o checkpoint. Não retreina ou publica alertas para terceiros.

Ritmo 1x exige `--speed 1` desde a criação de **outro** diretório; a janela completa
leva semanas. Não mude a velocidade de um ensaio existente nem encurte o protocolo
para conseguir um resultado. O lote acelerado é o passo inicial viável. Um recorte
1x mais curto depende de protocolo próprio antes da execução. Mesmo um alvo local
aprovado não representa SLA de transporte distribuído ou produção.

## Resultado nativo informado pelo autor — 2026-10-15

[Evidência documental](../../references/evidence/paced_native_author_validation_2026-10-15.json).
O autor concluiu o ensaio a **8.640×**, capacidade 128, em **14 sessões**. Foram
556.824 eventos: 278.412 transações e 278.412 feedbacks. O contexto inicial teve
201.667 eventos sem pacing. A janela pontuada produziu 143.945 decisões e 1.453
alertas. A avaliação final retornou `success`, paridade exata das 19 features,
paridade de scores/decisões com `atol=1e-12`, `rtol=0` e **zero commits sem medição**.

| Tempo das 143.945 transações medidas | p50 (s) | p95 (s) | Máximo (s) |
| --- | ---: | ---: | ---: |
| Agendado→commit | 186,449511 | 379,570051 | 423,992456 |
| Atraso de emissão | 184,698177 | 377,783443 | 422,739568 |
| Espera na fila | 1,610370 | 3,183494 | 4,902304 |
| Processamento do núcleo | 0,010024 | 0,023705 | 2,438961 |

Quantis agregados de sessões com âncoras próprias, não de operação contínua.
O processamento inclui features, scoring e commit da decisão, não apenas a
inferência do modelo. Não somar p95s nem atribuir contribuição percentual a uma
etapa dividindo seus quantis. O atraso crescente é compatível com oferta
acelerada superior à vazão disponível; não identifica sozinho gargalo de I/O,
modelo ou journal. O máximo de processamento de 2,44 s também fica registrado.
`continuous_1x_lab_target_evaluable=false`, resultado do alvo 1x `null` e
`sla_validated=false` são esperados neste desenho.

A qualidade reproduziu o replay corretivo da mesma janela: 768 capturas, 358
perdas e 685 alertas sem fraude por cliente/data; precisão agregada 52,86%, recall
68,21%, AP transacional 0,578783 e ROC AUC 0,876322. Rótulos estão completos;
isso não cria confirmação independente, superioridade ou fraude financeira evitada.
O top-100 retrospectivo dispõe do dia completo e permanece comparação descritiva.

| Invocação inicial informada | Eventos novos | Warmup novo | Parede | CPU usuário + sistema (s) | RSS máximo (KiB) |
| --- | ---: | ---: | --- | ---: | ---: |
| Piloto | 1.000 | 201.667 | 7min09,26s | 227,73 | 348.844 |
| Retomada | 10.000 | 0 | 2min13,78s | 372,32 | 347.404 |
| Lote maior | 30.000 | 0 | 6min51,27s | 1.152,18 | 344.724 |

No piloto, o warmup levou 408,725 s; não compare o tempo inteiro desse processo
com o trecho programado das retomadas. Estes são três relatórios parciais, não
o custo consolidado das 14 sessões. RSS e CPU não incluem uma medição de toda a
stack Docker; percentuais de CPU acima de 100% representam múltiplos núcleos.
Os onze lotes automáticos seguintes concluíram os 314.157 eventos restantes;
o último teve 14.157 eventos e somente feedbacks.

Antes do nativo, o autor demonstrou no smoke gerado: 20 eventos/10 decisões,
repetição sem mudar o hash do checkpoint, scrape Prometheus com total 20 e
valores esperados dos dois dashboards. Um processo encerrado abruptamente foi
retomado para as mesmas features, scores e triagem do smoke contínuo, sem lacunas;
sessões `interrupted` e `complete`. O código 124 do `timeout` usado nesse teste
foi observado com uutils coreutils 0.10.0; não inferir códigos de outra implementação.
Essa recuperação não substitui evidência de falhas nativas ou distribuídas.

O recibo operacional foi informado como
`c1192d5481f890f9bf878e8f8aa730011007f13c9f576cf0d5a84161b4e129d1`.
O assistente reconciliou as saídas fornecidas, incluindo o relatório de qualidade
anexado, mas não recalculou esse hash nem inspecionou os bancos nativos. A revisão
publicada de referência é `939cf34`; isso não comprova o SHA integral do checkout
durante a execução. `data/replay/paced-native-v1/assessment/result.json` e seu
recibo continuam sendo os artefatos operacionais locais a preservar.

O autor posteriormente confirmou `/summary` com 15 dias maduros e zero pendências,
scrape/coletor em 1 e total 556.824 no Prometheus. O teste de indisponibilidade
também passou, conforme a seção seguinte. A conferência visual dos painéis com
os valores nativos finais ainda não foi informada; não confundir API com renderização.

## Falha do exporter e recuperação informadas pelo autor — 2026-10-15

[Evidência](../../references/evidence/exporter_recovery_author_validation_2026-10-15.json).
O utilitário distribuído antes de sua incorporação ao repositório verificou o
baseline nativo, parou somente o exporter, observou `FraudExporterUnavailable`
em `firing`, reiniciou o serviço e confirmou coleta/resolução do alerta.

| Medida observada | Tempo |
| --- | ---: |
| Solicitação de parada até observar `firing` | 56,36 s |
| Solicitação de início até observar recuperação completa | 12,29 s |

A parada inclui o comando Compose e sua espera; a recuperação inclui início,
coleta, scrape e resolução. O polling foi a cada dois segundos. São tempos
observados no laboratório, não latências exatas de transição nem SLA de produção.
O resultado reportou `state_files_unchanged=true` para metadados, checkpoint,
telemetria e os dois arquivos da avaliação. O autor enviou o resumo de terminal;
os valores individuais dos hashes e o relatório completo permanecem locais.

A rotina agora está versionada em `scripts/validation/exporter_recovery.py`,
com pytest de controle no tox padrão e teste real em ambiente dedicado.
Para uma futura validação explícita da stack existente:

```bash
poetry run tox -e exporter-recovery
```

Alternativa pela CLI, sem executar pytest:

```bash
poetry run python -m scripts.validation.exporter_recovery
```

Ambas provocam indisponibilidade temporária do exporter; não são comandos para
executar automaticamente a cada validação de código. Geram relatório novo em
`data/replay/measurements/paced-native-v1/exporter-recovery-*/result.json`.
O runner registra o hash do seu código, verifica o baseline, tenta restaurar o
exporter em `finally` e confere o retorno e os hashes; erros conservam relatório.
Não executa replay, não remove volumes e não altera o núcleo identificado.
O sucesso real relatado acima pertence ao utilitário anterior, não ao wrapper
novo ainda não exercitado contra Docker pelo autor.

Para conferir as três séries de saúde/progresso sem que `or` una labels iguais:

```bash
curl -fsSG http://127.0.0.1:19090/api/v1/query \
  --data-urlencode 'query={__name__=~"up|fraud_monitor_collector_up|fraud_replay_events_total",job="fraud-replay"}' | \
  poetry run python -m json.tool
```

O estado final informado foi coletor=1, up=1 e eventos=556.824; qualidade madura
preservada. O alerta é avaliado no Prometheus; entrega de notificação externa não
foi testada/configurada. [Testes e isolamento](TESTING.md#indisponibilidade-e-recuperação-do-exporter).
O próximo incremento trata do [armazenamento e restauração](STORAGE.md), incluindo
o inventário de todas as camadas de dados.

## Contrato de métricas

| Grupo/painel | Interpretação e suporte |
| --- | --- |
| Saúde | `up` mede scrape; `fraud_monitor_collector_up` mede validade do snapshot. Falha de coleta remove métricas anteriores e retorna readiness 503 |
| Progresso/fila | Eventos, decisões, alertas, throughput por tempo de parede e emissões ainda sem commit; contexto aparece separado |
| Tempos | Histogramas acumulados de transações programadas: agendado→commit, atraso de emissão, espera na fila e processamento do núcleo |
| Recuperação | Commits sem duração conhecida são contados; não viram amostras zero nem entram no p95 |
| Qualidade madura | Dias inteiros com todos os feedbacks consumidos e prazo de sete dias vencido; EOF completo também fecha a última coorte |
| Precisão/recall | Capturas/alertas e capturas/clientes-data fraudulentos. Qualquer fraude naquele cliente/data conta, mesmo posterior ao disparo |
| AP | Ranking das transações maduras, com empates; sem positivos fica ausente, não zero |
| Orçamento | Alertas, limite diário, ocupação nas datas já observadas e transações suprimidas. Estas não são clientes únicos nem fraudes perdidas |
| Scores | Últimos 1.000 scores vs histograma do desenvolvimento verificado; JS base 2 em [0,1], sem suporte fica ausente |
| Recursos | `/usr/bin/time -v` por invocação: tempo, CPU e RSS máximo do processo/filhos conforme ferramenta; não somar máximos RSS como memória simultânea |

O instante de origem de 2018 serve como watermark, não é subtraído do relógio
atual para medir lag. O produtor usa `perf_counter`; tempos expostos são
referenciados à época da sessão, preservando os deltas monotônicos. Emissão
atrasada inclui espera por espaço na fila; agendado→commit inclui esse atraso.
Pacing é por invocação; pausas não simulam disponibilidade contínua.

Dois bancos têm snapshots individualmente consistentes, sem transação distribuída
entre decisão e telemetria. Um crash após o commit pode deixar uma lacuna; o
checkpoint é a autoridade e a retomada marca `recovered_unmeasured`. A telemetria
usa SQLite com `synchronous=FULL` e rollback journal, permitindo leitura em mount
realmente read-only mesmo após sair o último escritor. O cache do exporter evita
reescanear o estado em cada requisição HTTP; a coleta periódica ainda varre o
estado e precisa ser medida no volume nativo. IDs de transação/cliente, scores
individuais, features e rótulos individuais não são labels nem endpoints públicos.

## Alarmes e resposta

| Regra | Ação |
| --- | --- |
| Exporter indisponível | Verificar processo, bind e rede local |
| Coletor inválido | Conferir diretório/identidade/permissões, preservando os bancos; não apresentar qualidade zero |
| Sessão ativa sem heartbeat | Investigar escritor/recursos; espera legítima até o próximo evento mantém heartbeat |
| Lacuna de tempo | Preservar evidência; exclui aprovação de ensaio contínuo sem lacunas |
| p95 acumulado > 1 s | Somente velocidade 1, ≥100 tempos e sem lacunas; investigar decomposição. Quantil aproximado do histograma; o recibo usa quantil exato das amostras |
| JS > 0,1 | ≥1.000 scores por dois minutos; limiar exploratório para investigação, sem retreino automático |

Regras ficam no Prometheus; não há Alertmanager nem destinatário externo nesta
entrega. Os testes de regras exercitam falha de coleta/lacuna e evitam alarmes
por replay parado ou pouco suporte. Faça demonstração local controlada: pare o
exporter, observe `up=0`/alarme após 30 s e retome; preserve estado e registre
horário de detecção/recuperação. Não injete corrupção no checkpoint nativo.

## Validação e pendências

[Evidência desta preparação](../../references/evidence/local_observability_preparation_2026-10-15.json)
registra checks e escopo. Testes gerados cobrem retomada, falha após commit,
erro no produtor, mudança de identidade, contexto excluído, pacing 1x curto,
coortes/empates, HTTP agregado, paridade independente e recibo final.
Promtool verifica métricas/config/regras; Compose pode validar sem daemon.
Nesta preparação, o Grafana nativo registrou os dois dashboards e o datasource;
as 27 consultas foram aceitas pelo Prometheus. O sandbox impediu os sockets Unix
dos plugins backend do Grafana, portanto a consulta ponta a ponta via datasource
Grafana e a renderização no navegador não foram validadas nessa preparação.
A verificação ficou para a stack Docker no host do autor, que posteriormente
confirmou os valores esperados dos painéis no smoke gerado.

Após essa preparação, o autor subiu a stack Docker, confirmou o smoke nos painéis
e concluiu o replay programado nativo descrito acima. Depois validou coleta nativa
e falha/alarme/retorno do exporter. Continuam abertos: conferência visual dos
painéis no volume nativo final, custos
consolidados de todas as sessões/stack, recorte 1x sob protocolo, RustFS com
restauração S3, features completas, transporte externo, CD/rollback e CT.
KinD + Helm é o destino final escolhido, com implantação posterior aos critérios
de persistência/restauração e rollback. A preparação não equivale a esses resultados.
