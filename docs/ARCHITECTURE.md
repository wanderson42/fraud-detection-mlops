# Organização e dependências

A estrutura acompanha responsabilidades existentes. O pacote permanece na raiz
do checkout. O serving tem runtime e recursos operacionais explícitos;
infraestrutura entra por capacidade implementada.

## Modelagem

| Local | Responsabilidade e fronteira |
| --- | --- |
| `modeling/contracts/model_interface.py` | `ModelSpec`, construção, fit e score com checks de compatibilidade |
| `modeling/contracts/prediction_schema.py` | Tipos de metadados e scores persistidos |
| `modeling/contracts/temporal_assessment_protocol.py` | Snapshot do protocolo estatístico e vínculos com recibos históricos; lê somente JSON versionado |
| `modeling/algorithms/<algoritmo>.py` | Factory completa e não ajustada; sem política temporal ou publicação |
| `modeling/algorithms/model_catalog.py` | Registro explícito e parâmetros padrão dos algoritmos |
| `modeling/experiments/candidate_training.py` | Entrada compartilhada de ajuste, métricas e artefatos |
| `modeling/experiments/baseline_policy.py`, `baseline_experiment.py` | Política fixa, verificação e execução da baseline |
| `modeling/experiments/temporal_development_data.py`, `hgb_optimization.py` | Preparação autorizada e busca de hiperparâmetros HGB |
| `modeling/experiments/terminal_feature_ablation.py` | Ablações específicas das features de terminal |
| `modeling/experiments/candidate_freeze.py`, `final_holdout_evaluation.py` | Congelamento e avaliação final autorizada |
| `modeling/experiments/reference_temporal_assessment.py` | Preparação do plano temporal da referência; somente metadados, sem execução reservada |
| `modeling/experiments/reference_assessment_execution.py` | Execução autorizada da janela fixada, auditoria de acesso, retomada e verificação offline |
| `modeling/experiments/experiment_provenance.py`, `experiment_artifacts.py` | Identidade de implementação e integridade compartilhadas |
| `evaluation/` | Ranking, comparação pareada, diagnósticos e referência condicional de fila aleatória sobre contagens diárias |
| `integrations/` | Tracking MLflow e persistência skops |

Dependências seguem essas fronteiras: algoritmos dependem do contrato; integrações
validam o contrato; métricas recebem rótulos, scores e metadados; experimentos
compõem essas capacidades e controlam datas, orçamento e gates. Algoritmos e
contratos não importam executores. O treino compartilhado não depende da política
da baseline. A avaliação final permanece em experimentos por consumir um holdout
sob uma autorização específica.

Uma contribuição nova entra por
[CONTRIBUTING_MODELS](modeling/CONTRIBUTING_MODELS.md). O catálogo não amplia as
políticas congeladas. Nomes de modelos, parâmetros nativos, classes, features,
schemas e formato skops permanecem iguais nesta mudança estrutural.

## Entradas e proveniência

Os adaptadores antigos de `modeling/` foram retirados após a migração do código,
exemplos e notebooks para os módulos explícitos da tabela. `modeling.experiments`
agrupa os executores; cada comando identifica seu módulo, incluindo
`modeling.experiments.terminal_feature_ablation`. O contrato `model_interface_v1`
permanece em `modeling/contracts/model_interface.py` com as mesmas regras.

