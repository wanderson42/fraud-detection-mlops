# Datasets no RustFS do KinD

Este recorte publica e restaura **bundles já preparados** de Bronze, Silver,
Gold e development Gold no RustFS da fundação `fraud-platform`. Os manifestos,
contratos e inventários arquivados acompanham os dados. A instalação do RustFS
continua pertencendo ao chart Helm `fraud-platform`; este transporte não cria
outro servidor, release Helm ou PVC.

**Estado: aceite nativo informado pelo autor em 2026-10-20.** As quatro camadas
foram publicadas, reenviadas sem novos uploads e restauradas com seus verificadores.
As identidades da fundação foram preservadas. O aceite encerra o transporte dos
bundles; os leitores ainda usam seus caminhos atuais.

## Aceite nativo — 2026-10-20

Relatório `rehearsal-1d25a05058064c8fb9aa4d070a646a33.json`: `status=success`.
O teste nativo passou em 66,78 s; tox concluiu em 81,18 s. A evidência versionada
transcreve o JSON e o resultado enviados pelo autor; não arquiva os bytes originais
do recibo nem atribui um hash a eles.

| Camada | Arquivos de payload | Objetos verificados, incluindo manifesto | Bytes de payload | Verificação semântica |
| --- | ---: | ---: | ---: | --- |
| Bronze | 188 | 189 | 107.309.243 | 183/183 arquivos do inventário |
| Silver | 187 | 188 | 40.288.931 | 183 partições, 1.754.155 transações distintas |
| Gold | 48 | 49 | 25.196.448 | 42 partições, 402.877 linhas e 19 features |
| Development Gold | 66 | 67 | 38.202.741 | 63 partições, 603.669 linhas e 19 features |
| Total | 489 | 493 | 210.997.363 | As contagens de linhas das camadas se sobrepõem |

Os quatro reenvios reutilizaram todos os objetos; o gate de zero uploads passou.
O total de objetos inclui quatro manifestos de conclusão e exclui o marcador
de ownership do bucket. Os IDs dos bundles e os 12 recibos de publicação,
reenvio e restauração estão na
[evidência](../../references/evidence/kind_datasets_author_validation_2026-10-20.json).

Os campos `snapshot_path`, `silver_path` e `gold_path` preservam os caminhos da
quarentena no instante da verificação. Depois, o diretório foi renomeado para
`restored_directory`. Para acessar a cópia aprovada, use esse destino final e
acrescente o caminho relativo do dataset; os caminhos antigos não representam
arquivos ausentes no bundle recuperado.

## Caminho e responsabilidade

| Elemento | Destino |
| --- | --- |
| Fonte do envio | `data/storage/bundles/<bundle-id>/`, montado somente para leitura |
| Serviço de destino | `fraud-platform-rustfs:9000`, namespace `fraud-platform` |
| Bucket nesse RustFS | `fraud-datasets` |
| Objetos | `<camada>/v1/<bundle-id>/<caminho-arquivado>` |
| Marcador do bucket | `kind-datasets-owner.json`, associado ao node e ao workspace |
| Recibos e identidade da imagem | `data/storage/kind-platform/datasets/` |
| Cópia recuperada | `data/storage/kind-platform/datasets/restore-<id>/restored/` |

O nome do bucket é igual ao usado no Compose, mas o endpoint é outro. Não há
leitura do RustFS do Compose: o envio usa o bundle local e aponta explicitamente
para um port-forward do serviço do KinD. Um bucket existente sem o marcador
correto é recusado. Uma inicialização interrompida deixa os recursos para revisão;
não se adota o bucket nem se substitui o marcador automaticamente.

## Reproduzir

Pré-requisito: fundação pronta, ownership preservado e os quatro bundles locais
preparados nos recortes anteriores. Execute na raiz do checkout, na branch
`feat/kind-helm-serving`. Não é preciso recriar o cluster ou o MLflow.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/infra/test_kind_datasets.py \
  tests/infra/test_kind_dataset_layers.py \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py &&
