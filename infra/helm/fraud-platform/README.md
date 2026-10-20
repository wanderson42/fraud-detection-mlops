# fraud-platform 0.1.0

<!-- kind-foundation-acceptance-2026-10-20:start -->
**Estado em 2026-10-20 — fundação KinD/Helm aprovada.** PostgreSQL e RustFS
prontos; marcadores SQL/S3 preservados após recriar os pods, com os mesmos PVCs.
Regressão nativa: 45 testes aprovados. Bootstrap corrigido após ajuste de
inotify no host; persistência desse ajuste após reboot ainda não confirmada.
O fluxo completo no KinD segue em andamento; próximo incremento: MLflow.
[Aceite, pré-requisito e limites](../../../docs/operations/KIND_FOUNDATION_ACCEPTANCE.md). As notas anteriores mantêm seu contexto histórico.
<!-- kind-foundation-acceptance-2026-10-20:end -->


Primeiro recorte do chart: dois StatefulSets (RustFS/PostgreSQL), dois PVCs
com retenção Helm e Services internos. `values.schema.json` exige imagens com
digest, nomes e recursos válidos. Credenciais são referenciadas por Secret
existente, criado pelo controlador sem registrá-las em values/releases.

Não contém MLflow, jobs, replay, serving ou observabilidade ainda. Não oferece
HA ou backup só por ter PVC. O storage local depende da vida do node KinD.

[Operação e teste de persistência](../../../docs/operations/KIND_FOUNDATION.md).
