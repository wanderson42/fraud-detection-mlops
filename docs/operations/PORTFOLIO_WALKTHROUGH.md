# Guia de execução e demonstração do portfólio

O caminho principal para o novo ciclo é a [rodada operacional canônica](OPERATIONAL_ROUND.md):
`make round-init`, dados, treino com Inputs, promoção e API. O aceite nativo dessa
rodada está pendente; as seções seguintes conservam os recortes anteriores.

Este guia organiza a experiência de quem vai operar ou avaliar o projeto. O
`Makefile` passa a oferecer entradas curtas, delegando ownership, locks, hashes,
Helm e recibos aos controladores que já tiveram seus próprios aceites.
O autor abriu API, MLflow e Grafana pelos novos atalhos. A validação nativa de
infraestrutura permanece preservada; não é necessário repeti-la para ler o guia.

## O cenário de fraude que estamos representando

Uma transação chega, o sistema consulta somente o passado disponível para formar
features e calcula um score de risco. A política usa esse ranking para priorizar
uma fila limitada de investigação por cliente/dia. O rótulo de fraude só fica
conhecido depois: feedback atrasado não pode vazar para a decisão anterior.
Na operação, acompanhamos disponibilidade e latência, continuidade do estado,
qualidade/tempo das features e mudanças de distribuição; qualidade preditiva
exige rótulos maduros e uma população de avaliação bem definida.

Os dados do Handbook são simulados. O score de ranking não é uma probabilidade
calibrada nem uma autorização de bloqueio de pagamento. A capacidade de recriar
pods e retomar o laboratório comprova aspectos operacionais; ainda não mede
escalabilidade sob carga, alta disponibilidade ou eficácia contra fraude real.

## O que está conectado hoje

| Ligação | Estado atual e consequência para a apresentação |
| --- | --- |
| Bronze publicado → Jobs Silver → Jobs Gold | Cadeia executada no KinD com a saída Silver identificada consumida pelo Job Gold; novas saídas S3 e restores verificados |
| Gold histórico → treino/experimentos → referência congelada | Fluxo offline com evidências preservadas; os comandos de demonstração não voltam a treinar nem consumir reservas de avaliação |
| Eventos → features causais → score → política → feedback/checkpoint | Replay nativo já exercitado; a execução programada usa scorer local e guarda estado/telemetria. O gate KinD de recuperação usa outra fixture sintética pequena |
| Entrega qualificada → API → gateway → troca/rollback | Serving KinD instalado com pesos sintéticos qualificados, independentes do novo Gold e do replay histórico |
| Replay nativo concluído → cópia somente de leitura → exporter → Prometheus → Grafana | Monitoramento aprovado de estado materializado; os painéis não representam uma nova ingestão ao vivo |

**Existe uma sequência de componentes demonstráveis, não um único fluxo novo de
transações chamando a API por HTTP.** O Make encurta a operação, mas não cria essa
ligação de dados. Não apresente o novo Gold como entrada do modelo sintético da
API, nem o replay sintético de recuperação como o replay nativo de 556.824 eventos.
As identidades e os resultados desses recortes são diferentes por desenho.

## 1. Apresentar o ambiente existente — caminho curto

Na raiz do repositório, com dependências e cluster já preparados:

```bash
make help
# Catálogo completo de provisionamento, ciência e ensaios:
make help-all
make platform-status
```

`platform-status` é diagnóstico de node/recursos, inclusive se o node estiver
parado. A saída não substitui um gate HTTP. Se o node estiver parado, retome pelo
baseline de operação que você salvou, conforme a seção 4; não use `create`.

Abra três terminais no repositório:

```bash
# Terminal 1: champion ativo, pelo gateway; confere Registry, pesos e HTTP 422.
make platform-api
```

```bash
# Terminal 2: MLflow no KinD; imprime uma URL loopback temporária.
make platform-mlflow
```

```bash
# Terminal 3: Grafana; mantenha esse processo aberto.
make platform-grafana
```

