# Footprint das imagens da infraestrutura


## MLflow ativo otimizado; treino qualificado em ambiente isolado

No comparativo do commit `fd8ced7`, acrescentar somente `--no-compile` ao
`pip install` reduziu as duas imagens, mantendo as versões instaladas, Python,
plataforma, base e fontes de cada papel:

| Papel | Antes | Candidata | Redução |
| --- | --- | --- | --- |
| MLflow | 310.302.710 bytes | 246.885.278 bytes | 63.417.432 bytes — 20,44% |
| Treino | 313.902.517 bytes | 248.976.607 bytes | 64.925.910 bytes — 20,68% |

As contagens são de `docker image inspect Size`; não são espaço exclusivo
liberado no daemon. O bytecode lógico não equivale diretamente ao tamanho da
imagem e não deve ser somado às medidas RECORD. A diminuição de bytes em
caminhos de testes é compatível com não gerar seu bytecode; os fontes de testes
de terceiros não foram removidos. O pip, as bibliotecas científicas e seus
metadados permanecem. Bytecode herdado da base pode continuar presente.

A qualificação das candidatas executou um HGB sintético de 256 linhas pelo
contrato do projeto, registro com skops/allowlist existente, tracking e Registry,
proxy HTTP de artefatos S3, reinício do MLflow temporário e leitura por outro
cliente sem credenciais de banco/S3. Passaram metadados, duas observações de
métrica, alias `qualification`, bytes de artefato e paridade de recarga. A
conferência independente confirmou registros no PostgreSQL e bytes no RustFS.
A limpeza terminou sem erros. O banco, bucket e Registry eram temporários e
não alteraram o champion ou o histórico operacional. Não se inferem melhoria
de latência, economia de RAM ou novo resultado de qualidade a partir desse teste.

Os Dockerfiles de `infra/kind/tracking` e `infra/kind/round` incorporam a flag
para próximos builds. A candidata do MLflow foi aplicada ao tracking ativo pela
manutenção `tracking-bytecode-v1`; a imagem anterior e seus recibos continuam
preservados. O leitor do tracking aceita em memória apenas a transição
exata e commitada declarada em
`references/python_bytecode_source_transition_v1.json`; isso não transforma a
imagem antiga em otimizada. Dependências, contratos e fitters continuam fixados.
A declaração também permite consultar perfis operacionais já aceitos sem
reescrever fontes, recibos ou o baseline.

**Estado:** redução e qualificação isolada aceitas para ambas as imagens; troca
do MLflow ativo aplicada e verificada no KinD em 2026-10-27. As 65 tabelas de
metadados e os 711 objetos do bucket dedicado mantiveram seus fingerprints.
Registry, API, portal e evidências de desempenho passaram nas leituras funcionais,
sem mudança do champion. O runtime de treino será usado por próximos Jobs;
sua execução em nova rodada no KinD continua um aceite separado. Não há serviço
de treino permanente para reiniciar nem necessidade de repetir a rodada histórica.

O baseline operacional atual é
`data/storage/kind-platform/operations/baseline-tracking-bytecode-v1.json`.
O check confirmou a topologia e as leituras funcionais atuais. Não representa
novo aceite de stop/start ou reboot. Não foi executado rollback nativo desta
manutenção do tracking; a reversão continua disponível com aprovação própria.

[Aceite nativo do tracking](../../references/evidence/tracking_runtime_maintenance_author_validation_2026-10-27.json).

[Evidência consolidada](../../references/evidence/python_bytecode_author_validation_2026-10-27.json).


## Aceite atual: API otimizada implantada e verificada no KinD

A primeira redução da imagem da API está **medida, qualificada em Docker e
implantada no KinD**, na manutenção `api-trim-v2`, com os mesmos pesos e release
do champion. O baseline da época é
`data/storage/kind-platform/operations/baseline-api-trim-v2.json`, preservado como
histórico. O baseline atual inclui a manutenção posterior do tracking, descrita
na seção anterior.
O aceite da implantação e seus readbacks estão na
[evidência consolidada](../../references/evidence/runtime_maintenance_author_validation_2026-10-25.json).

