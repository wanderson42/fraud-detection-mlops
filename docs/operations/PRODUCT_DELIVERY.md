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
