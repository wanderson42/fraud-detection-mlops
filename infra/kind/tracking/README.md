# Runtime MLflow para o KinD

Build usa o mesmo lock operacional de `infra/tracking/requirements.txt`, com
hashes. O Dockerfile acrescenta apenas o worker de bootstrap/readback S3.
O serviço não executa fit; o cliente científico roda em processo separado.

O controlador registra fonte, ID Docker e identidades CRI após o load. Tag local
única e imagePullPolicy Never; nenhum push de registry. Contexto de build
filtrado por `Dockerfile.dockerignore`; dados e credenciais ficam fora dele.

[Procedimento](../../../docs/operations/KIND_TRACKING.md).
