# Monitoramento de uma sessão em ambiente novo

Este perfil instala a mesma release `fraud-observability` com receiver de agregados,
Prometheus e Grafana. Não exige nem cria snapshot de replay histórico. O perfil
legado permanece como padrão. A conversão de instalação legada é recusada.

Pré-requisitos: foundation/tracking/serving próprios; sessão v1 concluída e
reconciliada; exportação da sessão compatível; imagens de observabilidade já
construídas. A implementação deve estar commitada antes do bootstrap.

```bash
make session-monitor-bootstrap SESSION_MONITOR_ID=repro-arrival-200-v1
make session-monitor-rehearse SESSION_MONITOR_ID=repro-arrival-200-v1
```

O bootstrap constrói o receiver, vincula sessão/modelo/release e instala o chart.
O receiver não monta checkpoint, dados brutos ou PVC. Somente Prometheus e Grafana
possuem volumes persistentes. O observador lê o checkpoint no host e publica
agregados autenticados. O ensaio exige ausência de outro observador; verifica
expiração após 30 segundos e retomada, sem pontuar novamente.

Após o ensaio, mantenha a coleta com:

```bash
make session-monitor-watch SESSION_MONITOR_ID=repro-arrival-200-v1
```

Ctrl+C encerra somente o observador. Sem publicações, a coleta fica expirada; isso
não altera o estado concluído da sessão. Credenciais permanecem nos arquivos
privados da instalação. Acesso externo deve continuar por túnel SSH e loopback.

O perfil privado `observability/session-only.json` fixa sessão, proprietário,
nó e fontes. Retentativas preservam esses vínculos; divergências devem ser
investigadas, sem apagar recursos ou recibos para contornar a recusa.

Este recorte não aceita novas chegadas concorrentes, firing de alertas,
backup/restore, reboot, nem reprodução integral. Também não torna os comandos
legados de baseline/check compatíveis automaticamente com este perfil. A
integração desse ciclo de vida e a supervisão do observador permanecem pendentes.
O portal continua separado da coleta e suas evidências anteriores são preservadas.
