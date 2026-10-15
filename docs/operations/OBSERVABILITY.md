# Replay programado e observabilidade local

Preparação de **2026-10-15**. [Roadmap](../project/ROADMAP.md),
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
mkdir -p data/replay/paced-native-v1
/usr/bin/time -v -o data/replay/paced-native-v1/time-invocation-01.txt   poetry run python -m scripts.replay.paced_triage_replay native   --output "$PWD/data/replay/paced-native-v1" --speed 86400 --capacity 128   --max-paced-events 10000
```

Esse primeiro lote exercita pausa/retomada e registra custo. O contexto anterior
a 16/09 é reconstruído a partir dos eventos materializados, sem pacing nem
latência. Ele pode dominar o tempo total. Antes de concluir, confira as saídas e
recursos. Retome com a mesma velocidade, capacidade, release, protocolo e diretório;
use outro nome para cada arquivo de `/usr/bin/time`. Para completar, omita o limite:

```bash
/usr/bin/time -v -o data/replay/paced-native-v1/time-invocation-02.txt   poetry run python -m scripts.replay.paced_triage_replay native   --output "$PWD/data/replay/paced-native-v1" --speed 86400 --capacity 128
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
Grafana e a renderização no navegador não foram validadas. Essa verificação fica
para a stack Docker no host do autor.

Docker/RustFS no computador do autor, custo/latência da entrada nativa, restauração
S3, monitoramento de features completas, transporte externo, CD/rollback e CT
continuam abertos. Conclusão da preparação não equivale a esses resultados.
