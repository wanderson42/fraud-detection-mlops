# Jobs da cadeia de dados no KinD

**Estado: aceite nativo informado pelo autor em 2026-10-20.** Os produtores
existentes executaram **Bronze → Silver → Gold** em dois Jobs Helm no cluster
`fraud-platform`, com publicação e restauração independente das novas saídas.
A entrada foi o bundle Bronze já publicado; os caminhos primários dos leitores
permanecem os atuais.

## Aceite nativo — 2026-10-20

Execução `22f16340baea4d119fd3c05b9ee3a7a1`, release temporária
`fraud-chain-22f16340baea`: `status=success`, `phase=complete`.
O teste nativo passou em 63,98 s; tox concluiu em 80,06 s. O relatório completo
foi fornecido pelo autor como saída de terminal; a evidência registra seu JSON,
hashes das fontes/imagens e envelopes, sem atribuir um hash aos bytes do arquivo
original do recibo que não foi enviado diretamente.

| Saída nova | Arquivos de payload | Objetos do bundle verificados | Bytes de payload | Verificador independente |
| --- | ---: | ---: | ---: | --- |
| Silver | 187 | 188 | 40.288.896 | 183 partições; 1.754.155 transações distintas; 14.681 fraudes |
| Gold | 48 | 49 | 25.196.413 | 42 partições; 402.877 linhas; 19 features |
| Total | 235 | 237 | 65.485.309 | As linhas das camadas se sobrepõem |

Os 237 objetos incluem os dois manifestos dos bundles. Dois envelopes de
conclusão e o marcador de ownership do bucket ficam fora desse total.
O Gold consumiu exatamente o novo bundle Silver do primeiro Job:

- Bronze de entrada: `bca0bdcbd60853db575dcdd5af986fd7608057c69fb368fd5fa0007aa62bc4c0`.
- Silver produzido: `5a665d9654980fb171eeebc57c513af6529274683d043c73ae9c8f726f1cb606`.
- Gold produzido: `893d241ffd86d465043fa61a374a985b63af926ed5639d12b7219475ffe1655e`.

As identidades dos pods/PVCs/PVs PostgreSQL/RustFS permaneceram iguais.
Os três containers de cada Job concluíram com as imagens CRI registradas.
Depois dos restores e verificadores, a release temporária foi removida;
as cópias S3 e as cópias recuperadas no host foram retidas. Gold recalculou
features conforme o protocolo existente; nenhum modelo foi treinado ou avaliado.

[Evidência e recibo completo](../../references/evidence/kind_pipeline_author_validation_2026-10-20.json).
As identidades `docker.io/library/import-...` são identidades locais do CRI;
não representam publicação em um registry. Este aceite não declara igualdade
byte a byte com os Parquets históricos ou recuperação por perda do cluster/host.

O objetivo é demonstrar execução e recuperação da pipeline no cluster, com dados,
código, contratos e saídas identificados. A execução Silver converte os pickles
confiáveis do snapshot congelado; a execução Gold **recalcula features** segundo
o protocolo temporal existente. Isso reproduz a construção dos dados e não
constitui nova avaliação, confirmação ou promoção de modelos.

## Sequência

1. O controlador confere a propriedade do cluster e as imagens carregadas no node.
2. Instala uma release Helm temporária, contendo o Job Silver.
3. O Job baixa o bundle Bronze do RustFS, verifica identidade e integridade,
   executa o produtor Silver e publica um novo bundle com manifesto por último.
4. O host restaura esse Silver em uma nova área e aplica o verificador existente.
5. Somente após esse aceite, um upgrade da mesma release acrescenta o Job Gold.
   A especificação do Job Silver permanece idêntica.
6. O Job Gold consome o **novo Silver identificado**, constrói as features e
   publica outro bundle. O host também restaura e verifica essa saída.
7. O controlador confere as identidades dos pods/PVCs/PVs da fundação e remove
   apenas a release temporária dos Jobs. Bundles S3, cópias restauradas e recibos
   do host permanecem disponíveis.

Cada Job contém `download` e `compute` como init containers, seguidos por
`publish` como container principal. Compartilham somente um `emptyDir` de 4 GiB
e um `/tmp` de 128 MiB. Não há `hostPath`, bind do checkout ou PVC novo. A release
`fraud-platform` e a release `fraud-tracking` existentes não são atualizadas.

## Imagens e credenciais

A imagem `science` contém um subconjunto Linux/Python 3.14 de `poetry.lock`,
com versões e hashes exatos de NumPy, pandas, PyArrow, DuckDB, Typer e dependências.
O Docker instala apenas wheels e confere `pip check`. Não instala bibliotecas
de treinamento, MLflow ou Optuna. Os produtores e seus verificadores são copiados
com seus fontes e os quatro arquivos de referência necessários.

A imagem `transport` usa o SDK S3 já travado no projeto. Somente os containers
`download` e `publish` recebem chaves RustFS pelo Secret existente da fundação.
O container `compute` não recebe variáveis AWS, PostgreSQL ou MLflow nem token da
ServiceAccount; também desativa os ServiceLinks. As chaves não são gravadas nos
arquivos de trabalho, values Helm, imagem ou recibos. Ainda são as credenciais
administrativas do RustFS da bancada, sem IAM por aplicação.

O Job Silver verifica o hash do inventário arquivado contra o inventário congelado
na imagem **antes de ler qualquer pickle**. Confere também os blobs Git e hashes
do bundle completo. Esse caminho só aceita o snapshot Handbook revisado; não é
uma interface para executar pickles recebidos de usuários ou fontes arbitrárias.

