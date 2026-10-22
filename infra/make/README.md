# Entradas operacionais do Make

`platform.mk` é incluído pelo Makefile da raiz. Use `make help` para o catálogo.
As receitas delegam a `scripts.platform.*`, sem duplicar lógica de Helm, ownership,
locks ou recibos. Os alvos são serializados mesmo quando Make recebe `-j`.
Parâmetros operacionais são exportados e expandidos como argumentos entre aspas.

[Guia de execução](../../docs/operations/PORTFOLIO_WALKTHROUGH.md): apresentação do
ambiente pronto, reprodução de dados e ensaios de falha são jornadas distintas.
API/MLflow/Grafana ficam em primeiro plano; Ctrl+C encerra somente o próprio acesso.
Não há alvo `destroy`, prune, `pkill` global ou reboot automático.