| Medida | Baseline | Candidata | Redução |
| --- | --- | --- | --- |
| `docker image inspect Size` | 175.012.393 bytes | 170.839.706 bytes | 4.172.687 bytes, 2,38% |
| Arquivos lógicos em `site-packages` | 426.845.426 bytes | 414.901.631 bytes | 11.943.795 bytes, 2,80% |

As imagens comparadas são `fraud-api-footprint:baseline-v1` e
`fraud-api-footprint:candidate-v1`. Seus IDs completos, fontes e gates estão na
[evidência nativa](../../references/evidence/api_runtime_trim_author_validation_2026-10-25.json).
A comparação entre esses dois builds é a referência; não se calcula a economia
usando a imagem histórica anterior ou o tamanho total de `docker system df`.
O valor de `image inspect Size` não representa o espaço exclusivo liberável do
daemon. Nenhuma imagem, volume ou cache foi removido.

Somente pip desapareceu da lista de distribuições; as versões restantes foram
preservadas. Não foi detectado bytecode na venv candidata. O inventário atribui
uma diferença de bytes ao pacote do próprio projeto, compatível com a limpeza
de bytecode; contagem de bytes e versões não comprova identidade de todo fonte.
A qualificação funcional abaixo é uma verificação separada. Os caminhos de testes
de terceiros continuam com os mesmos 46.016.249 bytes. PyArrow e todas as
bibliotecas de inferência permanecem presentes.

Os dois ensaios HTTP reportaram sucesso, com o mesmo modelo
`6dad9a78a5da0bda34cae6957b0ddbe391ecd848fbbc6604ae930bea3501370b`,
release `e8350e0a841f4136983e0d905068a743` e manifesto
`fdef602970fbb5d4a4abeb510a074c8f943547d5f3d6c54f21f5a8600ca31400`.
O score da transação de controle 335910 foi idêntico: `0.4178630794049542`, com
tolerância de 1e-12. Também passaram startup/readiness, contrato health/info,
entrada inválida com HTTP 422, reinício, usuário 10001 e escrita bloqueada na
raiz. Manifesto incorreto e modelo corrompido ou ausente impediram a inicialização.
Os arquivos originais da release foram preservados.

O p95 observado foi 9,97 ms no baseline e 12,46 ms na candidata. Foram apenas
100 requisições sequenciais de controle por imagem após 10 de aquecimento.
Esse resultado não comprova melhoria ou regressão estatística, SLA ou capacidade
sob carga. As leituras de memória de 182,7 e 177,9 MiB são snapshots, não picos,
e não demonstram economia estável de RAM. Não houve nova avaliação global de
qualidade, treinamento, alteração do champion ou promoção de sucessor.

**Implantação aceita:** a API e os readbacks passaram e o baseline foi atualizado.
O rollback comprovado pertence à tentativa anterior `api-trim-v1`; não foi
executado rollback de `api-trim-v2`. Essa manutenção é independente da promoção
de um modelo com pesos diferentes, cujo aceite continua condicionado a um
candidato qualificado. Não se repete treinamento para otimizar uma imagem.

As seções seguintes conservam a revisão e o roteiro usados na preparação.


## Auditoria do snapshot a501f75, 2026-10-27

A revisão usa o archive do commit completo
`a501f755e03c36e27ca02bb7010f9cc0478dcc27`, incluindo as correções da branch
operacional. Lê fontes, requirements, locks e contextos; os tamanhos atuais dos
demais componentes ainda precisam de coleta no Docker do autor.

