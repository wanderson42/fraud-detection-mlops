# Carrier compartilhado da release

`Dockerfile.artifacts` e seu dockerignore são usados pela primeira implantação
canônica (`scripts/operational_round/serving.py`) e pela manutenção do instalador
(`scripts/platform/serving_recovery.py`). Eles recebem somente seed.py e as duas
releases pinadas em um contexto de build restrito. Nenhum dado, checkout inteiro
ou credencial é copiado. A API usa a imagem qualificada e pesos identificados.

O controlador da antiga instalação sintética `fraud-serving` foi aposentado.
O seeder corrente e idempotente é `scripts/operational_round/seed.py`; os bytes de
imagens antigas já aceitas não são reconstruídos por esta limpeza.

[Fluxo canônico](../../../docs/operations/OPERATIONAL_ROUND.md).
