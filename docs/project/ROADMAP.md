# Roadmap — Fraud Detection MLOps

Atualizado em **2026-10-27** após integração do PR #11 (`e53d3e3`) e CI da main
aprovado. O [guia operacional principal](../operations/PRODUCT_OPERATIONS.md)
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
| Sessão causal pela API | Pausa/retomada, feedback, reconciliação e 2.001 decisões; [escala](../operations/PRODUCT_SESSION_SCALE.md) |
| Portal e transparência | Portal único aceito funcional/visualmente; sessão, 200 casos/cinco explicações e avaliação de 67.255 transações com origens explícitas; [portal](../operations/UNIFIED_PORTAL.md) |
| Monitoramento | Mesma sessão concluída no Prometheus/Grafana, expiração/retomada e receiver corrigido; [operação](../operations/SESSION_MONITORING.md) |
| Ciclo de vida | Parada/subida canônica no mesmo node/volumes; reboot planejado no escopo histórico registrado; [runbook](../operations/KIND_OPERATIONS.md) |
| Simplificação | Entradas Make por finalidade, IO/CRI compartilhados, importador concluído e serving sintético aposentados; [revisão](../operations/CODEBASE_SIMPLIFICATION.md) |
| Footprint | API e MLflow reduzidos, aplicados e verificados; treino reduzido e qualificado em ambiente isolado; [medidas](../operations/IMAGE_FOOTPRINT.md) |
| Integração | PR #11 integrado; 1.788 testes locais e 44 testes de UI aprovados, CI da branch e da main aprovados |

A baseline aceita em 2026-10-27 é `baseline-session-counts-v1.json`. Ela confere
configuração e readbacks correntes; não acrescenta um novo ensaio de reboot.
Os aceites mantêm seus escopos: uma sessão concluída monitorada não comprova
chegadas simultâneas; manutenção de runtime não promove um sucessor de modelo.

## Backlog ordenado

| Ordem | Recorte | Critério de conclusão |
| --- | --- | --- |
| 1 — preparado | Jornada diária integrada | Um dia completo disponível pela API; portal e métricas avançando simultaneamente; pausa/retomada, expiração da coleta com API disponível, feedback causal e reconciliação final; [ensaio](../operations/LIVE_OPERATIONS.md) |
| 2 — posterior | Reprodução em ambiente limpo | Instalação e jornada reproduzidas em destino isolado, sem remover o ambiente aceito; fechar lacunas efetivamente encontradas |
| 3 — posterior | Terraform | Declarar o ambiente estabilizado, com providers/locks/state protegidos e plano/apply conferidos; reutilizar Helm |
| Condicionado | Sucessor do champion | Candidato qualificado, aprovação, ativação/rollback com pesos diferentes e recuperação coerente no KinD; gates permanecem inalterados |

## Próxima entrega concreta

A implementação do ensaio diário está preparada. O aceite nativo depende da
execução pelo autor, seguido da conferência visual no portal e Grafana. O ensaio
usa uma sessão nova e preserva as sessões de 200 e 2.001 decisões e seus recibos.
Não exige novo modelo, broker, feature store ou orquestrador.
A janela já observada é uma demonstração operacional, não um holdout inédito.

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