| Papel | Observação nas fontes atuais | Próxima decisão sustentada pela coleta |
| --- | --- | --- |
| API | Imagem menor já aplicada; PyArrow permanece no contrato | Não repetir esta redução nem quebrar contrato para diminuir bytes |
| Tracking MLflow | 93 distribuições fixadas, incluindo MLflow completo, scikit-learn, PyArrow e Matplotlib | Medir tamanho; avaliar uma composição de servidor que preserve PostgreSQL, S3 proxy, Registry e readbacks |
| Treino / atualização | 94 e 97 distribuições; as 94 do treino têm as mesmas versões na atualização; adicionais: Optuna, tqdm e colorlog | Medir bytes e camadas; avaliar camada comum sem unificar papéis nem forçar otimização de hiperparâmetros |
| Pipeline científico / replay | 15 e 20 distribuições com requisitos próprios | Conservar causalidade, features e integridade; priorizar pelo peso medido |
| Transporte / datasets | 7 distribuições do SDK S3, mesma base e instalação em vários Dockerfiles | Conferir reutilização efetiva de camadas antes de criar uma base comum |
| Exporter / receiver da sessão | Só biblioteca padrão e COPY de fontes, sem pip install | Baixa oportunidade de recorte de dependências; medir base e conteúdo |
| Carrier / gateway / bancos / Prometheus / Grafana / node | Imagens com papéis diferentes; terceiros fixados por digest | Inventariar antes de trocar variante; não usar tamanho do node como soma da aplicação |
| Portal | Ambiente no host, sem imagem Docker nesta entrega | Não misturar sua venv com a contabilidade de imagens |

Contagens de distribuições não são tamanhos nem prova de dependências inúteis.
Requirements iguais podem compartilhar camadas; tags distintas podem apontar
para o mesmo ID. A ausência de pods não torna uma imagem descartável: Jobs,
rollback e recibos antigos podem precisar dela. Não há prune neste recorte.
As allowlists já excluem dados e logs dos builds; algumas incluem mais fontes
ou referências que os COPYs exigem. Reduzir esse contexto pode diminuir envio
ao builder, mas não demonstra redução na imagem final.

### Coleta consolidada

```bash
poetry run python -m scripts.platform.image_inventory \
  --output data/reports/image-footprint/platform-v1
```

A coleta consulta o contexto `kind-fraud-platform`, namespace `fraud-platform`
e o node Docker `fraud-platform-control-plane`; verifica o label de cluster
antes de executar `crictl images` no node. Não cria contêiner de diagnóstico,
não instala ou baixa imagens, não acessa dados/PVCs e não altera a plataforma.
Usa o publicador de relatórios já existente e preserva diretórios anteriores.
Para repetir, escolha outro ID de saída.

O relatório agrupa aliases por ID no CRI e no Docker separadamente e vincula
imagens a pods e templates de Deployments/StatefulSets/DaemonSets/Jobs/CronJobs,
incluindo init containers e workloads com zero réplicas. Não inclui Secrets,
env, comandos de containers ou anotações no relatório. Inclui também imagens
locais `fraud-*`, referências dos locks e a imagem do node; outras imagens do
host ficam fora dessa lista. Os totais de `docker system df` são globais, em
texto formatado, incluindo outros projetos; não são economia atribuída à fraude.

Uma referência indisponível no Docker do host não significa ausência no CRI.
Imagens do CRI sem consumidor atual recebem classificação de retenção, nunca
autorização de exclusão. O coletor repete a projeção dos consumidores ao final;
se ela mudou, o relatório assinala essa condição e não representa uma topologia
estável. A coleta não trava deploys: use um período sem alterações da plataforma.
Os hashes dos arquivos de build identificam as fontes locais; `git_head` sozinho
não afirma que o checkout está limpo. A coleta não inspeciona dependências
instaladas; após selecionar um runtime Python, reutilize `image_footprint`
com seu ID e o interpretador correto (em geral `/usr/local/bin/python` para os
serviços Python em infra/kind; `/opt/runtime/bin/python` para a API).

Envie `data/reports/image-footprint/platform-v1/report.json` para priorizar o
próximo build comparativo. Não some Docker e CRI, nem some tamanhos das tags.
Nenhuma otimização adicional ou troca de imagem foi aceita por este inventário.

## Revisão de fontes em 2026-10-24 (histórico da preparação)

