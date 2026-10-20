# Replay sintético com PVC retido

Chart dedicado ao gate descrito em `docs/operations/KIND_REPLAY.md`.
Um Deployment `Recreate`, uma réplica e um PVC de 1 GiB por release aleatória.
Não altera os charts da fundação, do tracking ou dos Jobs de dados.
Valores aceitam apenas o identificador do ensaio e uma tag local criada pelo
controlador. O controlador verifica também o digest CRI efetivo.

O uninstall remove o processamento, mas mantém o PVC para inspeção. O chart não
fornece HA, backup/restore, migração histórica ou integração com broker.
