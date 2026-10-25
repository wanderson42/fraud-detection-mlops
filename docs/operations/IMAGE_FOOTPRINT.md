# Footprint das imagens da infraestrutura

## Estado da revisão em 2026-10-24

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
