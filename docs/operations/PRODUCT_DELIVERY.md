# Entrega do produto de triagem de fraude

## Valor para quem usa

O produto prioriza transações e clientes para uma equipe de investigação com
capacidade limitada. A integração recebe uma transação, utiliza o histórico
disponível naquele instante, calcula um score identificado e registra a decisão
de triagem. O analista consulta casos priorizados e seus fatores explicativos;
o feedback posterior permite medir a qualidade e avaliar novos modelos.

A API atende o sistema transacional; a fila e a consulta de casos atendem o
analista; Grafana atende quem opera o serviço. A plataforma, o champion e os
componentes verificados serão reutilizados. Este documento define o próximo
recorte; a integração terá seu aceite registrado quando executada.

## Usuários e entregas

| Usuário | Entrega |
| --- | --- |
| Sistema transacional | API de pontuação com modelo identificado |
| Analista de fraude | Fila priorizada, contexto da transação e explicação |
| Equipe de operação | Grafana com saúde, erros, atrasos e qualidade |
| Equipe de ML | Linhagem, comparação de candidatos e implantação controlada |

A tabela define responsabilidades; os aceites abaixo identificam o que já foi
executado e o que será integrado em cada recorte.

## Jornada que a entrega deve demonstrar

| Passo | Comportamento do produto | Vínculo verificável |
| --- | --- | --- |
| Preparar | Bronze íntegro alimenta Silver/Gold com contratos temporais | Bundles, hashes, partições e produtor |
| Selecionar | Candidatos enfrentam critérios absolutos e o modelo ativo | Inputs MLflow, métricas, política e gate |
| Servir | Uma versão fixa aprovada atende a API | Registry/run, hash, release e contrato HTTP |
| Receber | Transação entra com identidade e instante da fonte | Contrato, idempotência e relógio |
| Construir features | Estado causal usa eventos e feedback disponíveis | Contrato, feature_as_of e checkpoint |
| Pontuar | Cliente do fluxo chama a API do champion | Requisição, resposta, score e modelo efetivo |
| Priorizar | Política converte scores em fila compatível com a capacidade | Cliente/dia, prioridade e motivo da seleção |
| Investigar | Analista vê o caso e as contribuições para o score | Valores, explicação, unidade e modelo |
| Aprender | Feedback chega com disponibilidade temporal própria | Decisão, maturidade dos rótulos e métricas |
| Operar | Serviço e qualidade são observados; implantação pode ser revertida | Grafana, recibos, identidades e recuperação |

O aceite deve reconciliar o mesmo conjunto de eventos ao longo dessa jornada.
Exibir um replay concluído e uma API acessível em paralelo não comprova que as
decisões passaram pela API. O score atual é de ranking, não uma probabilidade
calibrada. A fila prioriza revisão humana; ações sobre pagamentos pertencem à
política do sistema integrador. Bloqueio automático ou latência de autorização
exigiriam um contrato próprio de decisão, falhas e desempenho.

## Primeiro recorte: eventos → API → decisões

Reutilizar executor causal, inferência e destinos gerenciados. Implementar um
adaptador HTTP e uma entrada Make canônica, sem duplicar features, treinamento
ou triagem. Fixar modelo/release e política no início; mudança de identidade
durante o lote exige parada controlada ou transição explicitamente registrada.
Revisar períodos autorizados antes de executar. Reproduzir períodos já consumidos
verifica integração; não cria nova confirmação independente de qualidade.
Conectar o champion ao fluxo não exige novo treino.

Critérios de aceite:

- Toda decisão pontuada tem resposta HTTP e identidade de modelo/release conferíveis.
- Entrada inválida ou features fora do contrato não produzem decisão válida.
- Falha HTTP tem timeout e retentativas limitadas; checkpoint não avança como se houvesse score.
- Retomada/reentrega não duplicam decisões ou alertas; scores conflitantes são detectados.
- A fila mantém cliente/dia e a política temporal, com vínculo às transações.
- Feedback só entra na avaliação quando disponível, vinculado à decisão.
- Resultado reconcilia eventos, requisições, scores, decisões e alertas.
- Execução e retomada têm comandos documentados.

