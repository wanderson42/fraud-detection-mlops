# Monitoramento da mesma sessão de investigação

A fila e o painel operacional passam a compartilhar uma sessão identificada:
**checkpoint causal → agregados → exporter → Prometheus → Grafana**. O operador
acompanha progresso, carga de investigação, feedback e latência HTTP; o analista
continua recebendo os casos pelo produtor, sem solicitar novas transações.

O ensaio ampliado `arrival-2k-v1` já foi aceito: 2.001 decisões, 81 alertas,
1.561 clientes, 2.001 recibos HTTP e feedback das decisões. Seu recibo permanece
intacto. A integração teve aceite nativo e visual confirmado pelo autor em 2026-10-24.
[Evidência de monitoramento](../../references/evidence/session_monitoring_author_validation_2026-10-24.json).
[Evidência da ampliação](../../references/evidence/product_session_scale_author_validation_2026-10-23.json).

## Componentes e origem

| Componente | Responsabilidade |
| --- | --- |
| Produtor da sessão | Reconstruir contexto, enviar transações à API, persistir decisões e feedback |
| Fila de investigação | Ler o checkpoint com paginação e identidade da sessão |
| `session-monitor-watch` | Ler agregados em uma transação SQLite somente leitura e publicar a cada 5 s |
| Container `session-exporter` | Manter projeção volátil autenticada, vinculada ao recibo, pesos e release |
| Prometheus existente | Coletar o job `fraud-product-session` a cada 10 s; avaliar duas regras |
| Grafana existente | Exibir `fraud-product-session`, incluindo origem e validade da coleta |

O receiver é um container stdlib adicional **no Deployment exporter existente**,
com porta interna 19102 e Secret próprio. Não monta o banco, não recebe casos,
features, IDs de transação/cliente ou rótulos. A imagem usa allowlist de três
módulos; nenhum log, dado privado ou dependência científica entra na imagem.
Não há novo Deployment, Service, PVC, banco ou broker. O Secret e o ConfigMap da
vinculação pertencem ao mesmo owner da release Helm `fraud-observability`.

O acesso de publicação atravessa um port-forward temporário em loopback, com
token privado; o token não aparece nos valores Helm ou nas saídas. O receptor
não autentica scraping de agregados dentro do cluster, tal como o exporter já
existente; não há exposição pública. Uma futura distribuição entre hosts deve
usar conectividade e autenticação de serviço apropriadas.

Os quatro dashboards anteriores e o job `fraud-replay` preservam a fonte do
replay concluído. As imagens e o estado histórico aceitos são reutilizados.
A extensão habilitada é conservada nas próximas chamadas de
`kind_observability up`, evitando perda silenciosa da configuração.

## Instalar e conferir

Depois de aplicar o pacote, testar e fazer seu commit seletivo:

```bash
make session-monitor-build &&
make session-monitor-prepare SESSION_MONITOR_ID=arrival-2k-v1 &&
make session-monitor-up &&
.venv/bin/python -m tox -e session-monitoring &&
make portal-verify &&
make portal-performance-verify
```

`build` exige código/transition commitados, constrói somente a nova imagem e a
importa por arquivo `linux/amd64`; confere sua identidade no CRI do node owned.
`prepare` valida a sessão original e fixa seu recibo, modelo, release e limites.
`up` recusa valores Helm estranhos e recursos de outro owner, preserva os PVCs e
confere os serviços históricos. Uma instalação interrompida conserva os recibos:
resolva a causa e repita **`make session-monitor-up`**, sem apagar o workspace,
criar outra sessão ou editar recibos. O token fica sob diretório privado 0700,
em arquivo 0600.

O teste nativo usa a sessão ampliada já concluída. Publica sua projeção, compara
contagens e p50/p95/máximo no Prometheus e no proxy autenticado do Grafana,
confere dashboard, deixa a coleta expirar e publica novamente. Leva pelo menos
35 s para exercitar expiração; pode demorar mais devido aos intervalos de scrape.
Preserva hashes do input, recibo, checkpoint e ensaio aceitos, além das
identidades dos recursos externos e PVCs. Não pede novas pontuações.
O resultado fica em `observability/session-monitoring/native-*.json`.

