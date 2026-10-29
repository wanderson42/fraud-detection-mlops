# Componentes Grafana com fontes da sessão reproduzida

Esta extensão mantém a release `fraud-observability`, seu receiver único, o
Prometheus/Grafana e seus dois PVCs. Acrescenta quatro dashboards ao perfil
`session-only`. O dashboard `fraud-product-session` permanece byte a byte igual.
O perfil legado conserva seus próprios dashboards e sua fonte original.

| Dashboard | Fonte e limite |
| --- | --- |
| Plataforma e sessão identificada | Readiness e restarts dos pods atuais, lidos pelo observador no Kubernetes. O papel `api` representa explicitamente o slot blue. Não é uptime, SLA ou prova de disponibilidade HTTP. |
| Operação da sessão e feedback | Contadores causais e recibos HTTP da sessão. Tempos de fila, atraso de emissão e scheduled-to-commit permanecem indisponíveis. |
| Saúde das features da sessão | Requisições persistidas, contrato e coerência. Comparação exige primeiras 1.000 versus últimas 1.000 decisões sem sobreposição. |
| Triagem com feedback da sessão | Coorte de decisões com feedback amadurecido. Métricas por cliente-dia e AP transacional são identificadas separadamente; não representam o dia completo ou validação global. |

A coleta lê features/feedback na mesma transação SQLite dos contadores. Somente
agregados com nomes fixos saem do host. A imagem do receiver continua usando
apenas stdlib e não monta checkpoints, dados brutos ou credenciais Kubernetes.
Somente o observador do host faz o readback Kubernetes. As novas séries usam
`fraud_session_detail_*`, com o mesmo ID/digest de sessão. Não há reaproveitamento
de nomes `fraud_replay_*` para disfarçar ausência de um replay legado.

A validade expira após 30 segundos sem publicação, como antes. Os novos painéis
filtram por fonte válida e capacidade de detalhes. Valores indisponíveis não
viram zero; sem denominador, precisão/recall/AP permanecem ausentes. Os tempos
históricos do evento continuam separados do relógio de coleta. A política,
threshold, modelos, dados e implementações estatísticas existentes não mudam.

## Preparação

Aplicar, testar e commitar o código antes de construir a candidata. Não há
necessidade de repetir treino, scoring, captura de eventos ou preparação da sessão.
O observador antigo pode continuar durante a preparação, mas deve ser encerrado
antes de apply, verify ou rollback (esses comandos exigem o lock livre).

```bash
poetry run python -m scripts.session_monitoring.components prepare \
  --id grafana-components-v1 --session-id repro-arrival-200-v1 --no-other-writers
```

A preparação confere sessão reconciliada, propriedade, fontes e valores Helm;
constrói/carrega outra imagem do receiver, testa seu contrato em Docker sem rede,
renderiza Helm e guarda o plano em
`observability/session-monitoring/components-maintenance/grafana-components-v1/`.
Não altera o deployment. Os bytes originais de image/prepared/enabled são copiados
com permissão 0600 e hashes, sem sobrescrever os recibos históricos.

## Aplicação, verificação e recuperação

Após revisar a saída de prepare, encerrar o observador pelo terminal onde ele está
rodando (Ctrl+C), preservando os serviços. Confirmar ausência com:

```bash
pgrep -af 'scripts\.session_monitoring\.controller watch'
```

Não apagar o arquivo de lock. Usar o digest real impresso por prepare:

```bash
poetry run python -m scripts.session_monitoring.components apply \
  --id grafana-components-v1 --approval-sha256 SHA_DO_PLANO --no-other-writers
poetry run python -m scripts.session_monitoring.components verify --id grafana-components-v1
```

A aplicação mantém as identidades protegidas e dos PVCs. O checksum da configuração
provoca rollout dos componentes de observabilidade; não se afirma zero downtime.
O novo receiver, Prometheus e Grafana são verificados, incluindo todos os agregados,
a ausência das séries sem suporte e os contratos dos cinco dashboards. A verificação
é um readback da sessão concluída; não executa chegadas nem firing de alertas.

Se aplicar falhar, preservar o plano/recursos. A recuperação usa o digest de rollback
impresso por prepare e não exige que o receiver defeituoso esteja Ready:

```bash
poetry run python -m scripts.session_monitoring.components rollback \
  --id grafana-components-v1 --approval-sha256 SHA_DO_ROLLBACK --no-other-writers
```

Ela restaura os bytes exatos dos três arquivos originais e os valores Helm anteriores.
Os recibos novos e o plano permanecem como histórico; imagens não são removidas.
Não executar bootstrap/up durante uma manutenção incompleta; concluir apply ou
rollback desta manutenção primeiro.

Após sucesso, iniciar um novo observador para carregar a implementação ampliada:

```bash
make session-monitor-watch SESSION_MONITOR_ID=repro-arrival-200-v1
```

Se o acesso persistente do Grafana apontar para o pod anterior, reiniciar apenas
esse encaminhamento com `make access-stop ACCESS_SERVICE=grafana` seguido de
`make access-start ACCESS_SERVICE=grafana`. O túnel SSH continua no loopback.
O portal Streamlit e seus snapshots não são modificados neste recorte.

## Aceite e reprodução

Os testes cobrem renderização real Helm com cinco dashboards, duas PVCs e um
receiver; HTTP real com payload estendido/legado e TTL; SQLite real com features e
feedback; ausência de suporte; aprovação, preservação de bytes e recuperação sem
receiver pronto. O ensaio nativo e a revisão visual ficam pendentes até a execução
na VM. A validação Docker da candidata é uma etapa distinta do aceite KinD.

O baseline/check legado ainda não é ampliado automaticamente para este perfil.
Reboot, backup/restore, supervisão permanente do observador e instalação integral
a partir de ambiente vazio permanecem objetivos separados. Tox verifica apenas os
comandos configurados: uma suíte Python verde não prova reprodução de toda a stack.
