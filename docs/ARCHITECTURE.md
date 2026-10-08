# Organização e controle de complexidade

O código se organiza por responsabilidades que já existem. O pacote continua na
raiz do checkout; não criamos pastas vazias para infraestrutura futura.

Esta é a arquitetura implementada. Metas, prioridade e critérios das entregas
futuras têm uma referência única no [mural do projeto](ROADMAP.md).

| Código | Responsabilidade | Testes |
| --- | --- | --- |
| `artifacts.py` | Escrita JSON atômica e SHA256 em memória limitada | `tests/test_artifacts.py` |
| `bronze.py`, `profiling.py`, `silver.py`, `gold.py` | Aquisição, diagnóstico e contratos de dados | `tests/test_<módulo>.py` |
| `features.py`, `temporal.py`, `eda.py` | Features causais, protocolo e EDA | Testes correspondentes na raiz |
| `modeling/baseline.py` | Política fixa e verificação não executável de artefatos | `tests/modeling/test_baseline.py` e `test_train.py` |
| `modeling/train.py` | Ajuste, seleção na validação e publicação da execução | `tests/modeling/test_train.py` |
| `modeling/metrics.py` | Métricas de ranking e priorização diária | `tests/modeling/test_metrics.py` |
| `modeling/persistence.py` | Persistência skops e contrato de recarga | `tests/modeling/test_persistence.py` |
| `modeling/tracking.py` | API nativa MLflow e CLI de UI/verificação | `tests/modeling/test_tracking.py` |
| `modeling/diagnostics.py` | Diagnóstico da validação com modelo existente, permutação e SHAP | `tests/modeling/test_diagnostics.py` |
| `modeling/comparison.py` | Comparação pareada e gate de desenvolvimento | `tests/modeling/test_comparison.py` e `test_experiments.py` |
| `modeling/experiments.py` | Ablações limitadas, checkpoints e retomada | `tests/modeling/test_experiments.py` |
| `modeling/freeze.py` | Recibo da referência e política final, com paridade na validação | `tests/modeling/test_freeze.py` |
| `modeling/evaluation.py` | Avaliação do holdout congelado, decisão e publicação nativa | `tests/modeling/test_evaluation.py` |

As fixtures compartilhadas de modelagem ficam em `tests/modeling/conftest.py`.
Arquivos de teste não importam uns aos outros. O pytest usa `importlib`; tox e CI
continuam executando a mesma suíte com as versões do lockfile. Os cenários de
integração usam dados sintéticos pequenos, diretórios temporários e SQLite local.

O espelhamento é por responsabilidade: `silver.py` fica na raiz do pacote e seu
teste na raiz de `tests/`; `modeling/train.py` corresponde a
`tests/modeling/test_train.py`. Não é necessário repetir o nome do pacote dentro
de `tests/` nem criar um arquivo de teste para cada arquivo Python. Uma integração
pode cobrir vários módulos; [Testing](TESTING.md) descreve a organização e os riscos.

## Decisões desta refatoração

- A migração histórica foi concluída; o runner de conversão e seus testes específicos
  saíram da árvore ativa. O Git e as evidências preservam o histórico.
- Novos modelos usam o suporte nativo do MLflow para skops. Evitamos wrappers pyfunc
  próprios, um registry prematuro e um sistema paralelo de retomada de migrações.
- Operações de arquivo compartilhadas têm interfaces públicas. Modelagem não acessa
  funções privadas de treinamento ou Bronze para persistir artefatos.
- Importar o pacote não carrega `.env` nem altera o logger do processo.
- Os exemplos vazios `predict.py` e `plots.py` foram removidos. Serving será criado
  quando existir um contrato de inferência; gráficos reais já estão na EDA.
- `baseline_v2` versiona a mudança de persistência. `baseline_v1` permanece verificável
  como evidência histórica, sem qualquer carregamento ou conversão de joblib.

## Critérios para próximas mudanças

1. Implementar uma necessidade concreta e dizer qual operação ou falha ela resolve.
2. Preferir a API da biblioteca antes de criar uma abstração própria.
3. Separar responsabilidades quando isso reduzir acoplamento ou esclarecer o fluxo;
   a quantidade de módulos ou linhas não é uma meta isolada.
4. Testar riscos reais: causalidade, população de avaliação, integridade, recarga e
   falhas de publicação. Remover testes de funcionalidades removidas.
5. Não manter caminhos antigos de escrita indefinidamente. Compatibilidade de leitura
   deve ser pequena e necessária para as evidências que decidimos preservar.
6. Revisar dependências e documentação junto com o código. Recursos novos precisam
   justificar seu custo de manutenção no portfólio.

Os runners de dados e de experimentos concentram verificação, publicação e
recuperação; merecem atenção conforme evoluírem. Separar um componente é útil
quando ele tiver contrato próprio, duplicação ou acoplamento que dificulte uma
mudança. Dividir arquivos apenas por tamanho acrescentaria navegação sem resolver
esses problemas. Novas integrações devem preservar interfaces pequenas.