Não execute o observador durante esse teste: ambos usam o mesmo lock para que
outra publicação não esconda a expiração. O ensaio demonstra leitura de uma
sessão concluída, frescor e recuperação; não declara chegada concorrente no KinD,
firing de alertas ou conferência visual. Esses campos permanecem separados.

## Operação e revisão visual

Após o teste, em um terminal dedicado:

```bash
make session-monitor-watch SESSION_MONITOR_ID=arrival-2k-v1
```

Em outro terminal:

```bash
make access-start ACCESS_SERVICE=grafana
make access-status
```

Abra a URL informada e procure **Fraud — sessão e investigação**
(`/d/fraud-product-session`). Confira 2.001 decisões, 81 alertas, 1.561 clientes,
2.001 feedbacks das decisões e identidade dos pesos/release. O p95 esperado no
conjunto aceito é aproximadamente 110,47 ms. O relógio de coleta avança mesmo
quando a sessão está concluída; isso significa uma nova leitura do mesmo estado,
não chegada de novas transações. A fila segue em `session-open` nessa etapa;
a unificação no portal é o próximo recorte.

Para executar o observador em background, com a plataforma já acessível:

```bash
monitor_dir="$PWD/data/storage/kind-platform/observability/session-monitoring"
mkdir -p "$monitor_dir" && chmod 700 "$monitor_dir"
(umask 077; nohup poetry run python -m scripts.session_monitoring.controller watch \
  --session-id arrival-2k-v1 </dev/null >"$monitor_dir/watch.log" 2>&1 &)
tail -60 "$monitor_dir/watch.log"
```

O lock impede um segundo observador. Prefira foreground durante o aceite, para
parar com Ctrl+C. A parada afeta apenas a coleta; produtor, API, fila e serviços
permanecem. Arquivos privados e logs continuam sob `/data/`, já excluído do Git
e de contextos de imagem. Após reiniciar o host, o observador precisa ser iniciado
novamente; o receiver fica inválido até receber nova coleta. O gerenciamento de
acessos `access-*` mantém sua função de abrir/fechar as interfaces locais.

## Validade e interpretação

| Indicador | Significado |
| --- | --- |
| `source_valid` | Coleta com até 30 s, mesma identidade e produtor sem falha ou heartbeat ativo vencido |
| `collection_age_seconds` | Idade de parede da leitura; não usa o timestamp histórico do evento |
| `producer_stale` | Produtor em fase ativa sem heartbeat por mais de 15 s; pausa/conclusão podem ficar ociosas |
| `events`, `transactions`, `feedback` | Contagens do contexto e da janela capturada, não apenas decisões pontuadas |
| `decisions`, `alerts`, `customers`, `decision_feedback` | Totais da mesma sessão da fila, incluindo feedback disponível das decisões |
| `complete` | Todas as decisões e eventos previstos, inclusive feedback, foram persistidos |
| `http_latency_*_ms` | Percentis exatos de todos os recibos HTTP confirmados da sessão, incluindo retries |
| `http_retries` | Tentativas adicionais dos recibos confirmados; não é taxa total de falhas HTTP |

Os percentis não são uma janela móvel nem latência isolada do servidor. Excluem
warmup, espera programada e reconciliação; não se somam percentis nem se infere
throughput pela sua divisão. Não há limiar de SLA inventado para colorir latência.
O relógio de evento histórico continua disponível em métrica própria.

Sem publicação válida, as contagens anteriores são retidas no receiver, mas os
painéis de negócio usam `source_valid == 1` para não exibi-las como atuais.
A interrupção do receiver também aparece em `up`. As regras
`FraudSessionSourceStale` e `FraudSessionReceiverDown` distinguem as duas situações.
O runtime começa sem contagens e inválido; reiniciar o pod não inventa zeros de
negócio. Uma nova publicação restaura a projeção sem pontuar novamente.

