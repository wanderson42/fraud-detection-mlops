# Replay com checkpoint no KinD

**Estado: aceite nativo informado pelo autor em 2026-10-20.**
Este recorte valida a retomada do executor `DurableTriageReplay` existente.
A cadeia Bronze–Silver–Gold já teve seu aceite próprio; aqui usamos uma fixture
pequena para testar falhas sem reprocessar o histórico de fraude.

## Aceite nativo — 2026-10-20

O autor executou o gate `kind-replay`: 1 teste aprovado em 17,89 s; tox concluiu
em 33,80 s. Recorte `2de730eacb52459fb4e7e9c387af2d93`, `status=success`,
`phase=complete`. O novo pod tem UID diferente; Deployment, PVC e PV permaneceram
os mesmos. As identidades PostgreSQL/RustFS também foram preservadas.

| Verificação | Resultado observado |
| --- | --- |
| Estado antes da interrupção | 8 eventos: 6 transações/scores, 2 feedbacks e 4 alertas |
| Transação interrompida | `feedback:2`, antes da atualização do checkpoint e do COMMIT |
| Retomada | 12 eventos novos e 10 entregas reutilizadas; somente 4 chamadas novas ao scorer |
| Ledger final | 20 eventos distintos, 10 transações/scores, 10 feedbacks e 6 alertas |
| Comparação integral | Schema e todas as linhas iguais à execução sem interrupção |
| Nova reentrega completa | Resultados reutilizados sem rescoring ou alteração das linhas |
| Encerramento | Processamento temporário removido; PVC do ensaio retido |

A comparação integral inclui scores e alertas, mas **a queda deste ensaio ocorreu
na transação de feedback**. Ela não demonstra interrupção do pod durante a escrita
de um novo score/alerta. O teste comprova recuperação dessa transação incompleta
e retomada consistente do ledger sintético no mesmo PVC, preservando a identidade
do modelo e da política. Não é ensaio de perda de energia, host ou cluster.

O hash lógico final e o da referência são
`bc11cbc86e2a2763f3f67992714d53ec8f39cbf2d314f4d078b518363f7e9856`.
O PVC retido é `fraud-replay-2de730eacb52-state`, UID
`d68d5529-7b3d-4a08-a569-0ca8c72c9366`; os bancos e marcadores ficam em `/state/run`.
A imagem e os recibos permanecem disponíveis; não remova esse PVC sem decidir
explicitamente descartar a evidência do ensaio.

[Evidência e recibo completo](../../references/evidence/kind_replay_author_validation_2026-10-20.json).
O JSON foi fornecido como saída de terminal. Os hashes das fontes foram
conferidos contra o código entregue; não atribuímos hash aos bytes do arquivo
nativo original, que não foi anexado diretamente. As identidades CRI locais
`import-...` não representam publicação em registry.

## O que acontece no ensaio

Uma release Helm temporária instala um Deployment com uma réplica, estratégia
`Recreate` e PVC exclusivo de 1 GiB no cluster `fraud-platform`. A imagem é
construída com Python e dependências fixados, carregada apenas nesse KinD e
conferida contra as identidades Docker/CRI registradas.

O primeiro processo gera um modelo de demonstração e 22 entregas de eventos:
20 eventos distintos (10 transações e seus 10 feedbacks atrasados), mais duas
reentregas. O modelo é explicitamente sintético; não representa a referência.
Uma execução sem interrupção estabelece a comparação no mesmo PVC.

O executor grava oito eventos. No nono, um trigger **temporário**, exclusivo
do ensaio, interrompe a execução durante o feedback `feedback:2`, depois de
escrever o feedback/evento e antes de atualizar o checkpoint e executar `COMMIT`. O controlador identifica o pod pelo
UID e sua cadeia Deployment–ReplicaSet, solicita sua exclusão com grace period
de um segundo e aguarda a confirmação. Não há fechamento cooperativo da conexão
nem retry de treinamento. O Kubernetes cria um novo pod para o mesmo Deployment.

O novo processo abre o mesmo checkpoint. O gate exige que a recuperação do journal
SQLite tenha desfeito a transação inteira: o estado deve conter exatamente os
oito eventos confirmados anteriormente. A seguir ele reentrega todos os eventos,
reutiliza os já confirmados e processa os restantes. Uma terceira passagem exige
reutilização de todos os resultados e bloqueia qualquer nova chamada ao scorer.

O gate compara o schema e **todas as linhas de todas as tabelas** dos dois bancos,
incluindo features, scores, feedbacks, decisões, alertas, política e checkpoint.
Não compara apenas contagens ou bytes físicos do SQLite. O orçamento de alertas
por cliente/dia também participa da comparação. A referência é local e sintética;
esta igualdade comprova recuperação operacional, não qualidade preditiva.

## Executar

Requer a fundação existente, pertencente ao controlador, com pods/PVCs prontos.
Não exige o laboratório Compose nem modifica seu roteamento.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/infra/test_kind_replay.py \
  tests/infra/live/test_kind_replay_live.py \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py &&
poetry run python -m scripts.platform.kind_replay validate &&
poetry run python -m scripts.platform.kind_replay build &&
.venv/bin/python -m tox -e kind-replay
```

O primeiro comando ignora o teste live; o último autoriza a interrupção somente
do pod da nova release aleatória. O controlador utiliza o kubeconfig/contexto
privado da fundação e a mesma trava de operação dos recortes anteriores.
Não usa o contexto global do kubectl.

No sucesso, conferir `status=success`, `phase=complete`, UIDs diferentes dos pods,
mesmos UID/PV do PVC, comparação integral dos bancos e fundação preservada.
O relatório aparece em `data/storage/kind-platform/replay/rehearsal-<id>.json`.

## Persistência e limites

A release temporária é removida após a verificação. O PVC fica retido por
`helm.sh/resource-policy: keep`, com nome e UID no relatório, para inspeção dos
bancos, da release sintética e dos marcadores. Cada execução cria outro PVC;
sua remoção posterior exige identificar o recurso exato e decidir descartar
essa evidência. Não há limpeza ampla de namespace, PVCs ou volumes existentes.

Em falha, o controlador guarda o relatório e tenta capturar logs/estado; preserva
os recursos do ensaio. Não repetir sucessivamente sem revisar esse diagnóstico.
A imagem e seus recibos também permanecem; mudanças em fontes vinculadas à imagem
são recusadas antes de uma nova execução.

O pod recebe apenas seu PVC e `/tmp`, sem hostPath, credenciais S3/PostgreSQL/MLflow
ou token de ServiceAccount. Executa sem root, com filesystem raiz somente leitura
e limites de recursos. Isso não implementa NetworkPolicy ou isolamento de rede.
SQLite continua com um único escritor; não há múltiplas réplicas, broker, consumer
groups ou garantia de entrega de alertas em sistemas externos.

Este gate não migra o checkpoint histórico, não comprova perda do host/cluster,
não executa backup/restore nem mede throughput, latência ou desempenho do modelo.
O modelo sintético é ajustado uma vez na preparação; a retomada o carrega sem
novo fit. Os dados e modelos históricos permanecem preservados.
