# Rodada operacional canônica

Esta é a nova rodada oficial de modelagem e entrega do portfólio. Ela parte do
Bronze identificado, recompõe Silver/Gold em Jobs no KinD, treina três candidatos
com datasets registrados no MLflow, promove o vencedor aprovado e serve seus
pesos pela API. **Implementação preparada; aceite nativo pendente.**

O experimento principal será `fraud-operational-portfolio-v1`. O Registry da tarefa
é `fraud-detector`, com alias `champion`, independentemente do algoritmo vencedor.
Os modelos da construção anterior ficam arquivados e não entram nesta seleção.

## Política da rodada

A política executável é `references/operational_round_policy_v1.json`, ligada aos
contratos de dados e à configuração dos três candidatos. Ela deve estar commitada
antes de inicializar o ciclo. O comando confere o conteúdo dos arquivos contra
`HEAD`; alterações posteriores na implementação ou nos contratos impedem a
continuação com uma identidade antiga.

| Decisão | Regra |
| --- | --- |
| Candidatos | Dummy Prior, Regressão Logística com scaler e HGB |
| Elegíveis a champion | Regressão Logística e HGB; Dummy é controle obrigatório |
| Treino | 2018-04-01 a 2018-04-28, com feedback de sete dias |
| Seleção | Validação de 2018-05-06 a 2018-05-12; mesmos dados para todos |
| Gate de qualidade | AP ≥ 0,5, precisão diária por cliente no top 100 ≥ 0,45 e AP maior que Dummy |
| Vencedor | Maior AP entre elegíveis aprovados; empate pelo identificador do modelo |
| Gate de rastreabilidade | Inputs nativos de treino/validação, hashes, schema, partições, origem, código e ambiente conferidos |
| Gate do modelo | Recarga de toda a validação; paridade com tolerância absoluta/relativa de `1e-12` |
| Promoção | Versão registrada somente após verificação independente; leitura de volta do alias |
| Entrega | Os mesmos pesos na API, controle HTTP, HTTP 422 e blue/green com rollback |

Uma rodada em que ninguém atende aos critérios é rejeitada e conserva o relatório.
O sistema não baixa os limiares após olhar os resultados. A execução autorizada
visa produzir o primeiro champion desse novo ciclo, sem comparar com o HGB antigo.

As janelas são as do protocolo já conhecido: o resultado demonstra reprodução e
integração operacional. Não é uma estimativa independente de generalização.
O teste histórico não é usado para selecionar o vencedor e não há nova busca
Optuna ou treinamento contínuo neste protocolo.

## Preparar e executar

Use a branch de trabalho atual e o cluster `fraud-platform` já em operação,
com PostgreSQL, RustFS e MLflow prontos. As credenciais e os recibos de ownership
existentes continuam sob `data/storage/kind-platform/`. O Bronze publicado é
imutável e verificado; não é necessário baixar novamente o Handbook.

Primeiro, aplique o pacote, execute os testes e commite os arquivos da nova
implementação/política. O applier não faz Git add/commit/push. Preserve as
alterações alheias, inclusive o arquivo já staged em `.vscode`.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/operational_round \
  tests/storage/test_persistence_inventory.py \
  tests/storage/test_managed_paths.py

