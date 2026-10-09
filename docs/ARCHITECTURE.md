# Organização e controle de complexidade

O código se organiza por responsabilidades que já existem. O pacote continua na
raiz do checkout; não criamos pastas vazias para infraestrutura futura.

A tabela descreve a arquitetura implementada. As seções de contratos propostos
identificam o trabalho pendente. Metas, prioridade e critérios das entregas futuras
têm uma referência única no [mural do projeto](ROADMAP.md).

| Código | Responsabilidade | Testes |
| --- | --- | --- |
| `artifacts.py` | Escrita JSON atômica e SHA256 em memória limitada | `tests/test_artifacts.py` |
| `bronze.py`, `profiling.py`, `silver.py`, `gold.py` | Aquisição, diagnóstico e contratos de dados | `tests/test_<módulo>.py` |
| `features.py`, `temporal.py`, `eda.py` | Features causais, protocolo e EDA | Testes correspondentes na raiz |
| `modeling/interface.py`, `modeling/models.py` | Contrato executável e factories explícitas, compartilhados pelo treinamento e pela persistência | `tests/modeling/test_interface.py` |
| `modeling/development.py`, `modeling/hgb_optuna.py` | Datas autorizadas, Gold causal própria, estudo Optuna local e comparação pareada de configurações | `tests/modeling/test_development.py`, `tests/modeling/test_hgb_optuna.py` |
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

## Interface para contribuição de modelos

Contrato executável: **`model_interface_v1`**. Implementado em
`modeling/interface.py`; as factories atuais ficam em `modeling/models.py`.
O contrato HTTP de pontuação organiza o serviço. Este contrato organiza a
integração do código de um modelo ao treinamento, à avaliação, à persistência e
ao tracking. A compatibilidade foi verificada com dados sintéticos na preparação;
a validação local do autor e uma execução de CI são evidências separadas.

### Padrões obrigatórios e responsabilidades

| Fronteira | Regra |
| --- | --- |
| Contribuição | `ModelSpec(model_id, factory)`: identificador em `snake_case`, começando por letra; factory sem argumentos que devolve uma Pipeline completa, nova e sem componentes já ajustados. Construção não lê dados nem faz fit, logging ou promoção. |
| Estimator | Usar a API scikit-learn: `fit(X, y)` retorna o próprio objeto; `get_params`/`set_params`/`clone`, `predict_proba`, `classes_` e `feature_names_in_`. Não duplicar essa API numa classe-base do projeto. |
| Configuração | `build_model(spec, parameters=..., random_state=...)`: parâmetros nativos como `classifier__learning_rate`, com valores JSON finitos. A configuração não substitui etapas; seeds usam o argumento separado. O builder clona a factory e define os `random_state` expostos. Estimadores determinísticos podem não ter esse parâmetro. |
| Features | DataFrame não vazio, índice único, valores reais finitos e colunas exatamente na ordem declarada. O primeiro suporte usa as 19 features da Gold ou um subconjunto canônico autorizado pelo protocolo. Rejeitar extras, reordenação, nulos, booleanos e valores complexos. As regras de domínio e causalidade continuam no contrato de dados. |
| Rótulos | Series inteira, índice alinhado às features e valores 0/1. O treino exige ambas as classes. Nenhum pré-processador recebe dados de avaliação para aprender transformações. |
| Metadados | Ficam fora dos preditores. A entrada comum verifica alinhamento entre features, rótulos e metadados; a saída Parquet preserva IDs e ordem dos eventos, com colunas/tipos canônicos. O índice pandas não é a identidade persistida. |
| Predição | Pipeline ajustada com classes inteiras **na ordem `[0, 1]`** e nomes de features compatíveis. `predict_proba` devolve matriz `(n, 2)`, valores finitos em `[0, 1]` e soma unitária por linha. Classes invertidas são rejeitadas; a coluna 1 corresponde explicitamente à fraude. |
| Saída comum | `predict_scores` devolve vetor float64 `(n,)`, na ordem de entrada. `fit_and_score` devolve `(Pipeline, scores, timings)`; tempos são segundos de parede de fit e pontuação, incluindo checks da pontuação. Scores são de ranking, sem presumir calibração. |
| Erros | Incompatibilidades da interface usam `ModelContractError`; convergência inválida impede publicação. Persistência e tracking conservam seus erros próprios. Não esconder falhas com score padrão ou troca automática de formato. |
| Persistência e tracking | Pipeline completa em skops, tipos revisados e paridade após recarga; um modelo por run principal do MLflow. O tracking verifica scores antes de criar a run e registra a versão da interface. Sem aprovação automática de tipos desconhecidos. |
| Política do experimento | O runner controla janelas, disponibilidade de rótulos, modelos autorizados, features, orçamento, avaliação, proveniência e promoção. Aceitar a interface não autoriza executar um modelo, abrir um holdout ou mudar gates. |

### Como contribuir

1. Implementar uma factory no pacote `modeling`, juntando pré-processamento aprendido
   e classificador na mesma Pipeline. Seguir tipagem, estilo e imports verificados
   pelas ferramentas do projeto; o contrato funcional é validado separadamente.
2. Declarar um `ModelSpec`. Para uma contribuição registrada, acrescentar a factory
   explicitamente a `MODEL_CATALOG`, com chave igual ao `model_id`.