poetry run python -m scripts.platform.kind_foundation status &&
poetry run python -m scripts.platform.kind_datasets inventory &&
poetry run python -m scripts.platform.kind_datasets build &&
.venv/bin/python -m tox -e kind-datasets
```

`inventory` lê somente os manifestos canônicos dos bundles preparados. Sua saída
não afirma verificação dos payloads. `build` cria uma imagem de transporte com as
dependências SDK já travadas em `infra/observability/storage-requirements.txt` e
registra seu ID local e hashes de entrada. O Poetry científico não recebe boto3.
A imagem é um cliente temporário no Docker do host; não é a imagem do MLflow e
não é carregada nos nodes. Se as fontes dessa imagem mudarem, a rotina recusa
reutilizar o recibo antigo; preserve-o para revisar a atualização.

O ambiente tox `kind-datasets` executa `rehearse`, também disponível diretamente:

```bash
poetry run python -m scripts.platform.kind_datasets rehearse
```

O ensaio exige **exatamente um bundle de cada camada** no inventário. Ambiguidade
ou ausência são recusadas antes de qualquer transferência. Nesse caso, use a
saída do inventário para selecionar explicitamente a camada e o bundle desejados:

```bash
poetry run python -m scripts.platform.kind_datasets publish \
  --layer bronze --bundle-id <bundle-id>
poetry run python -m scripts.platform.kind_datasets restore \
  --layer bronze --bundle-id <bundle-id>
```

As outras opções são `silver`, `gold` e `development-gold`. Não inclua os sinais
`< >` ao substituir o identificador. O transporte recusa bundles de modelos,
estudos HGB e snapshots de tracking, mesmo que estejam no diretório de bundles.

## Critérios de aceite

Para cada camada, o ensaio verifica o bundle antes do envio, publica com leitura
integral de todos os objetos e publica o manifesto por último. O reenvio precisa
reutilizar todos os objetos com zero novos uploads. Conteúdo divergente, objeto
extra no prefixo ou tamanho/hash incorreto interrompem a operação.

A restauração é uma nova execução do worker, sem montar o bundle ou os datasets
originais. O manifesto remoto deve ter hash igual ao bundle ID. Os arquivos são
baixados para quarentena e passam pelos verificadores existentes da camada:

| Camada | Verificação depois do download |
| --- | --- |
| Bronze | Inventário, commit Git, manifestos e hashes dos bytes opacos; sem desserializar pickle |
| Silver | Contrato arquivado, schema e verificações SQL existentes |
| Gold | Contratos, protocolo temporal e verificações existentes das 19 features |
| Development Gold | Política e dataset arquivados; estudo SQLite e modelos excluídos |

Apenas depois do verificador e da conferência das identidades dos pods/PVCs/PVs
a quarentena recebe o nome `restored`. Cada execução usa um novo diretório;
restores anteriores são preservados. Um erro deixa recibo `failed`; os arquivos
já baixados para diagnóstico não recebem um aceite de sucesso. Nenhum objeto
remoto ou fonte histórica é removido pelo controlador.

O relatório agregado `rehearsal-<id>.json` referencia os três recibos de cada
camada — publicação, reenvio e restore — com contagens, bytes e verificações
semânticas. O relatório aceito e seu limite de escopo estão registrados na evidência acima.

## Recursos e limites

O cliente usa `docker run --network host` no Linux para alcançar somente o
port-forward configurado em `127.0.0.1` pelo código. Isso **não é isolamento de
rede por política**: o modo host permite acesso à rede do host. A configuração
não depende de portas fixas do Compose. O processo temporário é limitado a
256 MiB/1 CPU/64 processos, raiz somente leitura e capacidades removidas. Os
binds contêm apenas o bundle de origem e o diretório novo de download, nunca o
checkout inteiro ou as bases originais. A execução usa o UID/GID do operador.

Credenciais RustFS são lidas do workspace privado da fundação e passadas por
arquivo temporário modo 600, retirado no `finally`; não entram nos argumentos,
recibos, imagem ou values Helm. O bucket usa as credenciais administrativas da
fundação; não há IAM por aplicação neste laboratório. O worker e o port-forward
são encerrados ao final, inclusive após falha controlada ou timeout. Uma queda
abrupta da máquina pode exigir revisão dos recursos e do arquivo temporário.

O espaço disponível local é conferido antes do download; reserve também espaço
no PVC RustFS para as quatro cópias e no host para a imagem e os restores.
Os arquivos restaurados ficam sob o diretório gerenciado `data/storage/`.

O aceite comprova recuperação de objetos a partir do RustFS **ainda disponível**.
Não comprova backup externo do PVC, recuperação após perda do cluster/host,
execução Bronze→Gold em Jobs, origem S3 primária dos leitores ou migração do
tracking histórico. Não há treino, recálculo de features, promoção de modelo ou
mudança das rotas atuais. Esses limites continuam explícitos no roadmap.