make round-validate
make round-init
```

`round-init` reserva a rodada `portfolio-v1`, o commit, os hashes do código,
a política e o destino. Ele exige que `fraud-detector` ainda não exista: este
protocolo define o primeiro champion canônico. Não cria um novo Registry para
contornar um nome ocupado. Repetir `round-init` para a mesma identidade reutiliza
o recibo existente.

Reexecute a cadeia de dados e prepare o treino:

```bash
make round-data &&
make round-build &&
make round-train
```

`round-data` usa os produtores e imagens de dados já aprovados para executar
novos Jobs Silver/Gold. O Gold consumido pelo treino é exatamente a saída dessa
execução, publicada no RustFS e restaurada/verificada separadamente. Bundles
iguais continuam endereçados por conteúdo; reutilização de objetos não substitui
execução dos produtores.

`round-build` constrói a imagem científica com versões e hashes do `poetry.lock`.
O Job recebe até **2 CPUs e 4 GiB**. Somente seu init container recebe credenciais
S3; o treinamento acessa o MLflow pelo proxy HTTP. O Job concluído é removido,
e os dados publicados, runs, modelos e recibos são preservados.

Suspenda outros escritores do Registry durante a promoção:

```bash
make round-promote ROUND_NO_OTHER_WRITERS=1
```

A declaração coordena a operação; o lock do checkout não bloqueia clientes
externos. O comando verifica os três candidatos, seus Inputs e os bytes de seus
artefatos, recalcula a qualidade na validação e confere a identidade dos dados.
Depois cria a versão do vencedor e atribui `models:/fraud-detector@champion`.
Uma repetição verifica e reutiliza a mesma versão.

Entregue o modelo e complete o aceite operacional:

```bash
make round-deploy &&
make round-rollout &&
make round-verify
```

O deploy constrói o runtime de inferência do código atual, qualifica os pesos do
vencedor por HTTP e instala a release Helm `fraud-detector`. A API usa os pesos
registrados da nova rodada, não uma fixture sintética. O ensaio green usa um
reempacotamento operacional desses mesmos pesos com outra identidade de release;
confere troca na mesma URL, falha 502 e retorno ao blue. Ao final, o green fica
com zero réplicas. Isso demonstra rollback, sem alegar zero downtime.

`round-verify` só publica `canonical_round_complete` após confirmar novamente
os datasets/modelos, o alias, o modelo servido e o recibo de blue/green.
Há uma API e um gateway ativos para essa rodada fora dos ensaios; o Job de treino
não fica executando. A release anterior de laboratório não é excluída por estes
comandos; sua desativação ocorre após o aceite da nova rota.

## O que mostrar no MLflow e na API

```bash
make platform-mlflow
# Em outro terminal:
make round-api
```

No experimento `fraud-operational-portfolio-v1`, cada candidato deve exibir:

- **Inputs:** datasets `training` e `validation`, digest, schema, target e perfil;
- **parâmetros:** configuração do pipeline e quantidade de linhas;
- **métricas:** resultados de validação associados ao dataset correspondente;
- **artefatos:** modelo sklearn/pyfunc em skops, dependências fixadas,
  `lineage.json` e `training-report.json`;
- **procedência:** hashes do Bronze, Silver e Gold, partições consumidas,
  conteúdo das linhas, política, código e lock de dependências.

No Registry, abra `fraud-detector` e o alias `champion`. A resposta da API deve
conter o hash dos mesmos pesos. O score é um ranking não calibrado; esse fluxo
não autoriza bloqueios de transações nem representa promoção em produção.

O monitoramento de rótulos atrasados e a jornada de eventos têm aceites próprios;
este ciclo não converte automaticamente os painéis de replay concluído em uma
nova ingestão ao vivo.

## Recibos e recuperação

O diretório é `data/storage/kind-platform/rounds/portfolio-v1/`. Os principais
recibos são `round.json`, `data.json`, `training-job.json`, `training.json`,
`promotion.json`, `serving.json`, `deployment.json`, `rollout.json` e
`verification-<uuid>.json`. A política de persistência cataloga esses destinos.

Após uma interrupção, use a mesma identidade e preserve os recibos. Dados e
modelos concluídos não devem ser apagados para tentar outra seleção. Se o Job
falhar antes de concluir, ele é preservado para diagnóstico; runs parciais não
recebem champion e não são silenciosamente duplicadas por uma nova tentativa.

Uma falha na construção/importação da imagem pode ser retomada após resolver a
causa; modelos já treinados não são reajustados. Uma mudança de código depois do
init exige revisão dos pins e um procedimento explícito de retomada. Não crie
outro `ROUND_ID` apenas para contornar uma recusa ou alterar os limiares.