3. Declarar parâmetros, features, seed, dependências e limites no protocolo do novo
   experimento. A política fixa `baseline_v1` conserva seus três algoritmos;
   adicionar ao catálogo não a expande. Protocolos congelados não são editados.
4. Passar pela mesma entrada `train.fit_candidate(..., model_spec=spec,
   parameters=..., random_state=...)`. O retorno continua
   `(model, scores, metrics, timings)`; avaliação e publicação usam os componentes
   existentes. Uma factory não implementa sua própria política de avaliação.
5. Verificar isolamento de instâncias, alinhamento, transformação aprendida só no
   treino, ordem das predições e persistência. As contribuições registradas entram
   no check parametrizado em `tests/modeling/test_interface.py`; verificar tracking
   e tipos adicionais quando a integração mudar.

Esse primeiro suporte é para Pipelines scikit-learn/skops. Estimators customizados
precisam cumprir a API nativa, testes apropriados e revisão dos tipos persistidos;
não basta devolver métodos com os mesmos nomes. Redes neurais exigirão uma decisão
sobre adapter, formato e runtime quando houver hipótese para sua adoção.

### Exemplo verificável, fora da baseline

[`examples/model_contribution.py`](../examples/model_contribution.py) demonstra
uma contribuição Gaussian Naive Bayes com StandardScaler, usando o mesmo fit,
métricas, Parquet/skops e tracking nativo. São **80 exemplos de treino e 20 de
avaliação gerados em memória**, sem leitura dos dados Handbook. Os resultados do
exemplo não estimam a qualidade de um candidato antifraude.

Execute a partir da raiz, escolhendo um diretório novo:

```bash
EXAMPLE_PATH="data/examples/model-interface-$(date +%Y%m%d-%H%M%S)"
poetry run python -m examples.model_contribution \
  --output "$EXAMPLE_PATH" \
  --tracking-root data/tracking/interface-smoke
```

O exemplo cria uma run principal no experimento `fraud-model-interface-smoke`,
identificada como `synthetic_integration_smoke` e `not_promoted`. O store é separado
por padrão nesse comando; `--tracking-root` é opcional. Reusar um diretório de saída
existente é rejeitado. Uma falha no smoke check pode deixar arquivos locais de
inspeção; não há um mecanismo paralelo de retomada. Após investigar, escolher uma
nova execução. Isso não é o executor de experimentos governados.

O teste de integração do exemplo usa diretórios temporários, confere recarga local
mais download/revisão do MLflow, e comprova uma única run concluída. Não adiciona
esse modelo à política histórica nem escolhe outro candidato para servir.

### Relação com Optuna e com as evidências históricas

O executor `modeling/hgb_optuna.py` usa a mesma factory e `train.fit_candidate`, com
parâmetros separados da seed. `modeling/development.py` prepara e verifica somente
as datas autorizadas no [protocolo Optuna](../references/hgb_optuna_protocol_v1.json).
O módulo `hgb_optuna.py` e as funções `optimize_hgb`, `fit_hgb_fold` e
`verify_hgb_optimization` tornam explícito o escopo do algoritmo. Esta implementação
autoriza somente o HGB. Optuna 5.0.0 é uma dependência travada. A integração está implementada; a busca
sobre os dados do autor ainda não foi executada.

SQLite persiste trials e seus resultados. Um lock de processo limita a um escritor
local; não há busca distribuída. O sampler TPE é recriado com seed `42 + número do
trial`, pois o backend não guarda seu estado aleatório. Um teste compara sequências
contínuas e retomadas, inclusive depois dos cinco trials de inicialização. Isso é
uma política explícita de amostragem; não equivale a manter um único sampler vivo.

O executor verifica identidade de código/lockfile/dados/protocolo/tracking,
artefatos concluídos e orçamento antes de novos ajustes. Cada ajuste de modelo por
corte usa sua própria run principal MLflow, Pipeline completa, skops e scores
Parquet. A paridade é conferida ao salvar e publicar. O relatório agrega resultados
por trial; não mistura modelos numa única run.

Uma tentativa é registrada antes do fit. Um trial interrompido vira `FAIL` na
retomada e consome orçamento; resultados concluídos são preservados. Referência
interrompida exige revisão explícita, sem refit automático ou publicação duplicada.
Os detalhes operacionais estão no [protocolo](EVALUATION_PROTOCOL.md#otimização-temporal-do-hgb-com-optuna-v1).

Alterar código impede reutilizar execuções que exigem os bytes da revisão anterior.
Preservar o recibo congelado e os artefatos; usar a revisão histórica para reproduzir
seu ambiente e `evaluation verify` para conferir os resultados já salvos. Não voltar
a `evaluation run` nesta revisão para ajustar ou repetir o teste consumido.
Os checks sintéticos confirmam compatibilidade dos parâmetros/predições das três
factories com o protocolo original, sem retreinar o modelo real.

A preparação deste contrato está registrada em
[Testing](TESTING.md#contrato-de-integração-de-modelos) e na
[evidência estruturada](../references/evidence/model_interface_preparation_2026-10-08.json).

Referências primárias: [API de estimators scikit-learn](https://scikit-learn.org/stable/developers/develop.html)
e [pré-processamento e prevenção de leakage](https://scikit-learn.org/stable/common_pitfalls.html).
