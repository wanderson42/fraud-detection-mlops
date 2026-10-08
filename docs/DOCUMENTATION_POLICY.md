# Política de documentação

Política: `documentation_v1`. Adoção: 2026-10-06. Projeto: Fraud Detection MLOps.

## Objetivo

Permitir que outra pessoa entenda o problema, reproduza o projeto, investigue uma
falha e confira as evidências que sustentam seus resultados. A organização segue
a convenção adotada no projeto de MLOps de energia: README como ponto de entrada,
notebook principal como narrativa técnica curada, notebooks próprios para trabalhos
substanciais e documentos por responsabilidade em `docs/`.

## Responsabilidade de cada artefato

| Artefato | Conteúdo | Quando atualizar |
| --- | --- | --- |
| [README](../README.md) | Objetivo, estado atual, arquitetura, Quick Start e navegação | Mudança de uso, arquitetura ou marco validado |
| [Notebook principal](../notebooks/fraud_detection_mlops.ipynb) | Sínteses, decisões e resultados que mudam a história do projeto | Conclusão de uma etapa substancial |
| `notebooks/stages/` | Investigação, decisões, contratempos e evidências de uma etapa | Na branch responsável pela etapa |
| `notebooks/experiments/` | Experimentos controlados e comparação reproduzível | Quando existirem benchmarks ou avaliações |
| [Data pipeline](DATA_PIPELINE.md) | Origem, contratos, camadas, qualidade e proveniência | Mudança de fonte, schema, transformação ou armazenamento |
| [Operations](OPERATIONS.md) | Comandos, diagnóstico, recuperação e verificações | Mudança operacional |
| [EDA](EDA.md) | Exploração, interpretação e proveniência dos relatórios | Mudança da análise |
| [Protocolo temporal](EVALUATION_PROTOCOL.md) | Janelas, atraso de rótulos, população e métricas | Antes de mudar uma avaliação |
| [Contrato da Gold](GOLD_CONTRACT.md) | Features, causalidade, cold start, splits e proveniência | Mudança de feature ou contrato de modelagem |
| [MLflow](MLFLOW.md) | Tracking, persistência, assinatura e retomada | Mudança da integração ou resultados locais |
| [Baseline](BASELINE.md) | Candidatos, métricas, ajuste, artefatos e fronteira de avaliação | Mudança de experimento ou resultado real |
| [Testing](TESTING.md) | Isolamento tox–Poetry, checks, seleção de testes e limites | Mudança de validação ou CI |
| `docs/INFRASTRUCTURE.md` | Arquitetura, configuração, persistência e resiliência | Quando a infraestrutura for implementada |
| `docs/MODEL_CARD.md` | Uso do modelo, dados, avaliação, limitações e governança | Quando houver modelo avaliado |
| [Stakeholders](STAKEHOLDERS.md) | Problema, entregas, evidências, limitações e próximos marcos | Mudança no resultado ou no escopo |
| `references/evidence/` | Recibos estruturados de evidência, ligados à revisão avaliada | Fechamento de um marco validado |

Os caminhos de infraestrutura, model card e experimentos acima são destinos
planejados. Criamos os artefatos quando houver conteúdo concreto.

## Regra editorial

1. O README apresenta o projeto e aponta para os detalhes. Procedimentos completos
   têm sua referência principal no runbook.
2. O notebook principal guarda sínteses e links. Uma branch com investigação ou
   implementação substancial deve ter notebook próprio; uma correção pequena não
   exige um notebook novo.
3. O notebook da etapa explica o problema, alternativas relevantes, decisão, motivos,
   resultados e limitações. Procedimentos reutilizáveis ficam em `docs/`.
4. Um assunto tem uma referência principal. Outros documentos incluem uma síntese
   e um link, evitando cópias que possam divergir.
5. Diagramas entram quando ajudam a explicar uma relação ou arquitetura e devem
   acompanhar seu estado real de implementação.
6. O texto técnico usa português; nomes de módulos, comandos e identificadores
   preservam o formato do código. Termos necessários são explicados no primeiro uso.

## Estados e evidências

Cada etapa distingue **planejado**, **implementado** e **validado**, indicando o
escopo da validação. Uma etapa pode estar implementada e ter apenas parte de suas
verificações concluídas. Resultados esperados não são apresentados como observados.

Cada evidência informa:

- revisão de código e, quando aplicável, versão da fonte de dados;
- comando, execução ou experimento associado;
- origem da informação e ambiente conhecido;
- resultado observado, data e limitações de sua interpretação;
- localização do artefato ou link para sua consulta.

Resultados locais, CI, integridade de arquivos, qualidade semântica dos dados e
avaliação do modelo são registrados separadamente. O sucesso em uma categoria não
comprova as outras. Uma saída fornecida pelo autor é identificada como relato de
execução local; uma execução consultada na plataforma tem seu link e identificador.
O recibo documental sintetiza evidências; o manifesto e as auditorias nativas
continuam sendo os artefatos operacionais da aquisição.

Notebooks preservam outputs selecionados quando forem reais, úteis e adequados para
publicação. Células sem execução mantêm `execution_count: null` e outputs vazios.
Resultados históricos citados em Markdown indicam sua origem e revisão. Não copiamos
resultados para uma célula como se ela tivesse sido executada.

## Versionamento e armazenamento

Código, documentos, notebooks oficiais, inventário e recibos pequenos ficam no Git.
O diretório `data/` é ignorado: arquivos brutos, manifesto e auditorias de extração
permanecem no armazenamento local na fase atual e devem ser preservados juntos.
Um recibo versionado não substitui o armazenamento desses arquivos.

Mudanças documentais seguem a mesma branch da alteração que descrevem. O commit de
implementação pode anteceder o de documentação; o recibo identifica explicitamente
qual revisão foi avaliada. Não registramos antecipadamente o hash de um commit
futuro. Uma mudança no resultado histórico gera uma atualização rastreável pelo Git.
Versões de política só mudam quando suas regras mudam; releases só são citadas
quando forem efetivamente criadas.

Não publicamos credenciais, caminhos pessoais desnecessários, dados brutos nem dumps
extensos. A documentação usa caminhos relativos ao checkout e comandos pela CLI do
projeto. Compartilhamento de dados respeita os termos registrados para a fonte.

## Critérios antes do merge

- A mudança está descrita no documento responsável e no README quando alterar uso
  ou estado do projeto.
- A síntese principal e o notebook da etapa foram atualizados quando necessário.
- Comandos e links foram conferidos, e o estado implementado/planejado está claro.
- Evidências reais apontam para revisão, execução e ambiente conhecidos.
- Checks técnicos aplicáveis e CI estão registrados com seus escopos.
- Limitações e trabalho pendente estão explícitos.

Essa revisão documental integra o fechamento da entrega. A CI atual verifica
lockfile, lint, formatação e testes de código; não há ainda um gate automático de
links ou qualidade editorial.

## Fluxo de colaboração

O assistente prepara e explica os arquivos e comandos. Wanderson aplica, revisa e
executa no ambiente local, então faz commit e push. Consultas ao GitHub podem conferir
revisões e evidências; alterações remotas são feitas pelo autor neste fluxo.

## Marco inicial

A primeira aplicação desta política documenta a [etapa Bronze](../notebooks/stages/01_bronze_ingestion.ipynb)
e o [recibo de evidências de 2026-10-06](../references/evidence/bronze_2026-10-06.json).
