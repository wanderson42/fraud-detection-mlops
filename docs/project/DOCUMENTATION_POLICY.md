# Política de documentação

Política: `documentation_v1`. Adoção: 2026-10-06. Projeto: Fraud Detection MLOps.

## Objetivo

Permitir que outra pessoa entenda o problema, reproduza o projeto, investigue uma
falha e confira as evidências que sustentam seus resultados. A organização segue
a convenção adotada no projeto de MLOps de energia: README como ponto de entrada,
notebook principal como narrativa técnica curada, notebooks próprios para trabalhos
substanciais e documentos por responsabilidade em `docs/project/`, `docs/data/`, `docs/modeling/` e `docs/operations/`. O [índice](../README.md) mantém a navegação.

## Responsabilidade de cada artefato

| Artefato | Conteúdo | Quando atualizar |
| --- | --- | --- |
| [README](../../README.md) | Objetivo, estado atual, arquitetura, Quick Start e navegação | Mudança de uso, arquitetura ou marco validado |
| [Mural de metas](ROADMAP.md) | Backlog principal, prioridades, estados e critérios de conclusão | Mudança de escopo ou conclusão de um marco |
| [Contexto e propósito](PROBLEM_CONTEXT.md) | História, referências, problema operacional, usos e limites da simulação | Mudança de escopo ou atualização do contexto e das fontes |
| [Notebook principal](../../notebooks/fraud_detection_mlops.ipynb) | Sínteses, decisões e resultados que mudam a história do projeto | Conclusão de uma etapa substancial |
| `notebooks/stages/` | Investigação, decisões, contratempos e evidências de uma etapa | Na branch responsável pela etapa |
| `notebooks/experiments/` | Experimentos controlados e comparação reproduzível | Quando existirem benchmarks ou avaliações |
| [Data pipeline](../data/DATA_PIPELINE.md) | Origem, contratos, camadas, qualidade e proveniência | Mudança de fonte, schema, transformação ou armazenamento |
| [Operations](../operations/OPERATIONS.md) | Comandos, diagnóstico, recuperação e verificações | Mudança operacional |
| [EDA](../data/EDA.md) | Exploração, interpretação e proveniência dos relatórios | Mudança da análise |
| [Protocolo temporal](../modeling/EVALUATION_PROTOCOL.md) | Janelas, atraso de rótulos, população e métricas | Antes de mudar uma avaliação |
| [Contrato da Gold](../data/GOLD_CONTRACT.md) | Features, causalidade, cold start, splits e proveniência | Mudança de feature ou contrato de modelagem |
| [MLflow](../operations/MLFLOW.md) | Tracking nativo, persistência, assinatura e armazenamento | Mudança da integração ou resultados locais |
| [Baseline](../modeling/BASELINE.md) | Candidatos, métricas, ajuste, artefatos e fronteira de avaliação | Mudança de experimento ou resultado real |
| [Protocolo de experimentação](../modeling/EXPERIMENT_PROTOCOL.md) | Hipóteses, orçamento, análise estatística e gate de desenvolvimento | Congelar no Git antes de executar candidatos; mudanças exigem nova versão |
| [Execução das ablações](../operations/EXPERIMENT_EXECUTION.md) | Comandos e recuperação específicos do executor | Mudança do procedimento |
| [Diagnóstico](../modeling/DIAGNOSTICS.md) | Inspeção da validação, orçamento, interpretação e limites estatísticos | Mudança da análise ou resultados reais |
| [Contribuição de modelos](../modeling/CONTRIBUTING_MODELS.md) | Interface executável, regras de integração e exemplo | Mudança da interface ou entrada de contribuição |
| [Serving](../operations/SERVING_CONTRACT.md) | Contrato HTTP, exportação, execução e checks do runtime | Mudança do contrato ou procedimento |
| [Arquitetura](../ARCHITECTURE.md) | Responsabilidades, organização e controle de complexidade | Mudança estrutural |
| [Testing](../operations/TESTING.md) | Isolamento tox–Poetry, checks, seleção de testes e limites | Mudança de validação ou CI |
| `docs/operations/INFRASTRUCTURE.md` | Arquitetura, configuração, persistência e resiliência | Quando a infraestrutura for implementada |
| [Model Card](../modeling/MODEL_CARD.md) | Uso do modelo, dados, avaliação, limitações e governança | Nova avaliação ou mudança de uso; distinguir a síntese curada do arquivo operacional da run |
| [Stakeholders](STAKEHOLDERS.md) | Problema, entregas, evidências, limitações e próximos marcos | Mudança no resultado ou no escopo |
| `references/evidence/` | Recibos estruturados de evidência, ligados à revisão avaliada | Fechamento de um marco validado |
| `references/frozen_candidate_v1.json` | Recibo das escolhas, modelo e insumos da avaliação final | Criar na execução real e versionar antes de abrir o teste; não substituir para escolher outro candidato |
| `references/hgb_optuna_protocol_v1.json` | Autorização executável de janelas, reserva, espaço de busca, recursos e gates | Versionar antes de preparar dados ou executar a busca; mudanças geram outra identidade |
| `references/reference_assessment_protocol_v1.json` | Pergunta, referência fixa, janelas e interpretação estatística; nesta etapa autoriza somente preparar o plano | Commitar antes do acesso reservado; alterações nas regras exigem outra versão, preservando o snapshot v1 |
| `references/reference_assessment_execution_v1.json` | Autoriza executar exatamente a avaliação declarada, com acesso registrado e verificação dos resultados | Commitar com a implementação antes de `run`; preserva o protocolo estatístico v1 e mantém replay/novo treino fechados |

A Model Card foi criada com a avaliação final relatada pelo autor. Infraestrutura
e diretórios de experimentos entram quando houver conteúdo concreto. A Model Card
gerada na run permanece no armazenamento operacional; a síntese em `docs/` aponta
para sua evidência e conserva o escopo da revisão.

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
7. A narrativa relaciona decisões técnicas ao processo de investigação. Estatísticas
   externas indicam ano, região e instrumento de pagamento; a simulação não herda
   resultados ou cobertura desses contextos. Referências ficam no documento responsável.

Metas e estados futuros são mantidos no mural; outros documentos apontam para ele.
Um documento novo exige pergunta, público ou contrato distintos. Rascunhos
substituídos saem da navegação e da árvore ativa quando a decisão e a evidência
estiverem preservadas; o Git mantém o histórico. Recibos não são duplicados em
células executáveis ou em longas cronologias de cada guia.

Siglas são expandidas na primeira ocorrência em cada documento ou notebook que as
usa em sua narrativa. **HGB** significa **Histogram-based Gradient Boosting**;
o [dicionário](../data/DATA_DICTIONARY.md#siglas-da-modelagem-e-da-operação) reúne os termos.
Identificadores executáveis e arquivos de políticas congeladas conservam seus nomes.

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

A primeira aplicação desta política documenta a [etapa Bronze](../../notebooks/stages/01_bronze_ingestion.ipynb)
e o [recibo de evidências de 2026-10-06](../../references/evidence/bronze_2026-10-06.json).