Azul representa volume/latência; amarelo, investigação; verde/vermelho, saúde da
coleta. Um alerta não equivale a fraude confirmada. A validação completa de
qualidade continua na área de desempenho do portal, vinculada ao modelo avaliado.

## Novo baseline operacional

A extensão altera o template do exporter, a configuração Prometheus/Grafana e
acrescenta Secret/ConfigMap. **O baseline anterior representa a topologia anterior**;
não edite seu hash e não force sua igualdade com a configuração nova.
O autor emitiu e conferiu o baseline atual em 2026-10-24:

- Caminho: `data/storage/kind-platform/operations/baseline-session-monitoring-20261024.json`.
- SHA-256: `676e04b92c9d8e479ce598a5dd348c13b9db0755fc89bba918d415f14df0ac9d`.
- Resultado: `current_platform_readbacks_verified`; portal e desempenho preservados.

Para checagem cotidiana, use o baseline já existente:

```bash
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-session-monitoring-20261024.json
```

Não reemita nem sobrescreva esse arquivo. Em outra instalação, ou após uma nova
alteração de configuração, gere outro baseline com caminho distinto:

```bash
PLATFORM_BASELINE="data/storage/kind-platform/operations/baseline-session-monitoring-$(date -u +%Y%m%dT%H%M%SZ).json" \
PLATFORM_TRACKING_REPORT="data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json" \
make platform-baseline
```

Use o caminho impresso para `platform-check` e para futuros ciclos de stop/start.
Emitir o baseline não comprova novo stop/start ou reboot. A transição de fonte
commitada permite ler os inputs aceitos sem alterá-los; contratos, código
científico, gate e pesos permanecem congelados.

## Trocar explicitamente a sessão observada

Para uma próxima sessão preparada, pare o observador atual e informe o SHA do
recibo **atualmente instalado**, além do novo ID. O controller arquiva o vínculo
anterior e recusa troca silenciosa ou instalação pendente de outro vínculo:

```bash
make session-monitor-prepare SESSION_MONITOR_ID=NOVO_ID \
  SESSION_MONITOR_PREVIOUS_SHA256=f82d8fb8c2e25f533c31d03f7dc430b4a15ef2761758840506a7136de94edf3e &&
make session-monitor-up
make session-monitor-watch SESSION_MONITOR_ID=NOVO_ID
```

A próxima execução do produtor alimentará a mesma fila/checkpoint observados.
A vinculação altera configuração e pede outro baseline. A promoção de sucessor
com pesos diferentes e seu rollback continuam sujeitos a candidato qualificado
e ao aceite nativo correspondente; este recorte não altera essa política.

## Aceite de 2026-10-24

O ensaio nativo passou: 1 teste em 68,39 s; ambiente `session-monitoring` aprovado
em 82,19 s. Conferiu a mesma sessão de 2.001 decisões, 81 alertas, 1.561 clientes,
feedback das decisões, p50/p95/máximo HTTP, expiração e retomada da coleta.
Estado histórico, dez regras saudáveis, proxy Grafana, bytes aceitos e champion
foram preservados. O autor confirmou a revisão visual com o observador ativo.
Essa confirmação é registrada separadamente: `visual_review_validated: false`
no relatório automatizado original não é reescrito.

A regressão geral passou: **1.705 testes, 51 pulados e 12 subtestes**, em 365,26 s;
`py314: OK`, total 386,24 s. Os 165 warnings reportados continuam registrados e
não são tratados como correções deste fechamento.

O baseline novo e os gates do portal/desempenho passaram. Isso registra a
configuração corrente, sem ampliar o aceite para outro stop/start, reboot,
chegada concorrente no KinD ou firing de alertas. A promoção de sucessor com
pesos diferentes continua dependente de candidato qualificado. O próximo recorte
é reunir a fila corrente e as evidências em um único portal Streamlit.
