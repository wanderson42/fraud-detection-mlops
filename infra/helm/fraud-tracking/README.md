# Chart MLflow dedicado

Release `fraud-tracking`, namespace `fraud-platform`, Kubernetes 1.35.x.
Reutiliza PostgreSQL/RustFS da fundação; não cria PVC, namespace ou Secret.
Uma réplica, Service ClusterIP e `/tmp` em emptyDir. O serviço é nonroot, com
root filesystem somente leitura, token de ServiceAccount desabilitado, seccomp,
capabilities removidas, probes e recursos declarados.

Aplicar pelo controlador, que prepara os recursos dependentes e injeta somente
identidades em Helm values. `ownerId` e tag são gerados pelo controlador; os
zeros do values padrão servem apenas para validação de templates. Não executar
uma instalação direta com esses placeholders.

[Procedimento e limites](../../../docs/operations/KIND_TRACKING.md).
