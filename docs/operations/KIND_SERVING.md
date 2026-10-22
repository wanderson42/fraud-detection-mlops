# Serving via Helm no KinD

**Estado: aceite nativo aprovado em 2026-10-21.** O recorte consome
uma entrega sintética qualificada existente. Mantém a imagem exata da API e
instala a baseline no laboratório, acrescentando uma candidata apenas durante o
gate de troca e rollback. O modelo de referência histórico fica preservado.

## Entrada confiável e distribuição da release

O diretório precisa conter a entrega inteira, incluindo `image.tar`, descritor,
release e recibos. Commit e hash do descritor vêm da execução confiável do produtor;
não devem ser extraídos automaticamente do próprio pacote para autorizar seu uso.
O verificador existente confere todos os bytes e vínculos antes de acessar Docker
ou o cluster. A tag de arquivo já existente é preservada se identifica a mesma
imagem; uma tag divergente interrompe a preparação.

A imagem da API é carregada e retaggeada para o KinD, sem novo build. O controlador
constrói somente um **carrier de releases**: imagem auxiliar contendo os três
arquivos blue, os três green e um copiador com validação de hashes, usando Python
por digest e nenhuma instalação pip. Esse contexto contém somente esses arquivos.
A green reutiliza os mesmos pesos e smoke; ganha outro `serving_release_id`.

Cada pod usa o carrier como init container para validar e copiar os arquivos para
um `emptyDir` novo. A API monta esse volume somente para leitura e valida o modelo
novamente no startup. A distribuição deste recorte é por imagens OCI locais;
o pod não lê S3 ou tracking e não recebe credenciais. O carrier é um novo artefato
de distribuição, enquanto o runtime da API continua sendo a imagem qualificada.

## Desenho e gate

A release Helm `fraud-serving` instala baseline blue (uma réplica) e gateway
NGINX (uma réplica). A green é uma segunda réplica apenas no sentido de outro
Deployment/candidato: cada slot tem no máximo um pod. Não há HPA ou canary.
Probes distinguem startup/readiness da API (`/ready`) e liveness (`/health`).
O gateway verifica sua própria saúde separadamente da saúde do backend.

O Service `fraud-serving-active` é ClusterIP e seu seletor escolhe blue ou green.
NGINX encaminha para esse Service; sua configuração e seu pod ficam estáveis.
O port-forward é para o **pod gateway**, em loopback e porta aleatória durante
a invocação. Encaminhar diretamente ao Service ativo não provaria a mudança de
rota, pois o port-forward escolhe um pod e mantém essa conexão.

O gate executa:

1. Baseline por HTTP: `/ready`, `/info`, identidade/score em `/score`, entrada inválida 422.
2. Pacote corrompido rejeitado antes de instalar a candidata ou alterar o seletor.
3. Green adicionada via Helm, init container aprovado, probes prontas e gates HTTP diretos no pod.
4. Seletor alterado por Helm; EndpointSlices precisam apontar exatamente para o UID da green.
5. Mesma URL do gateway precisa devolver a identidade green e o mesmo score de controle.
6. Green reduzida a zero via Helm; EndpointSlices sem targets prontos e erro 502 observado no gateway.
7. `helm rollback` para a revisão baseline capturada antes do ensaio; candidato removido,
   seletor e HTTP blue restaurados, com mesmos UIDs da baseline/gateway e do Service/ClusterIP.

O gate verifica as identidades CRI de todos os containers (inclusive init), seus
controladores, ausência de restarts e a preservação dos pods/PVCs da fundação.
A candidata troca a identidade operacional, não o algoritmo ou os pesos. O score
é de uma fixture sintética; não representa nova validação de qualidade do HGB.
Não há afirmação de zero downtime, SLA ou entrega externa de alertas.

## Executar a entrega local já aprovada

