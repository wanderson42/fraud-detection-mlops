# Roadmap — Fraud Detection MLOps

Atualizado em **2026-10-27** com aceite da jornada diária na branch posterior
ao PR #11. A integração `e53d3e3` e seu CI permanecem como marco histórico. O [guia operacional principal](../operations/PRODUCT_OPERATIONS.md)
concentra o percurso do operador. Os incrementos e resultados anteriores estão
preservados no [histórico do roadmap](ROADMAP_HISTORY.md).

## O produto que entregamos

Priorizar investigação com features causais, modelo identificado, política de
alertas, fila consultável, explicações verificadas quando disponíveis e feedback
posterior. O sistema transacional recebe pontuação; o analista recebe contexto;
a operação acompanha saúde e progresso; ML acompanha linhagem e atualização.
O modelo não assume sozinho a decisão sobre pagamentos.

## Marcos aceitos

| Entrega | Aceite e fonte |
| --- | --- |
| Dados e rodada canônica | Bronze/Silver/Gold identificados, três candidatos com Inputs, champion e API com os mesmos pesos; [rodada](../operations/OPERATIONAL_ROUND.md) |
| Governança de candidatos | Retenção nativa do champion nas rodadas fixa e de busca; [atualizações](../operations/MODEL_UPDATES.md) |
| Jornada diária pela API | 9.619 decisões/100 alertas, contexto causal, pausa/retomada, feedback e reconciliação; [aceite](../operations/LIVE_OPERATIONS.md#aceite-nativo-e-visual-final--2026-10-27) |
| Portal e transparência | Portal único aceito funcional/visualmente; sessão, 200 casos/cinco explicações e avaliação de 67.255 transações com origens explícitas; [portal](../operations/UNIFIED_PORTAL.md) |
| Monitoramento | Mesma sessão diária durante as chegadas no Prometheus/Grafana, expiração/retomada e API disponível; [operação](../operations/SESSION_MONITORING.md) |
| Ciclo de vida | Parada/subida canônica no mesmo node/volumes; reboot planejado no escopo histórico registrado; [runbook](../operations/KIND_OPERATIONS.md) |
| Simplificação | Entradas Make por finalidade, IO/CRI compartilhados, importador concluído e serving sintético aposentados; [revisão](../operations/CODEBASE_SIMPLIFICATION.md) |
| Footprint | API e MLflow reduzidos, aplicados e verificados; treino reduzido e qualificado em ambiente isolado; [medidas](../operations/IMAGE_FOOTPRINT.md) |
| Integração | PR #11 integrado; 1.788 testes locais e 44 testes de UI aprovados, CI da branch e da main aprovados |

A baseline corrente é `baseline-arrivals-day-v1.json`; confira o caminho e o
SHA no [guia operacional](../operations/PRODUCT_OPERATIONS.md). O aceite diário
inclui chegadas simultâneas verificadas pelo gate, AppTest durante a produção e
revisão visual humana do estado final do portal. Não acrescenta stop/start,
reboot, firing de notificação ou confirmação independente de qualidade.

## Backlog ordenado

| Ordem | Recorte | Critério de conclusão |
| --- | --- | --- |
| 1 — próximo | Reprodução em ambiente limpo | Instalação e jornada reproduzidas em destino isolado, sem remover o ambiente aceito; fechar lacunas efetivamente encontradas |
| 2 — posterior | Terraform | Declarar o ambiente estabilizado, com providers/locks/state protegidos e plano/apply conferidos; reutilizar Helm |
| Condicionado | Sucessor do champion | Candidato qualificado, aprovação, ativação/rollback com pesos diferentes e recuperação coerente no KinD; gates permanecem inalterados |

## Próxima entrega concreta

Concluir revisão, CI e integração da branch da jornada diária. Em seguida,
definir e executar uma reprodução limpa em destino isolado, preservando o
cluster atual. O aceite exigirá fontes/dependências identificadas, instalação,
API, dados, tracking, portal e métricas coerentes, com relatório das etapas e
das lacunas encontradas. Não é uma autorização para apagar ou substituir o
ambiente já aceito. A janela reutilizada demonstra operação, não novo holdout.

## Simplificação após maturação da infraestrutura

As remoções, consolidação de comandos, compartilhamento de IO/CRI e runtimes
medidos foram integrados. A documentação principal passa a separar estado atual
e histórico; [critérios e registros anteriores](ROADMAP_HISTORY.md#simplificação-após-maturação-da-infraestrutura)
continuam disponíveis. Novas remoções ou otimizações dependem de redundância ou
gargalo verificado, sem reabrir os aceites concluídos.

A análise histórica de CT, orquestração, feature store e broker está em
[ROADMAP_HISTORY.md](ROADMAP_HISTORY.md#análise-de-adoção-ct-orquestração-feature-store-e-broker).
Essas opções não são obrigações do próximo recorte. A imagem de treino otimizada
continua aguardando uso em nova rodada nativa; não repetimos uma rodada histórica
apenas para fabricar esse aceite.
