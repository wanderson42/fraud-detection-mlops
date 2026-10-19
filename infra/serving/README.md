# Bancada de troca e rollback do serving

O Dockerfile operacional já existe em `docker/serving/`. Este diretório fixa
somente a nova imagem do gateway NGINX. Não há outro runtime do modelo nem outro
banco. O controller cria containers próprios e duas redes temporárias para o ensaio;
nenhum serviço existente é substituído.

[Procedimento, gates e limites](../../docs/operations/SERVING_ROLLOUT.md).
`images.lock.json` fixa o gateway por digest e aponta a evidência da imagem de
serving anterior. O gate Docker foi aprovado pelo autor em 2026-10-19: troca A → B → A,
falha 502 e retorno verificado em 3,10 s, com score preservado. Não exige rebuild ou
parada da bancada de tracking/observabilidade.

Backends sem portas publicadas na rede interna; NGINX com bridge adicional de
entrada e porta loopback. Gates privados usam HTTP por `docker exec`.

A bancada é descartável: remove todos os seus recursos. Uma réplica ativa,
candidata temporária e janela de retenção anterior são a direção escolhida para
a entrega futura; o fluxo versionado e o provisionamento Helm ainda não foram
implementados. [Aceite e decisão](../../docs/operations/SERVING_ROLLOUT.md#aceite-nativo--2026-10-19).