**Há otimizações implementadas, mas a otimização global de tamanho ainda não
foi medida e aceita.** Esta revisão leu Dockerfiles, allowlists de contexto,
requirements, locks e evidências já versionadas. Não executou Docker no host do
autor nem inspecionou as imagens atualmente carregadas em seu KinD.

O portal unificado teve aceite funcional e visual concluído. O próximo recorte
operacional será inventariar o footprint atual e escolher alterações com ganho
mensurável, conservando os gates de identidade e funcionalidade de cada papel.

| Papel | O que o código já faz | Medição que falta |
| --- | --- | --- |
| API de inferência | Dois estágios; Poetry no builder; ambiente final próprio com grupo `main`, base slim e contexto por allowlist | Tamanho e dependências da imagem realmente ativa; comparação controlada antes/depois |
| MLflow | Base Python slim por digest, requirements próprios com hashes e instalação sem cache pip | Tamanho e camadas exclusivas/compartilhadas do runtime atual |
| Jobs científicos / treino / atualização / replay | Requirements separados por papel, wheels, hashes, `pip check` em builds científicos e limites de threads | Peso das bibliotecas, sobreposição entre imagens e uso efetivo de cada runtime |
| Transporte e publicação de datasets | Subconjunto próprio de clientes de storage e scripts explícitos | Peso atual e possível compartilhamento de camadas entre utilitários |
| Exporter histórico e receiver da sessão | Biblioteca padrão do Python, base slim por digest e cópia explícita de código | Tamanho atual; não há dependências científicas para retirar desses receivers |
| Gateway, PostgreSQL, RustFS, Prometheus e Grafana | Imagens de terceiros fixadas por locks/digests e configuração de função | Tamanhos e impacto reais; não houve comparação de variantes deste conjunto |
| Node KinD | Imagem de node com Kubernetes/containerd | Medir separadamente: inclui infraestrutura e armazena imagens importadas no runtime interno |
| Portal Streamlit | Ambiente `.portal-venv` isolado e requirements com hashes no host | Não existe imagem Docker do portal nesta entrega; seu ambiente local é outra categoria de footprint |

Fixar digests assegura a identidade do artefato. Isso não demonstra, por si só,
redução de tamanho. Allowlists reduzem conteúdo enviado ao build; o conteúdo final
da imagem também depende dos comandos COPY/RUN e das dependências instaladas.

## Evidências históricas não são o tamanho atual de toda a plataforma

Há dois recibos nativos de API:

| Recibo | Tamanho reportado pelo engine | Interpretação |
| --- | --- | --- |
| `laboratory_serving_docker_native_validation_2026-10-09.json` | 174.861.582 bytes, aproximadamente 166,8 MiB | Imagem específica daquele ensaio de serving |
| `serving_delivery_author_validation_2026-10-19.json` | 175.002.534 bytes, aproximadamente 166,9 MiB | Imagem específica de uma entrega sintética qualificada |

Referências: [ensaio de serving](../../references/evidence/laboratory_serving_docker_native_validation_2026-10-09.json)
e [entrega qualificada](../../references/evidence/serving_delivery_author_validation_2026-10-19.json).
Esses valores não são confirmação do runtime ativo com o champion atual, nem do
total do cluster. Outro recibo de CI registra 545.914.111 bytes em um build de
runtime; sem base/versão/dependências e condições comuns não é uma comparação
controlada de redução. Não se calcula economia percentual entre esses registros.
Tamanho de arquivo exportado, tamanho reportado pelo engine e transferência
comprimida em registry também são medidas diferentes.

## Dependências da API: ponto concreto para examinar

`docker/serving/Dockerfile` instala `poetry sync --only main --no-root` no builder
e copia o ambiente resultante para o runtime. No estado atual, o grupo `main`
em `pyproject.toml` inclui NumPy, Pandas, PyArrow, scikit-learn, skops e o stack HTTP.
Logo, o build em estágios retira a ferramenta de build do runtime, mas não limita
a instalação a um conjunto HTTP mínimo.

