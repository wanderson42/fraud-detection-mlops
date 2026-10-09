# Contribuição de modelos

Contrato executável: **`model_interface_v1`**. Implementado em
`modeling/contracts/model_interface.py`; as factories ficam em `modeling/algorithms/`, registradas em `model_catalog.py`.
O contrato HTTP de pontuação organiza o serviço. Este contrato organiza a
integração do código de um modelo ao treinamento, à avaliação, à persistência e
ao tracking. A compatibilidade foi verificada com dados sintéticos na preparação;
a validação local do autor e uma execução de CI são evidências separadas.

## Padrões obrigatórios e responsabilidades

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

## Como contribuir

1. Implementar uma factory em `modeling/algorithms/<algoritmo>.py`, juntando pré-processamento aprendido
   e classificador na mesma Pipeline. Seguir tipagem, estilo e imports verificados
   pelas ferramentas do projeto; o contrato funcional é validado separadamente.
2. Declarar um `ModelSpec`. Para uma contribuição registrada, acrescentar a factory
   explicitamente a `MODEL_CATALOG`, com chave igual ao `model_id`.
3. Declarar parâmetros, features, seed, dependências e limites no protocolo do novo
   experimento. A política fixa `baseline_v1` conserva seus três algoritmos;
   adicionar ao catálogo não a expande. Protocolos congelados não são editados.
4. Passar pela mesma entrada `candidate_training.fit_candidate(..., model_spec=spec,
   parameters=..., random_state=...)`. O retorno continua
   `(model, scores, metrics, timings)`; avaliação e publicação usam os componentes
   existentes. Uma factory não implementa sua própria política de avaliação.
5. Verificar isolamento de instâncias, alinhamento, transformação aprendida só no
   treino, ordem das predições e persistência. As contribuições registradas entram
   no check parametrizado em `tests/modeling/contracts/test_model_interface.py`; verificar tracking
   e tipos adicionais quando a integração mudar.

Esse primeiro suporte é para Pipelines scikit-learn/skops. Estimators customizados
precisam cumprir a API nativa, testes apropriados e revisão dos tipos persistidos;
não basta devolver métodos com os mesmos nomes. Redes neurais exigirão uma decisão
sobre adapter, formato e runtime quando houver hipótese para sua adoção.

## Exemplo verificável, fora da baseline

[`examples/model_contribution.py`](../../examples/model_contribution.py) demonstra
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

## Integração com os experimentos

`candidate_training.fit_candidate` é a entrada comum. O executor da baseline,
as ablações e a busca HGB importam essa entrada; um experimento não importa o
runner de outro para ajustar um candidato. Acrescentar uma contribuição ao catálogo
não a inclui na baseline histórica nem no protocolo HGB.

O fluxo da busca está no [protocolo temporal](EVALUATION_PROTOCOL.md#otimização-temporal-do-hgb-com-optuna-v1).
Parâmetros usam os nomes nativos do scikit-learn; o protocolo continua limitando
espaço, orçamento, datas e gates. Os checks de interface estão em
[Testing](../operations/TESTING.md#contrato-de-integração-de-modelos).
