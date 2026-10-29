# Reprodução limpa na VM — estado em 2026-10-28

Registro derivado das saídas de terminal fornecidas pelo autor. Não é uma
execução do assistente no guest nem substitui os recibos originais. O estado
original do Alienware permanece separado.

## Ambiente

- Guest `fraud-repro`, Ubuntu Server 24.04.5 LTS, 6 vCPUs, 8 GiB de RAM configurados,
  disco virtual dinâmico de 120 GB; SSH via host 127.0.0.1:2222.
- Checkout `/home/wanderson/projetos/fraud-detection-mlops`, revisão
  `6c01e44d48f694c7218446f917491e3329029f83` na rodada inicial; implementação
  posterior na branch `feat/vm-session-portal`, até `0f7f277`. A branch foi enviada
  ao GitHub e recebida no Alienware; merge e CI desta branch não estão comprovados aqui.
- Python 3.14.4, Poetry 2.4.3, Docker 29.1.3, kind 0.33.0,
  kubectl 1.35.9, Helm 4.3.1.
- inotify: 512 instâncias e 524288 watches; configuração persistente instalada.
  Isso não constitui teste de reboot da aplicação.

## Resultados comprovados pelas saídas

| Etapa | Resultado |
| --- | --- |
| Fundação | PostgreSQL/RustFS Ready; PVCs Bound; kubeconfig global preservado |
| Tracking | Publicação de artefato S3, reload HTTP independente e recuperação após recriação do pod; fundação preservada |
| Bronze | 183/183 arquivos, completo, proveniência/integridade verificadas, bundle preparado |
| Silver/Gold | Jobs concluídos, outputs publicados, restauração e verificadores independentes; fundação preservada |
| Treino | Dummy, logística e HGB novos; 67.255 linhas na validação; reload verificado; teste reservado |
| Gate | HGB AP 0,6239485132514692 e precisão diária cliente@100 de aproximadamente 0,54; selecionado pela política existente |
| Registry | `fraud-detector` versão 1 e alias champion, após verificações de datasets/artefatos/métricas |
| API | Pesos identificados, score de controle e HTTP 422 verificados |
| Rollout | Blue/green na mesma URL, falha 502 observada e champion restaurado; observação de rollback ~1,94 s nesta execução |
| Fechamento da rodada | `canonical_round_complete`; não implica conclusão da reprodução completa |

Modelo da VM:
`401d7c36fc17bfd4176e506d45da2a559e0e2d44b7305af2f77c4cd8ac04ca67`.
Release blue: `308a1a82036a44e49d477a1b16747b6e`.
Essas identidades são diferentes das do ambiente original por serem uma execução
nova. Um score de controle coincidente não prova igualdade de todos os modelos.

Recibos a preservar dentro de `data/storage/kind-platform/`:

- Tracking: `tracking/rehearsal-3e2dd28916564be0ab16ea39ba6c3db3.json`.
- Primeiro ensaio de dados: `pipeline/rehearsal-d702922f911b4c438efe6f5a9b8e1151.json`.
- Rodada: `rounds/repro-v1/`, incluindo `round.json`, `data.json`, `training.json`,
  `promotion.json`, `serving.json`, `deployment.json`, `rollout.json` e verification.
- Run da cadeia vinculada à rodada: `0f7fbcf897db4ebfa57ce0fa318050ae`.
- Silver da rodada: `ada058c3881895e5a96b8c1ef6ad215f9ae12844e3dee6760932a3190f8e1372`.
- Gold citado no treino: `38c6d9ae97381f15ebb2ab4cfa65cabfa069897e670f780bca8afbdecfaf09bf`.

A ordem correta do fechamento canônico inclui `round-rollout` antes de
`round-verify`. O segundo depende do recibo `rollout.json`.

## Sessão e monitoramento verificados

A sessão `repro-arrival-200-v1` foi reconciliada com o champion e a release acima.
As saídas de apply/verify e as capturas fornecidas pelo autor confirmam:

| Indicador | Resultado |
| --- | --- |
| Eventos persistidos | 269.334 |
| Transações / feedbacks no ledger | 134.667 / 134.667 |
| Decisões HTTP / tentativas | 200 / 200 |
| Clientes / alertas | 199 / 13 |
| Feedbacks nas decisões / pendentes | 200 / 0 |
| Latência HTTP p50 / p95 / máxima | 29,3 / 44,8 / 57,3 ms |
| Linhas de features inválidas, futuras ou incoerentes | 0 |
| Precisão / recall por cliente-dia da coorte | 7,69% / 50% |
| AP transacional da coorte | 0,5058479532163742 |

SHA-256 da sessão:
`1a2ddc97d2350d8203234e074500ccec58c7ed5be236208dc235fce4ccc8f525`.
Os contadores do ledger incluem contexto e feedback; não são decisões do modelo.
A coorte contém duas transações fraudulentas e dois cliente-dias positivos,
um capturado e um não capturado. Não representa o dia completo ou validação global.

O perfil somente sessão reutiliza receiver, Prometheus/Grafana e dois PVCs,
sem instalar o replay histórico. Expiração e retomada da coleta foram verificadas.
Os cinco dashboards estão disponíveis: sessão e investigação, plataforma,
operação/feedback, saúde das features e qualidade da triagem. A verificação nativa
confirmou agregados e métricas sem suporte ausentes, com fontes, recibos, PVCs e
imagem do receiver preservados na revisão de apresentação.

As capturas confirmaram os cinco dashboards e, após a correção visual, as tabelas
Feature | Valor. Os painéis de drift permanecem sem suporte: exigem duas janelas
de 1.000 decisões sem sobreposição. Espera em fila, atraso de emissão e
scheduled-to-commit não foram medidos nesta sessão. Readiness dos oito pods não
comprova uptime ou SLA. Os 13 alertas são de triagem, não firing de alertas de
monitoramento.

Os recibos originais continuam no guest; os flags históricos de revisão visual
não foram reescritos. Este documento registra a revisão humana recebida no chat.
A exportação da sessão para o portal foi adaptada; não se atribui aceite integral
de desempenho/SHAP do portal da VM a essa adaptação.

## Gates pendentes

1. Consolidar supervisão do observador e acesso operacional após rollout.
2. Fechar desempenho, SHAP e aceite integral do portal da VM com fontes próprias.
3. Qualificar instalação completa do produto a partir de checkout vazio.
4. Definir e verificar baseline própria da VM quando os requisitos existirem.
5. Ensaiar stop/start, reboot e backup/restauração em gates separados.
6. Ensaiar novas chegadas e firing de alertas de monitoramento, se incluídos no escopo.

O relato antigo de 288.172 eventos da fonte em preparação não deve ser confundido
com os 269.334 eventos efetivamente vinculados à sessão aceita. O recibo específico
da preparação inicial precisa ser consultado antes de afirmar seu fechamento.

## Consolidação operacional posterior

Inventariar Deployments/StatefulSets, Jobs finitos e processos fora do cluster.
Para cada um: comando de operação, proprietário, identidade da imagem, entrada,
saída, logs, saúde, persistência, recursos e retomada. Escolher um supervisor para
cada processo recorrente; não ter dois controladores disputando o mesmo objeto.

Acesso local deve distinguir portas do Alienware e da VM. O SSH e o port-forward
são acessos do operador, não mecanismos de alta disponibilidade da aplicação.
Uma VM com um nó não demonstra tolerância à perda do host.