Scripts externos devem usar os caminhos atuais. Os
[comandos e regras de migração](operations/OPERATIONS.md#migração-da-organização-da-modelagem)
mantêm o mapa das entradas aposentadas e distinguem leitura histórica de novas
execuções. Recibos históricos conservam os comandos efetivamente executados.

O inventário de código é recursivo, incluindo contratos, algoritmos, avaliação e
integrações. Novos ajustes exigem código e políticas commitados. Uma refatoração
muda a identidade da implementação; não reescrevemos manifestos, protocolos nem
recibos históricos para permitir retomada com código diferente.

## Dados e features

| Local | Responsabilidade e fronteira |
| --- | --- |
| `data/contracts/transaction_schema.py`, `silver_contract.py` | Tipos físicos, validação de inteiros e aceitação da Bronze |
| `data/contracts/gold_contract.py`, `temporal_protocol.py` | Regras da Gold e autorização das janelas temporais |
| `data/contracts/dataset_errors.py` | Erros compartilhados, sem dependência dos builders |
| `data/ingestion/handbook_inventory.py` | Identidade da fonte e seleção das partições |
| `data/ingestion/handbook_bronze.py`, `handbook_download.py` | Aquisição, integridade, retomada e CLI da Bronze |
| `data/datasets/silver_dataset.py`, `gold_dataset.py` | Construção, publicação atômica, verificação e consulta |
| `data/datasets/reference_assessment_dataset.py` | Contexto Silver restrito e features causais da avaliação de setembro; sem carga ou ajuste de modelo |
| `data/quality/transaction_profile.py`, `training_eda.py` | Diagnóstico dos dados brutos e exploração restrita ao treino |
| `features/feature_schema.py` | Ordem, tipos e schema dos preditores e metadados |
| `features/causal_history.py` | Fórmulas e janelas SQL sobre o passado elegível |
| `features/feature_validation.py` | Finitude e relações entre os valores calculados |

Schemas e contratos não dependem de builders. Silver compõe ingestão, perfil e
aceitação; Gold compõe Silver, protocolo e features. A qualidade compartilha o
schema físico, sem carregar o construtor da Gold. Modelagem importa o schema das
features; experimentos usam os loaders e verificadores de datasets quando necessário.
`artifacts.py` oferece escrita JSON atômica e hashes; `config.py` resolve a raiz
do checkout. Os caminhos de armazenamento permanecem iguais.

Os adaptadores antigos na raiz foram retirados; imports e comandos usam os módulos
da tabela. `features.py` foi substituído pelo pacote `features/`, que conserva sua
API pública de constantes e funções sem duplicar o cálculo. A
[migração de dados e features](operations/OPERATIONS.md#migração-de-dados-e-features)
explica os caminhos atuais, verificações históricas e identificação do código.

## Próximas fronteiras

`serving/request_schema.py` define o HTTP e reutiliza ordem, tipos e coerências
das features. `serving/model_release.py` verifica identidade, ambiente, tipos skops
e score de controle na inicialização. `serving/http_service.py` adapta esse runtime
ao FastAPI; não importa executores, tracking ou caminhos do checkout.

`integrations/serving_release_export.py` é o passo offline: verifica a avaliação
salva e os bytes congelados no MLflow, compara JSON/scores sobre a validação e
exporta os mesmos bytes. Não ajusta nem publica outro modelo. O recibo histórico
permanece igual; os hashes de código da época não são recalculados para aparentar
compatibilidade com a refatoração.

Poetry separa `main` (inferência), `pipeline` (dados/experimentos) e `dev` (checks).
A instalação padrão conserva todos os grupos. O wheel HTTP foi conferido em
ambiente só com `main`, fora do checkout. Isso não empacota os protocolos externos
dos workflows offline. Uma mudança para `src/` permanece decisão futura.
O autor validou o wheel com a referência real. `docker/serving/` define o build
em estágios; `scripts/serving/` contém a verificação operacional com a release
montada somente para leitura. O build/run nativo aguarda execução. Replay e
orquestração seguem o [mural](project/ROADMAP.md).

## Documentação e testes

`docs/project/`, `data/`, `modeling/` e `operations/` têm um
[índice](README.md). README resume; arquitetura explica dependências; contratos
definem regras; runbooks mantêm procedimentos. O contrato HTTP e sua execução ficam
em [SERVING_CONTRACT](operations/SERVING_CONTRACT.md).

Os testes acompanham as áreas do pacote. `tests/conftest.py` fornece fontes
sintéticas compartilhadas; `tests/experiment_fixtures.py` contém auxiliares de
comparação. Arquivos de teste não importam outros arquivos de teste. Inicialização
e opções das CLIs atuais são conferidas em `tests/modeling/test_entry_points.py` e
`tests/data/test_entry_points.py`; a API pública de features continua coberta em sua área.
Checks e limites ficam em [Testing](operations/TESTING.md).

A CLI de plano conserva seu escopo de metadados. O executor de setembro aplica
outro snapshot de autorização, verifica o modelo antes das partições e persiste
resultados sem novas runs. `evaluation/temporal_reference_metrics.py` recebe scores
e metadados; não lê dados nem carrega modelos. [Execução e limites](modeling/EVALUATION_PROTOCOL.md#executor-auditável-da-janela-fixada).