A separação de containers limita o acesso a credenciais; não é uma sandbox de
rede. Este incremento não aplica NetworkPolicy. Os containers são processos
não root (`10001`), sem capabilities, sem elevação de privilégios, com raiz
somente leitura e seccomp padrão. Os dados ainda exigem o nível de confiança
estabelecido no inventário congelado.

## Executar

Pré-requisitos: fundação e MLflow aceitos, quatro bundles publicados no RustFS do
KinD e controlador de datasets com imagem/ownership preservados. Não recriar o
cluster. Usar a mesma branch `feat/kind-helm-serving`.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/infra/test_kind_pipeline.py \
  tests/infra/test_kind_pipeline_workers.py \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py &&
poetry run python -m scripts.platform.kind_pipeline validate &&
poetry run python -m scripts.platform.kind_pipeline build &&
.venv/bin/python -m tox -e kind-pipeline
```

`build` constrói duas imagens e as carrega somente no node do cluster dedicado.
A instalação das dependências pode levar alguns minutos na primeira execução.
O recibo `images.json` registra hashes dos fontes/referências, imagens Docker e
identidades efetivas do CRI. Mudanças nesses pins ou no node são recusadas para
reutilização; não substitua esse recibo automaticamente.

O teste nativo equivale ao comando:

```bash
poetry run python -m scripts.platform.kind_pipeline rehearse
```

A entrada é selecionada pelo inventário dos bundles **locais preparados**, sem
ler os payloads originais no host. Deve haver exatamente um bundle Bronze;
ausência ou ambiguidade interrompem antes de criar Jobs. Os bytes usados nos
Jobs são baixados do RustFS interno, sem acesso ao RustFS do Compose.

## Critérios de aceite e recibos

| Etapa | Saída exigida pelo gate nativo |
| --- | --- |
| Silver | 183 partições, 1.754.155 transações distintas e 14.681 fraudes |
| Gold | 42 partições, 402.877 linhas, 19 features; splits 268.668 / 67.255 / 66.954 |
| Ligação | Input do Gold igual ao bundle produzido pelo Job Silver |
| Independência | Download posterior no host, sem acessar o `emptyDir` do Job |
| Proveniência | Inventário, contratos e protocolo com hashes iguais aos fontes da imagem |
| Runtime | Jobs/pods pertencentes à execução, três containers concluídos e imagens efetivas iguais ao recibo do build |
| Preservação | Mesmas identidades dos pods/PVCs/PVs PostgreSQL/RustFS antes e depois |
| Limpeza | Somente a release temporária dos Jobs removida após sucesso |

Os bundles **novos** são publicados em `fraud-datasets/silver/v1/<id>/` e
`fraud-datasets/gold/v1/<id>/`. Seus IDs podem diferir dos arquivos históricos:
os manifestos e auditorias pertencem a uma nova execução. Não é exigida igualdade
dos manifestos antigos, que incluem timestamps e run IDs. O aceite exige os
verificadores, contratos e contagens da coorte congelada; não declara paridade
byte a byte com os Parquets históricos ou nova qualidade preditiva.

O envelope de conclusão remoto fica em
`platform-jobs/v1/<run-id>/<etapa>/receipt.json`, escrito condicionalmente e
relido após o manifesto. O controlador valida a ligação input/output, hashes
das referências, worker e envelope, antes de aceitar a etapa.

No host, tudo fica sob `data/storage/kind-platform/`:

- `pipeline/images.json`: imagens, fontes e node.
- `pipeline/values-<run>-<etapa>.json`: plano sem credenciais.
- `pipeline/rehearsal-<run>.json`: execução, ligação das etapas, runtime e restores.
- `datasets/restore-<id>/`: saídas baixadas e verificadas independentemente.

Os caminhos `/work/...` dos recibos dos produtores pertencem aos containers e
são temporários. Use o `restored_directory` do recibo de restauração para acessar
a saída retida no host. O relatório aceito é `rehearsal-22f16340baea4d119fd3c05b9ee3a7a1.json`,
registrado na evidência acima. Nenhum aceite de execução em Kubernetes é inferido apenas
pela renderização do Helm ou pelos testes com S3 em memória.

## Falha e recursos

Cada Job tem `backoffLimit=0`, `restartPolicy=Never` e prazo de 1.800 s.
A conversão recebe até 2 CPUs/4 GiB, com request de 1 CPU/2 GiB; os containers S3
recebem até 1 CPU/512 MiB. O controlador espera até 1.860 s por etapa, imprime
progresso e inclui estados e logs limitados dos containers no relatório de falha.
Não há retry automático de transformações parciais.

Se download, produtor, publicação ou restore independente falhar, o próximo Job
não começa. O relatório fica `failed` e a release/Jobs existentes são preservados
para diagnóstico, incluindo os `emptyDir`s enquanto os pods existirem. Objetos
S3 já publicados permanecem; não há limpeza automática de bundles órfãos. Uma
nova execução recebe outro run ID e outra release, sem adotar a anterior.

Para investigar, use o kubeconfig e namespace dedicados; o nome da release e os
pods estão no relatório. Examine o relatório antes de repetir o comando ou
remover recursos. Não use comandos de limpeza de todo o namespace ou do cluster.

Este gate não cobre extração Bronze em Job, agendamento/orquestrador permanente,
replay/checkpoints, serving, Prometheus/Grafana no cluster, backup externo do PVC,
recuperação por perda do node/host ou alteração da origem primária de todos os
consumidores. Essas extensões continuam no roadmap.