Para Grafana, o usuário é `admin`; a senha permanece no arquivo privado
`data/storage/kind-platform/observability/credentials.json`. Não publique esse
arquivo, a senha ou o kubeconfig. Use a URL informada por cada comando; MLflow e
API usam portas efêmeras. Se a porta Grafana estiver ocupada, escolha outra:

```bash
make platform-grafana PLATFORM_GRAFANA_PORT=13011
```

Ctrl+C encerra somente o port-forward daquele terminal. Não há `pkill` global,
supervisor solto em background ou troca do contexto global do kubectl. A cópia
nativa/Compose e os clusters do projeto de energia não são alvos desses comandos.
Se um pod for substituído e o acesso cair, feche e abra novamente o comando.

### Roteiro para um avaliador

1. Mostre a origem/contratos e os recibos Bronze–Silver–Gold. Explique que features
   usam apenas o passado e que treino/validação seguem janelas temporais.
2. No MLflow, mostre a rodada `fraud-operational-portfolio-v1`: os candidatos,
   seus Inputs de dataset, parâmetros e métricas. Em Models, confira
   `fraud-detector` e a versão ativa em `champion`. A comparação histórica
   importada e os experimentos sintéticos permanecem identificados à parte.
3. Na API, mostre a release/hash e o score do smoke impresso por `platform-api`,
   incluindo rejeição HTTP 422. O serviço executa inferência; não treina a cada
   requisição. A URL também permite consultar `/ready` e `/info`.
4. No Grafana, percorra visão geral, operação do replay, dados/features e
   qualidade da triagem. Explique a origem concluída do estado, atraso de rótulos,
   cores semânticas e por que drift isolado não dispara treinamento.
5. Mostre os recibos de rollback e reboot. Só execute novas falhas se a sessão
   tiver esse objetivo e uma janela de indisponibilidade prevista.

O resultado para negócio é uma fila auditável de investigação. Não apresente as
contagens como perdas financeiras evitadas ou como decisões de bloqueio.

## 2. Reproduzir os dados desde Bronze

Essa jornada gera/verifica dados e leva mais tempo que a apresentação do ambiente
pronto. Não é uma ação de startup diário. Para um checkout novo, configure Python,
Poetry e dependências antes; para infraestrutura nova, siga
[KIND_PLATFORM.md](KIND_PLATFORM.md) e os pré-requisitos da fundação.

```bash
make data
make verify-data
make platform-foundation
make platform-tracking
```

`make data` usa a origem congelada configurada no projeto. Ele não busca um feed
bancário real. A fundação executa `init`, verifica inotify e cria **somente** o
cluster dedicado; recusa adoção/substituição de outro ambiente.

Prepare o bundle Bronze local e anote o `bundle_id` retornado:

```bash
poetry run python -m scripts.storage.bronze_archive prepare
poetry run python -m scripts.platform.kind_datasets build
poetry run python -m scripts.platform.kind_datasets inventory
```

Publique explicitamente o identificador revisado, substituindo o valor abaixo:

```bash
poetry run python -m scripts.platform.kind_datasets publish \
  --layer bronze --bundle-id SEU_BUNDLE_BRONZE
make platform-data-jobs
```

O controlador dos Jobs exige um único bundle Bronze preparado no inventário;
ambiguidade é um erro a resolver antes de rodar. Silver publica sua nova saída,
Gold consome exatamente essa saída e os dois restores são verificados. Esse
caminho recalcula features, **não treina modelos**. As evidências ficam em
`data/storage/kind-platform/pipeline/` e nos recibos associados do transporte.

`make platform-datasets` é um ensaio diferente: publica/reutiliza/restaura os
quatro bundles previamente preparados (Bronze, Silver, Gold e desenvolvimento).
Exige exatamente um de cada camada. Ele não é pré-requisito para todo startup e
não cria os datasets locais ausentes. Preparos específicos estão em
[STORAGE.md](STORAGE.md) e [KIND_DATASETS.md](KIND_DATASETS.md).

## 3. Entrega, monitoramento e ensaios opcionais

### Serving canônico

