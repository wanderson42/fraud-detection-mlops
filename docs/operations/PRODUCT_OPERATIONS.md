# Operação do produto de detecção de fraude

Atualizado em **2026-10-27**, com aceite nativo da jornada diária na branch
posterior ao PR #11; o CI da main do PR #11 permanece como marco histórico.
Este é o guia principal de operação. Os runbooks especializados detalham os
comandos; o [roadmap](../project/ROADMAP.md) registra prioridades e aceites.

## Estado atual

| Entrega | Estado |
| --- | --- |
| Dados e modelos | Bronze/Silver/Gold com contratos e linhagem; rodada canônica e champion identificado na API aceitos |
| Produto | Sessão diária de 9.619 decisões/100 alertas, pausa/retomada e feedback reconciliados; portal único com revisão visual final aceita |
| Transparência | 200 casos de evidência, cinco explicações verificadas e avaliação de 67.255 transações com modelo/dataset/política identificados |
| Monitoramento | Mesma sessão diária acompanhada durante chegadas no Prometheus/Grafana; expiração/retomada com API disponível aceitas |
| Runtimes | API e MLflow menores, aplicados e verificados no KinD; imagem de treinamento qualificada em ambiente isolado |
| Integração | PR #11 integrado em `e53d3e3`; testes locais e CI aprovados |
| Próximo aceite | Reprodução em ambiente limpo e isolado, preservando este ambiente aceito |

O baseline corrente é
`data/storage/kind-platform/operations/baseline-arrivals-day-v1.json`, SHA-256
`62152f69c976f4a4c965d04823eb1f9bea41640b960e86f8dd241e1a8f5226ba`.
O [recibo da jornada](../../references/evidence/live_operations_author_validation_2026-10-27.json)
registra o relatório nativo, a confirmação visual separada e o observador
reiniciado pelo operador. As baselines e sessões anteriores são históricas e
permanecem preservadas. Não há novo aceite de stop/start ou reboot.

## Usuários e entregas conectadas

| Usuário | Entrega no projeto |
| --- | --- |
| Sistema transacional | API de pontuação com contrato, pesos e release identificados |
| Analista de fraude | Portal Streamlit: fila priorizada, contexto, triagem, explicação disponível e feedback posterior |
| Equipe de operação | Grafana: disponibilidade, coleta, progresso, erros e indicadores de monitoramento |
| Equipe de ML | MLflow e fluxo governado: linhagem, comparação, qualificação e implantação controlada |
| Gestor de risco | Visão resumida da operação, métricas avaliadas e capacidade de investigação |

O produtor recebe transações, monta features disponíveis naquele instante, chama
a API e persiste decisões/alertas. A política determina o encaminhamento; o modelo
fornece ranking, não um veredito automático. O analista consulta o que chegou;
não precisa solicitar novos dados. Iniciar, pausar e retomar a demonstração são
responsabilidades do operador. Feedback posterior não participa retroativamente
da decisão ou da explicação.

MLflow fornece linhagem e Registry. O portal lê casos e evidências. Grafana lê
métricas. Pesos/release, datasets e identidades das sessões ligam essas entregas.
A interface não carrega modelos, treina, altera tráfego ou administra o cluster.

## Comandos por finalidade

| Finalidade | Entrada |
| --- | --- |
| Inspeção | `platform-status`, `platform-host-check`, `platform-check` |
| Acessos em segundo plano | `access-up`, `access-status`, `access-logs`, `access-stop-all` |
| Portal único | `portal-session-link`, `portal-session-verify`, `portal-open` |
| Sessão | `session-prepare-expanded`, `session-run`, `session-pause`, `session-feedback`, `session-verify` |
| Observação | `session-monitor-prepare`, `session-monitor-up`, `session-monitor-watch` |
| Jornada diária integrada | `session-daily-prepare`, `session-live-test` |
| Treinar e atualizar modelos | Famílias `round-*` e atualização governada; [MODEL_UPDATES.md](MODEL_UPDATES.md) |
| Parada/subida planejada | `platform-stop`, `platform-start`, `platform-verify`; [KIND_OPERATIONS.md](KIND_OPERATIONS.md) |
| Instalação e desenvolvimento | `make help-all`, [KIND_PLATFORM.md](KIND_PLATFORM.md), `validate`, `portal-ui-test` |