Os pins abaixo correspondem à entrega local aceita anteriormente neste projeto.
O commit é o da **imagem qualificada**, que pode anteceder o HEAD atual.
Se o diretório tiver sido movido, use sua localização real dentro do workspace
permitido e preserve os mesmos pins. Para outra entrega, informe os pins obtidos
de seu próprio produtor confiável. A rotina recusa um pacote ausente/alterado;
não recria automaticamente o modelo ou a entrega.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/infra/test_kind_serving.py \
  tests/serving \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py &&
poetry run python -m scripts.platform.kind_serving validate &&
poetry run python -m scripts.platform.kind_serving prepare \
  --directory data/serving/deliveries/1b85bede4ac24fc781491ef24eea0209 \
  --expected-source-sha 0b95136a462583441044d50de0884a558777b260 \
  --expected-descriptor-sha256 aa0e79886800765c516d3ed776295dc46d657a7ea50eb7c88900241d8c0ff0d7 &&
poetry run python -m scripts.platform.kind_serving up &&
poetry run python -m scripts.platform.kind_serving status &&
.venv/bin/python -m tox -e kind-serving
```

Requer a fundação existente, pertencente ao controlador, com seu kubeconfig
privado e node pronto. Os comandos mutáveis usam a mesma trava de operação dos
recortes anteriores. Os charts da fundação, MLflow, Jobs e replay não mudam.
Não usa o contexto global do kubectl nem cria/adota outro cluster.

## Conferir e encerrar o ensaio

Recibos ficam em `data/storage/kind-platform/serving/`: preparação, instalação,
valores Helm e `rehearsal-<id>.json`. No sucesso, conferir os seis eventos,
`rollback_verified=true`, `native_Helm_rollback_validated=true`, runtime Docker
igual ao qualificado, mesmos pods baseline/gateway e fundação preservada.
A preparação registra também fontes, hashes dos manifestos e identidades CRI.

A baseline sintética e o gateway ficam instalados no laboratório após o gate;
a candidata é removida pelo rollback. O port-forward temporário é encerrado.
Imagens, arquivos de preparação e entrega original permanecem preservados.
O PVC retido do ensaio anterior de replay não é montado ou modificado.

Em falha inesperada após iniciar a atualização, o controlador tenta rollback para
a revisão baseline e confere a API diretamente; registra separadamente sucesso
ou erro dessa recuperação. O gate continua falho. Não remove recursos da fundação
ou a release do serving para ocultar a falha. `status` fornece estado/logs mesmo
com pods não prontos; o recibo de instalação/rehearsal contém diagnóstico.

Os pods não recebem token de ServiceAccount ou Secrets e usam usuário sem root,
raiz somente leitura, capacidades removidas e limites de recursos. Services são
privados. Não implementa NetworkPolicy, autenticação, TLS ou isolamento multiusuário;
este é um laboratório local. Não constitui deploy de produção, promoção da
referência, migração primária, publicação em registry ou recuperação por perda
do cluster/host. A associação futura com observabilidade terá aceite próprio.


## Aceite nativo — 2026-10-21

O autor aprovou os seis eventos do gate em 2026-10-21: baseline, bloqueio de
pacote inválido, candidata direta, troca na mesma URL, falha HTTP 502 e retorno
por `helm rollback`. A imagem qualificada da API foi reutilizada, sem rebuild
do runtime ou fit; somente o carrier foi construído. Os mesmos pesos sintéticos
produziram score `0.03463335444035967` antes/depois da troca e do rollback.
Baseline/gateway mantiveram seus pods e a fundação permaneceu igual; candidata
removida, baseline instalada no laboratório e port-forward temporário encerrado.

| Verificação | Resultado observado |
| --- | --- |
| Entrada confiável | Commit `0b95136a462583441044d50de0884a558777b260`; descritor `aa0e79886800765c516d3ed776295dc46d657a7ea50eb7c88900241d8c0ff0d7` |
| API qualificada | Docker ID `sha256:bc2ba70a90de114613bd8679e79ccfbb7a17de048f72f16323ca26713e1dae8d`, sem rebuild |
| Importação | Três arquivos `linux/amd64`, identidades CRI conferidas e arquivos temporários removidos; digests de importação locais não representam publicação em registry |
| Identidade blue → green | `c298299b7d3540d3968b5e715937b923` → `4953dc6c2a1047cd8fc16c575d81b49b` |
| Pesos preservados | Modelo SHA-256 `33c6fbab88b0876fef76eb754ebe7c130294e6267d736452a38851e7c4e31106` |
| Gates HTTP | Identidade e score iguais ao esperado na baseline, candidata direta, gateway e rollback; entradas inválidas retornaram 422 |
| Troca e falha | EndpointSlice pronto da candidata; zero endpoints prontos após interrompê-la; HTTP 502 observado |
| Rollback Helm | Revisão baseline 1 restaurada na revisão 5; blue ativa, candidata removida, mesmos pods e Services da baseline/gateway |
| Duração | Rollback observado em 1,99 s numa execução controlada; não é SLA ou demonstração de zero downtime |
| Teste | 1 passou em 19,48 s; tox OK em 34,53 s |

A URL `http://127.0.0.1:34297` existiu somente durante esse gate e não é um
endpoint permanente. Para novas inspeções, usar os comandos de operação acima.
Os Services privados e a baseline sintética ficaram instalados; fundação e
entrega original foram preservadas. Não houve promoção histórica, migração
primária, publicação em registry ou validação de recuperação por perda do host.

