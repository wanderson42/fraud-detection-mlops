# Cluster dedicado do portfólio

<!-- kind-operations-acceptance-2026-10-21:start -->
**Estado em 2026-10-21 — marco KinD/Helm concluído no escopo do laboratório.**
Parada/subida e reboot planejado aprovados no mesmo node e volumes, com inotify
persistente e leituras de tracking, serving e monitoramento conferidas.
[Sequência integrada, aceites e limites](../../docs/operations/KIND_PLATFORM.md). Próximo: simplificação
e fechamento funcional do portfólio; Terraform permanece posterior. Notas
datadas abaixo preservam o estado de cada incremento, sem reabrir gates aceitos.
<!-- kind-operations-acceptance-2026-10-21:end -->


Configuração de um node Kubernetes 1.35.8 fixado por digest. O controlador cria
`fraud-platform` com kubeconfig separado sob `data/storage/kind-platform/`.
Não há mounts do checkout ou volumes Compose no node. PVCs ficam no storage
local interno do KinD: sobrevivência à recriação de pods precisa de aceite;
exclusão do cluster não é coberta por essa persistência.

[Runbook, versões e comandos](../../docs/operations/KIND_FOUNDATION.md).

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](../../docs/operations/KIND_TRACKING.md#aceite-nativo--2026-10-20).

## Operação aceita e próximo marco

Os recortes de datasets, Jobs, replay, serving e observabilidade foram aceitos
por incrementos. Parada/subida e reboot planejado passaram em 2026-10-21, com
mesmo node/PVCs e valores inotify persistentes. O marco KinD/Helm está concluído
no escopo do laboratório; próximo: simplificação e fechamento funcional.
[Guia integrado](../../docs/operations/KIND_PLATFORM.md) e
[operação diária](../../docs/operations/KIND_OPERATIONS.md).
Os aceites não declaram migração histórica, promoção ou recuperação por perda
do host. Notas datadas acima preservam o escopo do respectivo incremento.