O caminho HTTP não terá fallback silencioso para inferência local. O adaptador
não altera treinamento ou promove outro modelo para resolver falha de integração.

## Explicabilidade ligada ao modelo entregue

Já existe `fraud_detection_mlops/evaluation/validation_diagnostics.py`: usa
`shap.TreeExplainer`, amostra uniforme para visão global e casos locais do HGB.
Verifica aditividade em log-odds e reconstitui o score com a função logística.
Produz `shap_summary.csv`, `shap_values.parquet`, importância e waterfall.
[Diagnósticos existentes](../modeling/DIAGNOSTICS.md).

Reaproveitar esse núcleo, adaptando os vínculos ao champion atual. Explicações
dos pesos antigos não serão apresentadas como se fossem da nova versão. Revisar
dependências de manifests antes de executar o comando histórico. Precisamos
integrar a explicação ao modelo e à decisão atuais, não criar outro pipeline SHAP.

Cada explicação deve identificar modelo/hash, run/versão, transação, contrato,
valores de features, método/versão SHAP, saída explicada e referência utilizada.
Conservar contribuições assinadas e valor-base, verificar a soma contra a saída
correspondente e derivar o score quando necessário. Não apresentar log-odds
como pontos percentuais de probabilidade.

A visão global resume a amostra declarada; a local mostra o que elevou ou reduziu
o score em relação à base. Uma explicação preditiva não identifica a causa real
da fraude. A [documentação SHAP](https://shap.readthedocs.io/en/latest/example_notebooks/overviews/Be%20careful%20when%20interpreting%20predictive%20models%20in%20search%20of%20causal%20insights.html)
explica essa distinção. SHAP é uma escolha adequada para explorar, não requisito
universal de explicabilidade nem substituto da avaliação do modelo.

Calcular inicialmente fora do caminho crítico da API, sob demanda ou em lote
limitado. Reusar por hash de modelo e identidade exata da entrada, quando o
contrato permitir. Medir custo antes de decidir sobre cálculo síncrono. O runtime
de inferência pode continuar enxuto.

## Apresentação e operação

Grafana permanece com disponibilidade, erros, latência, progresso, atrasos e
indicadores agregados. Investigação pede consulta de casos: identidade,
prioridade, score, fatores e feedback. IDs de clientes/transações e contribuições
por caso pertencem ao armazenamento de casos, não a labels do Prometheus.

Primeiro entregar consulta de leitura e relatório por caso com artefatos SHAP
versionados. Depois avaliar uma interface pequena, como Streamlit, se facilitar
a investigação e a demonstração. Reutilizar API/armazenamento; não criar outra
lógica de decisão ou inferência. Essa escolha não substitui Grafana nem exige
imediatamente um serviço adicional.

## Interpretação dos limites numéricos

**Os limites numéricos atuais são específicos deste laboratório. Não são padrões
universais de MLOps. Em uma operação real, seriam calibrados com custos de fraude,
falsos positivos, capacidade de investigação e incerteza das métricas. Ganhar
0,005 de AP, sozinho, não comprova significância estatística nem retorno financeiro.**

AP mínima 0,5, Precision@100 mínima 0,45, ganho absoluto AP 0,005 e tolerância
P@100 0,01 são uma política versionada. O ganho é absoluto: 0,5 ponto percentual,
não 0,5% relativo. Critérios reais também precisam de estabilidade temporal e por
segmentos. Precision@100 aproxima a capacidade escolhida; não mede perdas
financeiras evitadas. A unidade cliente/dia precisa corresponder à operação.

Definir os critérios antes de observar os resultados. Menor custo, latência ou
obrigação regulatória podem justificar outra política de não inferioridade;
nem toda atualização precisa ganhar AP. Registrar a motivação e versionar a
política antes da comparação em dados temporalmente autorizados.
[Política executável e aceites](MODEL_UPDATES.md).

## Simplificar a manutenção e a leitura

Organizar responsabilidades: dados, features/estado, treino/Registry, inferência,
triagem/feedback, explicação e observabilidade. Escolher um caminho suportado e
uma entrada pública clara para cada responsabilidade. Revisar chamadas em código,
testes, Make, CI e runbooks antes de remover componentes substituídos.

Preservar dados e recibos. Verificadores com hashes de código/planos congelados
exigem revisão explícita de compatibilidade, sem reescrever aceites antigos.
Evidência histórica não obriga manter comandos legados como caminho principal.

O guia principal deve mostrar problema, usuários, execução canônica e resultado
visível. Detalhes dos aceites ficam em seção própria, evitando repetir ressalvas
em toda apresentação. Concluir com instalação limpa, fluxo integrado, retomada,
explicação de um caso e ensaio de implantação/rollback com identidades.
Terraform sucede a estabilização dessa experiência.

## Executar eventos pela API do champion

O ensaio do autor passou em 2026-10-22: 1 teste nativo em 493,00 s, com casos
pontuados pelo champion e feedback associado. [Evidência do fluxo](../../references/evidence/product_http_flow_author_validation_2026-10-22.json).
Não há novo treino, build, Helm upgrade, troca de tráfego ou mudança de alias.
A tabela de usuários é a referência para a entrega, não declaração de que todas
as interfaces de investigação já estejam implementadas.

O cliente local usa um port-forward temporário do gateway existente no KinD.
Reutiliza a lógica de `DurableTriageReplay.handle` para features causais, triagem
por cliente/dia, feedback e checkpoint. Os módulos aceitos não são modificados.
A inicialização do adaptador cria seu próprio ledger e adiciona recibos HTTP;
o cliente não carrega pesos e não tem fallback de inferência local.

A preparação lê somente os eventos de uma cópia concluída do replay existente.
Confere a estabilidade do SQLite e dos seus sidecars e grava um novo stream
imutável. Scores, decisões, limiar e modelo antigos não alimentam o novo fluxo.
São preservados 14 dias de contexto desde 02/09, as primeiras 200 transações
por relógio/ID a partir de 16/09 e seus feedbacks com atraso de sete dias. Pares
no mesmo timestamp são incluídos; o orçamento máximo é 256 transações pontuadas.
O contexto é processado sem chamadas de score. Este recorte verifica integração
sobre eventos já observados, sem atribuir nova confirmação de qualidade a eles.

`references/product_flow_policy_v1.json` fixa limiar 0,5 para o ensaio de
integração e capacidade de até 100 clientes/dia. Esse limiar é explícito e não
é o limiar calibrado dos pesos antigos nem uma recomendação financeira para o
champion novo. A política é de primeiro cruzamento por cliente/dia, respeitando
o orçamento; não é a seleção retrospectiva dos maiores scores do dia inteiro.
Pode resultar em menos de 100 alertas. A consulta apresenta alertas primeiro,
com scores decrescentes, sem modificar a política que os selecionou.

### Validar e commitar antes da preparação

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/product_flow tests/model_updates tests/operational_round \
  tests/streaming/replay/test_durable_triage_replay.py \
  tests/streaming/decisions/test_customer_day_alert_policy.py \
  tests/serving tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py tests/infra/test_platform_entrypoints.py &&
make product-validate
```

O pacote fornece os comandos de commit dos caminhos revisados. A preparação
exige esses arquivos de código/política no HEAD. Não inclui configurações do
editor ou stores locais de experimentação.

### Preparar e executar no ambiente existente

Deixe os serviços prontos e suspenda escritores do replay fonte, Registry e
rota de serving. Não desligue a API. O lock do controlador coordena comandos
locais; a declaração do operador cobre outros clientes.

```bash
make product-prepare PRODUCT_NO_OTHER_WRITERS=1 \
  PRODUCT_SOURCE_STATE=data/replay/paced-native-v1 &&
FRAUD_PRODUCT_FLOW_NO_OTHER_WRITERS=1 \
  .venv/bin/python -m tox -e product-flow &&
make product-cases
```

O ID padrão é `champion-http-v1`. O ensaio pausa após metade dos scores,
retoma, conclui o feedback e reinvoca o fluxo concluído sem novas chamadas de
score. Verifica Registry, rota, identidade HTTP e imagens antes/depois. Os
HTTPs de readiness e smoke desses gates são separados dos contadores de score
de transações. Não se promove um candidato para realizar este ensaio.

O cliente usa timeout de 3 s por tentativa, no máximo três tentativas e pausas
curtas para conexão/timeout/429/5xx transitórios. HTTP 422, JSON inválido e
identidade divergente não são tratados como pontuação bem-sucedida. Cada
resposta aceita recebe hashes da requisição/resposta e é gravada na mesma
transação SQLite do evento, decisão, eventual alerta e checkpoint.

Uma falha depois da resposta HTTP e antes do commit pode repetir a inferência
pura. Portanto, chamadas HTTP são pelo menos uma vez; o ledger mantém uma única
decisão por transação. Não se alega transação distribuída nem exatamente uma
chamada à API. Não há envio externo de alertas neste adaptador; uma integração
que envie notificações exigirá contrato de entrega/outbox próprio.

### Resultado, verificação e retomada

Arquivos em `data/storage/kind-platform/product-flow/<flow-id>/`:

| Arquivo | Conteúdo |
| --- | --- |
| `input.json` e `events.sqlite` | Política, implementação, champion e snapshot dos eventos |
| `checkpoint.sqlite` | Eventos, estado causal, requests, responses, triagem, alertas, feedback e recibos HTTP |
| `invocation-*.json` | Progresso, novas decisões e tentativas HTTP de cada chamada |
| `rehearsal.json` | Aceite de pausa/retomada/reinvocação, publicado somente após os gates finais |
| `verification-*.json` / `failure-*.json` | Reconciliações posteriores ou falhas preservadas |

A verificação reprocessa os eventos em memória, usando as respostas HTTP já
gravadas, e compara requests causais, resultados de triagem e todas as tabelas.
Ela verifica o vínculo da resposta e os digests; não substitui a resposta por
uma inferência local. Feedback de cada caso permanece com instante de disponibilidade.
A contagem de alertas com feedback positivo refere-se à transação disparadora;
não é precisão consolidada por cliente, retorno financeiro ou qualidade global.

Depois de falha, preserve os arquivos e retome o mesmo ID:

```bash
make product-run PRODUCT_NO_OTHER_WRITERS=1 &&
make product-verify PRODUCT_NO_OTHER_WRITERS=1 &&
make product-cases
```

Não repita `product-rehearse` sobre um checkpoint iniciado: use run/verify.
O ensaio só gera `rehearsal.json` numa execução iniciada com checkpoint novo;
a recuperação após falha tem seus recibos de invocação e verificação. Não crie
outro ID para contornar falha de dados, identidade ou mudança de código.
O input preparado permite retomar sem abrir novamente os originais. Se champion,
contrato ou código mudar, o controlador recusa a execução e conserva os recibos.

A consulta de casos já mostra requisição, score, motivo de triagem e feedback;
a explicação SHAP do champion atual será ligada no próximo recorte. Grafana
continua com os painéis existentes; exportar métricas do novo ledger também é
uma ligação posterior e não é inferido do sucesso do HTTP.

### Aceites que permanecem separados

A promoção recorrente de um sucessor com pesos diferentes e seu rollback ainda
precisam de aceite nativo no KinD quando existir candidato qualificado. A retenção
nativa já aceita não é reaberta. Este fluxo usa o champion atual, sem flexibilizar
gates ou alterar aliases para fabricar uma promoção.


## Retomada do instalador da release

A [manutenção do instalador](SERVING_RECOVERY.md) trata a reinicialização sobre
uma release já presente. Reutiliza bytes iguais, recusa divergências e testa
reinício no mesmo volume antes de atualizar a imagem portadora. O próximo
aceite operacional é específico dessa manutenção; o fluxo de eventos HTTP
já foi aprovado pelo autor, sem exigir novo replay para instalar a correção.