Essas bibliotecas não podem ser simplesmente removidas: `model_release.py` lista
Pandas/PyArrow no contrato de versões do runtime; o contrato de modelos usa
Pandas e `features/feature_schema.py` importa PyArrow. Separar constantes de
features de componentes de dados pode ser uma hipótese de enxugamento, mas
precisa de revisão das importações, persistência e contrato da release, seguida
de gates. Esta revisão não altera dependências nem promete uma redução antes de
medir a imagem atual.

Nos Jobs, recortar dependências requer preservar as etapas científicas e a
publicação de artefatos. O runtime da API e o runtime de treino têm necessidades
diferentes. Um build em múltiplos estágios não reduz automaticamente uma imagem
que já contém apenas wheels e runtime necessários.

## Coleta inicial, somente leitura

Execute no checkout do autor, com o Docker acessível:

```bash
# Engine do host: imagens, camadas compartilhadas/exclusivas, volumes e cache.
# Inclui outros projetos que usam o mesmo Docker daemon.
docker system df -v

# Imagens carregadas no containerd do node específico do projeto de fraude.
docker exec fraud-platform-control-plane \
  crictl --runtime-endpoint unix:///run/containerd/containerd.sock images

# Vínculo entre pods e imagens, incluindo init containers, sem exportar env/secrets.
kubectl --kubeconfig "$PWD/data/storage/kind-platform/kubeconfig" \
  --context kind-fraud-platform --namespace fraud-platform \
  get pods -o 'custom-columns=POD:.metadata.name,PHASE:.status.phase,IMAGES:.spec.containers[*].image,INIT:.spec.initContainers[*].image'
```

O Docker do host e o containerd interno do KinD são inventários distintos.
Um nome de imagem pode aparecer com vários aliases; o ID/digest identifica o
artefato. A soma dos tamanhos de cada tag conta novamente camadas compartilhadas.
Use o detalhamento do engine para distinguir tamanho compartilhado e exclusivo;
não some diretamente seus números aos tamanhos reportados pelo CRI interno.
Dados em PVCs e camadas graváveis do node também devem ser distinguidos do
conteúdo das imagens. As saídas acima não alteram recursos ou removem imagens.

