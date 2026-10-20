# Cluster dedicado do portfólio

<!-- kind-foundation-acceptance-2026-10-20:start -->
**Estado em 2026-10-20 — fundação KinD/Helm aprovada.** PostgreSQL e RustFS
prontos; marcadores SQL/S3 preservados após recriar os pods, com os mesmos PVCs.
Regressão nativa: 45 testes aprovados. Bootstrap corrigido após ajuste de
inotify no host; persistência desse ajuste após reboot ainda não confirmada.
A fundação foi integrada ao MLflow; o aceite e o próximo recorte estão registrados abaixo.
[Aceite, pré-requisito e limites](../../docs/operations/KIND_FOUNDATION_ACCEPTANCE.md). As notas anteriores mantêm seu contexto histórico.
<!-- kind-foundation-acceptance-2026-10-20:end -->


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

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.