O layout atual e os caminhos padrão suportam execução a partir do checkout.
O tox instala o projeto de forma editável; essa validação não comprova um wheel
independente com todos os recursos de `references/`. Antes do serving/container,
definiremos explicitamente o empacotamento desses recursos e os caminhos operacionais.
Migrar para `src/` só fará sentido junto desse trabalho, sem depender de atalhos em
`sys.path`. Docker, Prefect e armazenamento remoto serão entregas com escopo próprio.

## Primeiro contrato de inferência — definido, implementação pendente

Contrato de projeto: `precomputed_features_v1`. A avaliação final aprovou o gate
para revisão de serving de laboratório; isso não instala ou valida um serviço.
O primeiro endpoint de pontuação será `POST /score`, sem alteração do HGB existente.

### Entradas, saída e responsabilidade

| Elemento | Contrato proposto |
| --- | --- |
| Versão | `schema_version: 1` e `feature_contract_version: gold_v1` |
| Identidade do evento | `transaction_id`, `customer_id` e `terminal_id`: inteiros não negativos, metadados de correlação; não são preditores |
| Relógio | `tx_datetime` e `feature_as_of` iguais, em ISO 8601 sem offset, preservando o relógio simulado sem timezone; rejeitar conversões silenciosas |
| Features | Objeto `features` com exatamente as 19 chaves de `FEATURE_COLUMNS`; construir a matriz na ordem canônica, independentemente da ordem do JSON |
| Validação | Rejeitar ausências, extras, strings numéricas, booleanos, nulos e valores não finitos; manter tipos, domínios e coerências do contrato Gold |
| Fronteira dos rótulos | `TX_FRAUD`, `TX_FRAUD_SCENARIO` e `LABEL_AVAILABLE_AT` não pertencem à requisição de pontuação |
| Saída | `schema_version`, `transaction_id`, `score`, `score_semantics: uncalibrated_ranking`, `model_id`, `model_sha256`, `feature_contract_version` e identificador da release de serving |
| Score | Valor finito entre 0 e 1; sem classe, limiar automático, decisão de bloqueio ou seleção dos 100 clientes |
| Estado | Pontuação sem estado; históricos e rótulos atrasados são responsabilidade do produtor de features |

Domínios e relações seguem o [contrato da Gold](GOLD_CONTRACT.md) e o
[dicionário](DATA_DICTIONARY.md). Preservamos valores monetários zero; hora vai de
0 a 23 e dia ISO de 1 a 7. Contagens são inteiras não negativas, flags são 0/1 e
razões/taxas usam a mesma convenção de denominador zero e tolerâncias da Gold.
Não reinterpretamos unidades simuladas como moeda real.

O produtor deve usar eventos anteriores a `t` e rótulos com disponibilidade
estritamente anterior a `t`, incluindo o atraso de sete dias. Declarar
`feature_as_of = tx_datetime` não comprova causalidade: os testes de paridade do
produtor terão de demonstrá-la. Receber features prontas por HTTP demonstra o
serviço de pontuação; não demonstra ainda um pipeline de features online.

A fila de investigação diária é outra responsabilidade. Não incorporamos uma
política retrospectiva de dia completo ao endpoint como se fosse autorização
instantânea de pagamento. A futura fila por eventos terá contrato próprio.

### Identidade, execução e checks da próxima implementação

O serviço carregará uma cópia de distribuição dos bytes skops já aprovados, com
recibo, schema e identificação da release. MLflow continuará sendo a origem do
modelo; a inferência não dependerá de uma consulta ao tracking a cada requisição.
Nenhum novo fit ou registro de modelo é necessário. Hashes e tipos aprovados
serão conferidos antes da carga, com artefato montado somente para leitura.

O ambiente de avaliação permanece reproduzível na revisão `e0fddc0` e no lockfile
associado. O runtime de serving terá dependências explícitas e sua própria
identidade; não substituímos o ambiente histórico por uma resolução nova.
A imagem usará apenas o necessário para pontuar, sem dados brutos, SHAP ou stack
de treinamento incorporados por conveniência. A solução concreta será medida.

| Check da entrega | Evidência necessária |
| --- | --- |
| Paridade | Mesmas features e IDs produzem scores compatíveis com os salvos, inclusive após serialização JSON; sem fit e sem nova seleção no teste |
| Entradas inválidas | Resposta de erro previsível para schema/domínio inválido; requisição rejeitada não executa predição |
| Identidade | Resposta e informações do serviço apontam para bytes do modelo e release identificados; não usar alias mutável como única identidade |
| Inicialização | Falhar com modelo ausente, alterado ou contrato incompatível; `/health` indica processo e `/ready` exige modelo carregado |
| Independência do checkout | Carregar recursos e pontuar fora da árvore do Git, com caminhos operacionais explícitos |
| Custo e Docker | Construção reproduzível, imagem identificada, usuário sem root, modelo somente leitura; medir tamanho, memória e latência sem prometer SLO ainda não observado |

Esses checks serão implementados junto com o serviço. Este contrato documental
não cria novos testes, servidores, containers ou ferramentas de infraestrutura.