Referências oficiais:
[docker system df](https://docs.docker.com/reference/cli/docker/system/df/),
[docker image ls](https://docs.docker.com/reference/cli/docker/image/ls/) e
[build em estágios](https://docs.docker.com/build/building/multi-stage/).

## Critério de aceite de uma otimização posterior

1. Registrar imagem ativa por papel, ID/digest, plataforma e dependências; separar
   serviços permanentes, Jobs, carrier, node, cache e imagens de ensaios.
2. Medir com a mesma definição de tamanho e condições comparáveis. Não atribuir
   tamanho total do daemon exclusivamente a este projeto.
3. Escolher um alvo com ganho demonstrável. Só considerar uma imagem histórica
   removível depois de verificar consumidores, recuperação e recibos que a usam.
4. Requalificar função e identidade: paridade/contrato HTTP para API; metadados e
   artefatos para tracking; integridade/causalidade para Jobs; métricas/regras e
   proxy para observabilidade. A promoção do modelo continua sendo outro gate.
5. Registrar antes/depois e, se houver alteração de configuração implantada,
   emitir baseline correspondente, preservando o anterior.

O fechamento documental atual não muda Dockerfiles, locks, versões, imagens,
artefatos ou o baseline vigente. A decisão sobre mudanças virá dos tamanhos
atuais coletados e dos gates, não apenas da aparência de um Dockerfile enxuto.

## Medição por pacote antes de alterar a API

O inventário do autor, recebido em 2026-10-24, registra 757 MB para
`fraud-kind-serving-runtime:61439c973d934fa68b7f3e413f1ca394` no Docker do host e
175 MB no CRI do KinD. São contabilidades distintas, não uma redução entre builds.
O daemon também contém imagens e clusters do projeto de energia, contêineres
encerrados e 18,2 GB de cache de construção. Nenhum desses totais é atribuído
integralmente à plataforma de fraude. O tamanho atual desta API não deve ser
apresentado como 175 MB de ocupação completa no host.

A coleta abaixo identifica as distribuições Python e os diretórios que mais
ocupam espaço, preservando Pandas/PyArrow e os contratos da release. Este recorte
instrumenta a decisão de otimização; ainda não altera Dockerfiles/dependências
nem demonstra redução. Não repete treinamento ou promoção.

```bash
poetry run python -m scripts.serving.image_footprint \
  --image fraud-kind-serving-runtime:61439c973d934fa68b7f3e413f1ca394 \
  --output data/reports/image-footprint/api-before-v1
```

A referência vem do inventário aceito. O relatório não afirma que essa imagem
está ativa no gateway: seu vínculo com o deployment precisa ser confirmado antes
de uma implantação posterior. O coletor resolve a referência para o ID local e
executa o diagnóstico nesse ID, mesmo se a tag mudar durante a coleta.

O diagnóstico inicia um contêiner temporário: sem rede, volumes do projeto,
capabilities ou escrita na raiz; com usuário não privilegiado e limites de CPU,
memória e processos. Não inicia a API nem importa bibliotecas de ML. Não baixa
imagens, altera deployments, acessa o modelo ou remove imagens/cache/volumes.
O contêiner é removido ao terminar; uma interrupção tratada tenta remover somente
o contêiner de diagnóstico identificado por um nome aleatório próprio. Uma
interrupção abrupta do processo/daemon pode exigir conferir esse nome.

A saída fica em diretório novo, com `report.json` e `report.sha256`. O diretório
já existente não é sobrescrito; se precisar repetir, use outro sufixo. Esses
arquivos ficam em `/data/`, já ignorado no Git. O coletor não salva variáveis de
ambiente, comando original das camadas ou configuração do contêiner da imagem.

Para enviar o resultado da coleta:

```bash
python3 -m json.tool data/reports/image-footprint/api-before-v1/report.json
```

Interpretação dos campos:

| Campo | Significado |
| --- | --- |
| `image.Size` | Bytes reportados por `docker image inspect`, associados ao ID e plataforma |
| `rootfs_diff_ids` | Identidades das camadas, não seus tamanhos exclusivos |
| `history` | Tamanho formatado de cada entrada do histórico; não somar com os pacotes |
| `python_inventory.site_packages` | Bytes dos arquivos regulares legíveis em site-packages, incluindo bytecode |
| `top_level_directories` | Visão por diretório: também evidencia bibliotecas nativas em pastas `.libs` |
| `distributions` | Nome, versão e bytes atribuídos pelos arquivos RECORD; referências externas são excluídas |

Bytes lógicos, blocos alocados, diretórios e RECORD são visões distintas do mesmo
conteúdo. Não devem ser somados entre si, ao CRI ou ao tamanho total da imagem.
RECORD pode omitir arquivos gerados ou compartilhar referências com outra
distribuição. Pastas `test/tests` e bytecode são apenas candidatos para análise;
a sua presença não autoriza apagá-los. Conteúdo da base fora de site-packages
não entra na medição por pacote. O diagnóstico não mede RAM do serviço,
latência, vulnerabilidades, dependências efetivamente usadas ou espaço liberável.

A implementação usa somente biblioteca padrão. Pode ser aplicada aos outros
runtimes Python informando `--python` com o interpretador existente na imagem;
o valor padrão `/opt/runtime/bin/python` corresponde à API atual. Compare sempre
a mesma plataforma e a mesma definição de tamanho. Requalificar a imagem
resultante com os mesmos pesos e contrato HTTP continua obrigatório.

## Primeira redução conservadora: instalador e bytecode

O relatório do autor identifica a imagem
`sha256:570db485edf4c98ce19852f7cf42a76a943faafaef25eab13d8df5101a6c6cda`,
com `image inspect Size = 175014717`. Dentro do ambiente, `site-packages` contém
426841007 bytes lógicos, 6439247 bytes de bytecode e 46016249 bytes em caminhos
de testes. O RECORD de pip atribui 11152395 bytes à distribuição. Esses valores
podem se sobrepor e não podem ser somados para prometer redução comprimida.

A alteração no Dockerfile faz `pip check`, desinstala somente o pip da venv e
remove `__pycache__` na etapa **builder**, antes do COPY para a imagem final.
Apagar depois do COPY deixaria os bytes na camada anterior. A configuração
`PYTHONDONTWRITEBYTECODE=1` continua ativa no runtime. O pip da base Python, fora
da venv, não é removido: está em uma camada herdada, e sua remoção posterior não
eliminaria os bytes dessa camada. A API é um runtime imutável; instalação de
pacotes continua sendo responsabilidade do build.

Não são removidos testes de terceiros, bibliotecas nativas, arquivos `.dist-info`
de inferência ou dependências científicas. NumPy, Pandas, SciPy, scikit-learn,
skops e PyArrow conservam suas versões e os contratos da release. A limpeza
não acrescenta uma nova dependência ou serviço.

PyArrow permanece como oportunidade maior: seu RECORD tem 157189725 bytes,
mas `feature_schema.py` o importa e o contrato de versões da release o exige.
Retirá-lo requer um recorte próprio de contratos e compatibilidade, com
paridade dos pesos existentes. Não se falsifica sua presença mantendo apenas
metadados e apagando sua implementação.

### Comparação controlada no host

O pacote fornece `Dockerfile.before`, cópia exata do arquivo anterior. Faça os
builds no mesmo checkout revisado, sem editar fontes/lock entre eles, usando o
mesmo digest e plataforma. Os nomes abaixo são novos e não substituem a imagem
ativa. Se esses nomes já tiverem sido usados, escolha outro sufixo para ambos.

```bash
api_size_base=$(python3 -c \
  'import json; print(json.load(open("infra/serving/images.lock.json"))["build_python"])') &&

docker build --platform linux/amd64 \
  --build-arg PYTHON_IMAGE="$api_size_base" \
  --file /tmp/fraud-api-runtime-trim/Dockerfile.before \
  --tag fraud-api-footprint:baseline-v1 . &&

docker build --platform linux/amd64 \
  --build-arg PYTHON_IMAGE="$api_size_base" \
  --file docker/serving/Dockerfile \
  --tag fraud-api-footprint:candidate-v1 . &&

poetry run python -m scripts.serving.image_footprint \
  --image fraud-api-footprint:baseline-v1 \
  --output data/reports/image-footprint/api-controlled-before-v1 &&

poetry run python -m scripts.serving.image_footprint \
  --image fraud-api-footprint:candidate-v1 \
  --output data/reports/image-footprint/api-controlled-after-v1
```

O pacote também fornece `measure.sh`, que executa a sequência acima, recusa
sobrescrever tags/relatórios existentes e verifica o checkout antes/depois:

```bash
bash /tmp/fraud-api-runtime-trim/measure.sh
```

Para uma coleta nova, use `API_SIZE_RUN_ID=v2 bash /tmp/fraud-api-runtime-trim/measure.sh`.
Mantenha o checkout sem alterações durante os builds; o script não cria uma cópia
isolada das fontes. Se um build falhar, preserve o diagnóstico e use um ID novo
para repetir a comparação completa.

Envie os dois `report.json`. O recibo original `api-before-v1` é preservado,
mas não substitui o baseline reconstruído com as fontes atuais. Devem ser iguais
plataforma, Python e versões das distribuições de inferência; o candidato deve
estar sem a distribuição pip e sem bytecode na venv. Compare bytes de
site-packages com bytes de site-packages e `image.Size` com `image.Size`; não use
`system df -v` como se fosse o mesmo indicador. Uma economia em disco do daemon
não é obtida pela simples soma dos tamanhos de tags.

**Estado após o ensaio do autor:** builds e comparação antes/depois concluídos,
com qualificação HTTP em Docker dos mesmos pesos. Os detalhes aceitos estão no
início deste documento. O roteiro acima continua disponível para reprodução,
usando novas tags e diretórios para preservar recibos anteriores. A implantação
no KinD e seu baseline foram aceitos posteriormente, conforme a seção inicial.


## Implantação da imagem qualificada

O fluxo de manutenção preparado em 2026-10-25 reutiliza a cadeia de recibos e o
Helm canônico: [SERVING_RECOVERY.md](SERVING_RECOVERY.md#manutenção-do-runtime-da-api-com-os-mesmos-pesos).
A preparação usa o recibo Docker e um pod isolado com os mesmos pesos. Apply e
rollback têm autorizações distintas, retomada e gates; não alteram o champion.
A imagem anterior permanece disponível. A aplicação `api-trim-v2` e os readbacks
foram aceitos no KinD. A reversão verificada é a de `api-trim-v1`, após apply
falho; os dois resultados permanecem distintos no recibo consolidado.


## Preparação da manutenção do MLflow ativo

O pacote de manutenção temporário usa a imagem já qualificada, sem rebuild.
O preparo faz o check do baseline atual, carrega a candidata no node pelo loader
single-platform existente, renderiza o chart com tag `fraud-kind-tracking:<32 hex>`
e registra os valores anteriores e a identidade do Deployment. Não troca o
Deployment no preparo. O digest de aprovação vincula fontes, ferramentas,
baseline, qualificação e fingerprints dos dados observados.

A leitura dos metadados usa uma transação PostgreSQL somente leitura com
snapshot repetível; os recibos contêm contagens e hashes por tabela, não linhas.
A leitura S3 percorre todos os objetos do bucket dedicado do MLflow, em blocos,
sem carregar o modelo ou modificar objetos. A manutenção exige ausência de
outros writers durante preparo/aplicação/verificação. Os demais buckets e PVCs
não são alterados. Estes fingerprints são uma verificação de preservação,
**não um backup** ou ensaio de restore.

A troca mantém o chart, configuração e Secret; só a imagem muda. O chart usa
Recreate, portanto haverá possível indisponibilidade breve do MLflow. A API
continua com os mesmos pesos locais e não recebe nova implantação. Após readiness
e identidade CRI, o fluxo publica atomicamente o recibo da imagem de tracking e
compara metadados, objetos e readbacks funcionais. Falhas preservam o estado e os
recibos; rollback tem aprovação distinta. O recibo original da imagem é preservado
byte a byte em `original-image.json`. O baseline anterior não é sobrescrito;
um novo baseline foi criado após o aceite da aplicação, sem sobrescrever o anterior.

As ferramentas ficam também na pasta privada da manutenção, em `tool/`, com
hashes vinculados ao preparo, para não depender da permanência de `/tmp`.
Uma etapa interrompida pode ser retomada com o mesmo ID enquanto seus vínculos
forem válidos. Uma manutenção revertida não é reaberta para novo apply.

A preparação corrige ainda o leitor de relatórios usados para novos baselines:
ele reconhece a transição exata já commitada do controlador de tracking,
conservando a identidade do cliente e o relatório histórico. Não exige refazer
um treino ou ensaio antigo apenas porque o leitor operacional mudou.

[Preparação e limites de validação](../../references/evidence/tracking_runtime_maintenance_preparation_2026-10-27.json).


## Encerramento deste recorte de otimização

O recorte está concluído: a API otimizada e o MLflow otimizado estão ativos no
KinD, e o build de treino foi reduzido e qualificado em ambiente isolado.
Otimizações adicionais dependem de um gargalo medido; não são requisito para
continuar a entrega do produto. Não foram removidos volumes, caches ou imagens,
nem se atribui a soma dos tamanhos das tags a espaço exclusivo recuperado.

A redução de tamanho não comprova melhoria de latência ou memória. Este aceite
não promove um novo modelo: implantação e rollback de um sucessor com pesos
diferentes continuam condicionados a um candidato qualificado e à aprovação
prevista na política de modelos.
