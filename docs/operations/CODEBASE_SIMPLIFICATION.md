# Simplificação do código e dos comandos

## Primeira revisão — 2026-10-22

Base revisada: `740ca24bcbd17c57465ae9f654f9dbbfefd47d30` (main após o PR #10).
O objetivo é tornar a operação corrente fácil de localizar e retirar código que
já cumpriu sua função. Alterações são entregues em recortes verificáveis.

| Achado | Alteração |
| --- | --- |
| Makefile misturava operação, desenvolvimento e ciência em um arquivo longo | Raiz reduzida ao índice e ajuda; receitas existentes em `infra/make/development.mk` e `science.mk` |
| Ajuda mostrava treino, holdout e ensaios junto dos acessos diários | `make help` resume operação; `make help-all` conserva o catálogo completo |
| `platform-api` seguia o plano sintético e `round-api` seguia a primeira rodada | Um acesso operacional lê o deployment atual; `round-api` é alias; sintético tem comando explícito |
| Revisão de plano anterior à correção de leitura vazia já aplicada | Removidos o utilitário pontual e seus quatro cenários exclusivos; verificador real e negativos de integridade mantidos |
| Roteiro apresentava o Registry histórico como destino operacional | Roteiro passa a apresentar `fraud-detector`, seus Inputs e o champion ativo |

A redução de testes corresponde à retirada de uma capacidade transitória, não à
supressão de falhas para acelerar a CI. Os testes do acesso corrente cobrem outro
slot/versão, ausência de estado, divergência de pesos/Registry/rota, mudança de
identidade durante o acesso e encerramento do forward. A suíte de Make continua
verificando declaração de writers, argumentos com espaços, ordem e parada após
erro sob `-j`.

## Dependências que a revisão confirmou

`scripts/operational_round/controller.py` importa `scripts/storage/gold_archive.py`
e calcula pins sobre `scripts/storage`, o pacote científico e charts. O ciclo de
atualizações reutiliza a rodada, serving e transporte. Portanto, os diretórios
`storage`, `platform`, `tracking` e os ensaios Docker não podem ser classificados
como obsoletos apenas pela idade ou por existir KinD. Por exemplo, a qualificação
da entrega da imagem continua sendo executada pela CI antes de seu consumo.

Os módulos científicos e os pins de modelos/dados não são alterados neste
recorte. O acesso atual reutiliza os gates de `model_updates.deployment` e as
rotinas de forward existentes; não cria outro servidor ou controlador de deploy.
A primeira rodada permanece reproduzível pelo seu CLI e protocolo. O alias Make
`round-api` passa a significar acesso operacional, não seleção histórica por
`ROUND_ID`; essa mudança está explícita na ajuda e no guia.

## O que foi retirado

- `scripts/platform/kind_history_readback_fix.py`.
- O teste parametrizado de quatro cenários exclusivo dessa revisão de plano.
- A receita duplicada de acesso por `round-api` e as instruções correntes de uso
  do utilitário retirado.

O utilitário antigo não integra os pins dos planos históricos atuais. Sua
remoção não reescreve recibos, fontes, artefatos ou estado privado. Código e
procedimento continuam recuperáveis no Git anterior a este recorte. Evidências
datadas permanecem como registro do que foi executado; não viram instruções para
nova instalação.

## Verificação e aceite

Regressão local em Python 3.14.4: **186 testes aprovados, seis skips e 12
subtests**, em 59,25 s; Ruff e formatação aprovados. Foram conferidos Make,
transferência histórica, recuperação do serving, fluxo HTTP, atualizações, portal
e caminhos gerenciados. Os 167 pins da rodada, 187 das atualizações e 193 do
produto permaneceram idênticos aos blobs da main integrada.
[Evidência de preparação](../../references/evidence/operations_simplification_preparation_2026-10-22.json). A validação nativa do acesso é:

```bash
make help
make help-all
make platform-api
```

O último comando requer o cluster e o champion existentes saudáveis, imprime
versão/slot/pesos/release e verifica HTTP. Mantenha-o aberto e encerre com Ctrl+C.
Não execute um novo ensaio de falha para validar essa reorganização. O pacote não
inicia nem altera o cluster, não executa treino e não exclui dados locais.

## Continuação

O próximo recorte conecta produtor com ritmo, checkpoint, ledger, portal e
métricas da mesma sessão. A revisão mais ampla de duplicações deve usar esse
caminho operacional como referência e demonstrar que cada remoção deixou de ter
consumidores. A promoção de sucessor com novos pesos continua dependente de um
candidato que passe o gate; não é declarada concluída por esta simplificação.

## Aceite nativo do primeiro recorte

O autor executou 186 testes, seis skips e 12 subtests em 37,59 s. O acesso
`platform-api` abriu o champion versão 1, slot blue, pesos
`6dad9a78a5da0bda34cae6957b0ddbe391ecd848fbbc6604ae930bea3501370b`, release
`e8350e0a841f4136983e0d905068a743`; HTTP 422 conferido e p95 observado de
86,79 ms em 20 requisições. Ctrl+C foi executado.
[Evidência nativa](../../references/evidence/operations_simplification_author_validation_2026-10-22.json).
Esse tempo é a observação desse ensaio, não um SLA.

## Varredura do checkout

A revisão cobre **780 arquivos**, dos quais **358 Python** (67.725 linhas Python).
Foram cruzados imports AST, referências textuais, alvos Make, configuração de CI,
Dockerfiles/contextos, charts Helm, tox, catálogo, protocolos e células de notebooks.
O inventário completo identifica cada arquivo, hash, tamanho em linhas e decisão:
[evidência da revisão](../../references/evidence/repository_cleanup_review_2026-10-22.json).

A base é a main `740ca24` somada aos pacotes exatos aceitos na branch local
`feat/fraud-operations-demo` (commits `4664f6a` e `b1d11c2` informados pelo autor).
A branch ainda não estava publicada durante a revisão. `.vscode/settings.json`,
`mlruns/`, credenciais, logs e datasets privados não são candidatos a limpeza.
A cobertura é estrutural em todos os arquivos; a revisão semântica aprofundada
concentrou-se nos candidatos à remoção e nas fronteiras dos recibos. Ausência de
um import estático, isoladamente, não foi usada como autorização para apagar CLI.

| Área | Arquivos antes | Decisão |
| --- | ---: | --- |
| `scripts/` | 106 | Retirar importador histórico; manter operação, treinamento, transporte e recuperação usados |
| Pacote `fraud_detection_mlops/` | 94 | Preservar contratos, ciência, features causais, inferência e persistência vinculados aos pesos |
| `tests/` | 156 | Retirar teste exclusivo do importador e consolidar smokes de CLI; manter falhas e paridade |
| `infra/` | 137 | Manter build/qualificação/consumo e charts com consumidores declarados; KinD não elimina o gate Docker |
| `docs/` | 55 | Retirar procedimento corrente de reimportação; ajustar catálogo, testes e roteiro |
| `references/` | 188 | Remover três consumidores aposentados do catálogo corrente; preservar evidências e protocolos |
| `notebooks/` | 29 | Retirar entrada de execução do importador; manter capítulos e resultados científicos |
| Raiz / demais entradas | 15 | Manter ferramentas, lock e CI; `make help` continua a entrada curta |

### Remoções e consolidação executadas

- Retirados `scripts/platform/kind_history.py`, os cinco arquivos de
  `scripts/platform/history/` e `tests/infra/test_kind_history.py`: **sete arquivos
  e 1.923 linhas**, sem importador externo ao conjunto no código atual.
- Os testes `tests/data/test_entry_points.py` e
  `tests/modeling/test_entry_points.py` foram substituídos por
  `tests/interfaces/test_command_entrypoints.py`. Os **13 cenários** foram
  preservados: 12 CLIs e os diretórios padrão de Bronze/Silver/Gold.
- O catálogo corrente deixou de exigir os três consumidores do importador.
- O guia histórico foi reduzido a um registro arquivístico com link para a revisão
  anterior; a documentação de testes e o notebook não mandam executar código retirado.

Não há remoção de runs, modelos, aliases, buckets, datasets, checkpoints ou recibos.
A história da implementação continua no Git; o produto atual não precisa carregar
um utilitário de migração já concluída para funcionar. Nenhum novo serviço,
controlador ou biblioteca foi introduzido nesta limpeza.

### O que permanece e por quê

| Conjunto | Consumidor ou contrato identificado |
| --- | --- |
| `scripts/storage` | Gold e bundles usados por Jobs, rodada, atualização e recuperação; incluído nos pins da rodada |
| `scripts/tracking` / `infra/tracking` | Captura/restore PostgreSQL-S3 e laboratório de recuperação; capacidade distinta de importar histórico |
| `scripts/serving` / imagem Docker | A CI ainda constrói, qualifica e exporta a imagem, e outro runner consome o arquivo |
| Bootstrap diagnostics | Diagnóstico de falha de criação do node; CLI documentado, distinto da inspeção cotidiana |
| Replay e avaliação temporal antigos | Verificadores e paridade dos resultados aceitos; remover exigiria transição científica própria |
| Testes de erro, rollback, reinício e fraude causal | Exercitam contratos diferentes; não foram classificados como duplicados por serem numerosos |
| Evidências datadas | Registram execuções passadas; não são tratadas como instruções de nova operação |

### Consolidação do ciclo de vida canônico

A revisão encontrou o ciclo inicial em `fraud-serving-*`. O controlador existente
agora resolve o estado ativo `fraud-detector` e reutiliza o gate de
`local_access.connection("api")`, em vez de manter outro teste de serving sintético
como referência do produto. Não foi criado um segundo controlador ou serviço.
Uma única lista de dependências produz as ordens de subida e parada.

Cada controller presente tem suas réplicas capturadas e restauradas. O slot
inativo fica em zero; staging com dois slots vivos bloqueia preparar o baseline.
O laboratório anterior é opcional e só é incluído após conferir seu ownership;
nenhum chart, imagem ou dado é retirado automaticamente por este ciclo.

O baseline v2 registra a identidade do ponteiro ativo e do código dos gates. A
verificação é do modelo/release/Registry/HTTP atuais. A latência é validada contra
o orçamento, sem comparar tempos exatos. Os pins de atualização e produto não
mudam: a revisão não altera treino, feature contract, política de promoção,
instalador idempotente, pesos ou recibos científicos. Um baseline antigo não é
convertido; novo baseline é preparado por leitura dos serviços prontos.

O novo ciclo passou em 429 testes, com 12 skips e 12 subtests, em 104,35 s;
Ruff e formatação aprovados. O aceite nativo canônico permanece pendente.
Os aceites anteriores continuam com seu escopo original. O
[guia operacional](KIND_OPERATIONS.md) reúne os passos e o ensaio. Ainda não é
encerramento da simplificação ou da sessão contínua.

### Duplicações que ainda precisam de trabalho

A comparação de corpos AST encontrou poucas cópias exatas grandes; isso não
significa que não exista sobreposição de responsabilidades. Os pontos concretos
mais importantes são:

1. `main` e `request_for` repetidos nos dois módulos de treinamento.
2. Consulta CRI repetida entre rodada, replay KinD e entrega do serving.
3. Pins calculados sobre diretórios inteiros: acoplam limpeza de utilitários a
   recibos científicos aceitos. É preciso separar melhor identidade científica e
   implementação operacional, preservando a proveniência, antes de grandes movimentos.

Esses pontos **não foram declarados resolvidos**. Extrair um helper mudando hashes
sem uma transição revisada impediria retomar operações ou mascararia mudanças de
ciência. Métodos curtos de metadados com papéis diferentes também não justificam
uma abstração nova apenas para reduzir algumas linhas. A próxima intervenção de
simplificação deve tratar essas fronteiras; chegada contínua não substitui essa tarefa.

### Verificação

Os pins atuais de atualização e fluxo de produto foram comparados antes/depois e
permaneceram idênticos. A limpeza do catálogo não altera pesos, contratos de
features ou política. O pacote verifica hashes de todos os arquivos retirados e
valida cada dependência antes de escrever; edições divergentes bloqueiam o conjunto.
A suíte completa passou: **1.630 testes, 52 skips e 12 subtests**, em 914,57 s,
com 166 warnings; Ruff e formatação aprovados. O inventário de consumidores
conferiu 196 fontes e 29 consumidores declarados, com **zero inconsistências**,
sem abrir modelos ou consultar serviços externos. A matriz comum coletou os 13
cenários preservados. O aplicador também passou em testes de base integral,
reaplicação, recusa de edição/symlink e rollback que restaura arquivos removidos.

Balanço: **nove arquivos retirados e dois introduzidos** (teste comum e inventário),
redução líquida de sete arquivos. O Python caiu de 358 para 350 arquivos e perdeu
**1.946 linhas líquidas**, incluindo 1.360 linhas de implementação do importador.
Nenhum novo módulo de runtime foi adicionado. Os ensaios nativos existentes não
foram executados de novo apenas para retirar esse importador.