Make centraliza as entradas; os CLIs aplicam locks, ownership, checagens e recibos.
Helm descreve os workloads. O acesso `platform-api` verifica o deployment ativo,
Registry, imagens e HTTP; não escolhe um algoritmo ou uma rodada antiga por nome.
`round-api` permanece como compatibilidade para esse acesso canônico.

`access-up` abre API, MLflow, Grafana e portal com logs privados e processos
identificados. Parar um acesso não para seu workload. [LOCAL_ACCESS.md](LOCAL_ACCESS.md)
explica portas, logs e encerramento. O portal diário é o mesmo Streamlit em 18501;
a entrada independente `session-open` é de compatibilidade.

## Iniciar, observar e encerrar

Com a plataforma já ativa:

```bash
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-arrivals-day-v1.json &&
make access-up &&
make access-status
```

Se já estiver operando outra configuração aceita, use seu baseline específico.
Para a sessão diária já concluída, `session-monitor-watch
SESSION_MONITOR_ID=arrivals-day-v1` renova a coleta; não cria eventos. Depois de
vincular outra sessão, use seu ID. Sem o observador, a fonte expira em até 30 s.

Para uma nova jornada diária, siga [LIVE_OPERATIONS.md](LIVE_OPERATIONS.md):
preparar dados, trocar vínculos explicitamente, reabrir acessos, salvar baseline
e executar o ensaio. Não inicie um produtor em uma sessão reservada para o gate
antes de executá-lo. O guia registra o ID, a janela e os pré-requisitos de cada fase.

Para interromper a plataforma, pare produtores/observadores e acessos, declare
a ausência de writers e siga o ciclo de vida com o baseline correto.
`platform-check` confere o estado corrente. `platform-verify` exige o ciclo
controlado de parada/subida; um check não comprova reboot ou recuperação de host.
Os volumes e recibos não são removidos para iniciar outra demonstração.

## Dados, evidências e qualidade

O Handbook contém 183 dias de dados simulados. Sessões reproduzem eventos em
ordem causal, com ritmo configurável e feedback atrasado. Bronze/Silver/Gold
preparam e identificam os datasets; não são reconstruídos integralmente para
cada requisição de inferência. As features da sessão vêm do estado causal.

Reutilizar uma janela observada demonstra operação; não cria dados independentes
para confirmar qualidade. Os 200 casos de evidência, a sessão corrente e a avaliação
de 67.255 transações têm fontes e períodos próprios, identificados no portal.
SHAP só é reutilizado por identidade exata de request/response, features, pesos e
release. Ausência de explicação compatível não impede consultar o caso.

Os limiares de qualidade e promoção são específicos do laboratório. Custos de
fraude/falsos positivos, capacidade de investigação e incerteza das métricas
precisariam calibrá-los em outra operação. Ganho de 0,005 de AP, sozinho, não
comprova significância estatística. Não se afirma economia financeira ou desempenho
humano equivalente sem medição.

## Próximos aceites

A jornada diária foi aceita com produção, UI automatizada e métricas simultâneas,
seguida de revisão visual final do portal. O relatório automatizado original não
foi alterado para registrar essa revisão humana.

A próxima entrega é reproduzir instalação e jornada em ambiente limpo, isolado
do cluster e dos dados aceitos. Depois, sobre esse fluxo validado, Terraform
consolidará a IaC. O CI e a integração desta branch continuam sendo gates próprios.

Promoção e rollback de sucessor com pesos diferentes permanecem condicionados a
candidato qualificado e aprovação explícita. Não é necessário buscar um vencedor
artificial, afrouxar gates ou adicionar broker/feature store para este recorte.
