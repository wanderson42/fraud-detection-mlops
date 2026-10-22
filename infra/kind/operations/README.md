# Configuração operacional do host

`90-fraud-platform.conf` registra os mínimos inotify usados para resolver o
bootstrap deste host. Não é aplicado automaticamente. Revisão, instalação manual,
gate de parada/subida e reboot planejado estão em
[Operação do KinD](../../../docs/operations/KIND_OPERATIONS.md).
Nenhum daemon, cluster ou volume é recriado para comprovar retomada.

Aceite em 2026-10-21: parada/subida e reboot planejado aprovados; valores inotify
512/524288 persistiram no mesmo host, sem recriar o node ou os volumes.
