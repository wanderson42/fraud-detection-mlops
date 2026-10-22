# Runtime do exporter no KinD

Imagem stdlib com collector/exporter existentes, contrato Gold e worker de cópia.
Contexto allowlist, sem dados, modelos, dependências pip ou credenciais.
O controlador não usa hostPath: transfere três arquivos verificados a um PVC
separado. A imagem não contém o snapshot. Prometheus e Grafana são reutilizados
por digest e importados somente na plataforma linux/amd64.

[Runbook](../../../docs/operations/KIND_OBSERVABILITY.md).
