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

A CI agora tem um job de qualificação e exportação da imagem construída do commit.
`images.lock.json` acrescenta a base Python por digest; os pins da referência e do
gateway não mudam. O build novo usa o Dockerfile existente e release sintética;
o build/gate/export Docker foi aprovado em 2026-10-19, e a execução no
GitHub Actions mantém aceite pendente. Não publica no registry nem altera
serviço permanente. [Entrega versionada](../../docs/operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

A entrega local aprovada contém 11 arquivos, incluindo image.tar de 175.020.032
bytes, para o commit `0b95136`. A recarga desse arquivo em outro alvo ainda não
foi demonstrada. [Aceite nativo](../../docs/operations/SERVING_ROLLOUT.md#aceite-nativo-da-entrega-versionada--2026-10-19).

### Gate de consumo e CI entre jobs — preparação de 2026-10-19

O consumidor verifica o pacote por commit/hash externos, carrega a imagem exata
e testa HTTP em um container novo, sem rebuild ou fit. A entrega original e
as tags alheias são preservadas. A CI encadeia `quality` → `serving-delivery` →
`serving-consumer`; o último job recebe o artifact e o hash do produtor em outro
runner. Implementação e **aceite nativo concluídos; GitHub Actions pendente**.
O teste local usa o arquivo já aprovado e não exige repetir o build.
[Procedimento, pins e limites](../../docs/operations/SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Próximo: commit/push e aceite dos três jobs no GitHub Actions**, sem repetir
os gates locais encerrados. [Resultado e limites](../../docs/operations/SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).