O ponto de entrada é a [rodada rastreada](OPERATIONAL_ROUND.md): dados, candidatos,
qualificação, Registry e primeira implantação em `fraud-detector`. Atualizações
posteriores seguem [MODEL_UPDATES.md](MODEL_UPDATES.md). No ambiente já aceito,
use `make platform-api` ou `make access-up`; eles não refazem treino/instalação.
A antiga release sintética foi retirada. Qualificação e consumo de imagem na CI
continuam sendo gates independentes da promoção do modelo.

### Instalar monitoramento de replay concluído

O estado deve estar concluído, identificado e sem writers. Para a preparação
inicial — não para cada abertura de dashboard:

```bash
make platform-monitoring-prepare \
  PLATFORM_STATE=data/replay/paced-native-v1 \
  PLATFORM_NO_OTHER_WRITERS=1
make platform-monitoring-up
```

Isso copia e verifica o estado concluído. Não inicia um replay novo. Se o estado
nativo não existir em outro computador, é necessário reproduzi-lo pelo seu
protocolo ou usar uma fixture explicitamente identificada. Não se substituem
arquivos históricos silenciosamente por dados sintéticos. Essa dependência de
artefatos é uma fronteira a resolver na simplificação do portfólio.

### Exercitar rollback e recuperação

| Comando | Efeito e custo operacional |
| --- | --- |
| `make platform-replay-test` | Novo replay sintético pequeno, fit da fixture, interrupção durante feedback e retomada no mesmo PVC novo; PVC retido como evidência |
| `make platform-operations-test PLATFORM_TRACKING_REPORT=CAMINHO_DO_RECIBO PLATFORM_NO_OTHER_WRITERS=1` | Novo baseline, parada/subida do laboratório e leituras funcionais; indisponibilidade temporária, sem reboot automático |

São ensaios explícitos, separados da abertura de serviços. Não repita o replay
nativo completo, experimentos, holdout ou otimização apenas para apresentar a
infraestrutura. Quando necessário, os gates originais continuam disponíveis por
CLI e tox, com os escopos já documentados.

## 4. Encerrar e voltar a operar

Feche os port-forwards e finalize writers. Prepare um baseline novo, sem
sobrescrever os aceitos anteriormente:

```bash
make platform-baseline \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-sessao-01.json \
  PLATFORM_TRACKING_REPORT=data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json

make platform-stop \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-sessao-01.json \
  PLATFORM_NO_OTHER_WRITERS=1
```

Esse recibo de tracking é o aceito no ambiente do autor. Em outro computador,
use o recibo obtido na preparação do seu próprio tracking.

Para voltar, use **o mesmo baseline**, Docker ativo e valores inotify suficientes:

```bash
make platform-start \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-sessao-01.json \
  PLATFORM_NO_OTHER_WRITERS=1
make platform-verify \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-sessao-01.json
```

Após reboot real precedido da parada planejada, use `platform-reboot-verify` no
lugar de `platform-verify`. Nenhum alvo reinicia a máquina. Não apague cluster,
PVCs, arquivos privados ou recibos para tentar corrigir uma retomada.
[KIND_OPERATIONS.md](KIND_OPERATIONS.md) descreve os requisitos completos.

## Próximo recorte funcional

Primeiro, conclua a [transferência histórica e o Registry](KIND_HISTORY.md).
A atribuição de `champion` não substitui a release sintética da API. A ativação
da referência histórica exige seu próprio gate de identidade e rollback.

Para demonstrar uma jornada nova de ponta a ponta, falta vincular explicitamente
os eventos, o cálculo causal das features, uma release identificada, o cliente
HTTP e a política/checkpoint/feedback. A ligação precisa definir tratamento de
retries/reentregas, resposta inválida e troca de release durante a sessão, além
de provar a identidade do modelo usado em cada decisão. Isso merece um gate
próprio e orçamento limitado, não uma sequência de comandos que pareça conectada.

O roteiro atual permite apresentar o que já foi aceito e executar cada recorte.
A simplificação deve escolher um caminho canônico de demonstração, resolver a
distribuição das fixtures/artefatos necessários e reduzir instruções duplicadas.
Broker, feature store, CT e novo orquestrador não são requisitos automáticos para
essa conexão. Terraform permanece após estabilizar o fluxo escolhido.