[Evidência completa](../../references/evidence/kind_serving_author_validation_2026-10-21.json),
com origem no terminal enviado pelo autor. O arquivo original do recibo não foi
fornecido em bytes; seu hash não foi inferido da formatação no terminal.
Próximo recorte: observabilidade no KinD; a pendência operacional de reboot
abaixo continua aberta.

## Importação de imagens com Docker/containerd — correção de 2026-10-21

O autor observou a falha `ctr: content digest ... not found` ao importar a imagem
do gateway. Esse padrão é documentado pelo KinD quando o image store do Docker
usa containerd e a exportação contém referências de plataformas indisponíveis.
O log é compatível com esse problema; não comprova corrupção do modelo ou que
uma reinicialização da máquina tenha sido sua causa.

O controlador passa a exportar cada imagem do serving com
`docker image save --platform linux/amd64`, importar o arquivo com
`kind load image-archive --name fraud-platform` e conferir tag/identidades CRI.
O tar temporário é removido em sucesso ou falha; hash, tamanho e plataforma
ficam no recibo quando a importação conclui. A imagem aprovada e o digest do
NGINX permanecem os mesmos. Erros de exportação/importação incluem stdout/stderr
limitados, sem ocultar o diagnóstico nativo.

Referências oficiais:

- [KinD: erro de importação e workaround por plataforma](https://kind.sigs.k8s.io/docs/user/known-issues/#unable-to-kind-load-docker-images).
- [Docker: exportar uma plataforma específica](https://docs.docker.com/reference/cli/docker/image/save/#save-a-specific-platform---platform).

Aplicar esta correção antes de concluir `prepare`; em seguida repetir `prepare`
com os pins confiáveis do exemplo acima e continuar `up`, `status` e o gate.
Os diretórios das tentativas que falharam ficam preservados. A correção não
edita recibos `prepared.json` concluídos nem adota preparações com fontes antigas.

## Pendência operacional: retomada após reinicialização

Após o aceite do serving, fechar um procedimento de parada/subida da stack e
retomada após reboot: Docker e node existente, API Kubernetes, contexto privado,
readiness das dependências e das aplicações, mesmas identidades e volumes.
Conferir também a persistência dos limites inotify ajustados no bootstrap; o
ajuste anterior por si só não comprova persistência após reboot. O procedimento
deve reutilizar o cluster e seus volumes, com diagnóstico de estado parcial.
Essa validação operacional fica registrada para o fechamento; não foi executada
ou declarada concluída por esta correção de importação.
