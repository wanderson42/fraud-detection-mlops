# Reprodução limpa na VM — estado em 2026-10-29

Registro derivado das saídas de terminal fornecidas pelo autor. Não é uma
execução do assistente no guest nem substitui os recibos originais. O estado
original do Alienware permanece separado.

## Ambiente

- Guest `fraud-repro`, Ubuntu Server 24.04.5 LTS, 6 vCPUs, 8 GiB de RAM configurados,
  disco virtual dinâmico de 120 GB; SSH via host 127.0.0.1:2222.
- Checkout no guest, revisão
  `6c01e44d48f694c7218446f917491e3329029f83` na rodada inicial; implementação
  posterior na branch `feat/vm-session-portal`, corrigida até `b773479` e integrada
  no PR #13 em `66e2bbf`. Autor confirmou a main atualizada em ambas as máquinas.
  A supervisão posterior está na branch `feat/vm-user-supervision`, com commit
  inicial `22eebf9`; o SHA da correção TIME-WAIT não foi fornecido nesta evidência.
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

## Supervisão dos processos do host — 2026-10-29

A inspeção inicial mostrou systemd e o gerenciador de usuário funcionando,
um watch manual e o encaminhamento Grafana em segundo plano, com `Linger=no`.
Os outros acessos `not_started` descreviam encaminhamentos locais, não pods
indisponíveis. A instalação criou duas unidades sem ativá-las. Após a troca
explícita dos processos e o comando de configuração de Linger, o check retornou
`vm_supervision_same_session_verified`.

| Serviço | PID inicial supervisionado | PID após falha controlada | Resultado |
| --- | ---: | ---: | --- |
| Observador da sessão | 506569 | 512559 | Ativo; contador de reinícios aumentou |
| Acesso Grafana | 506570 | 514819 | Ativo; contador de reinícios aumentou e HTTP saudável |

O ensaio `vm_user_service_recovery_verified` encerrou um processo principal por
vez e verificou reinício automático e métricas da mesma sessão. O check posterior
manteve os PIDs 512559/514819, ambos com `NRestarts=1`, fonte/modelo/release,
contagens e hashes dos cinco arquivos aceitos. Recursos protegidos permaneceram
iguais e nenhuma nova inferência foi executada. PIDs são observações desta
execução, não identidades para comandos futuros de encerramento.

O procedimento de logout foi orientado, mas o material recebido não inclui prova
de que todas as conexões SSH e túneis foram fechados, nem uma leitura posterior
de `Linger=yes`. O check posterior sustenta continuidade entre leituras. Os flags
`logout_persistence_validated=false` e `reboot_validated=false` foram preservados;
não converter esse check em aceite automatizado de logout ou reboot.

O [runbook de supervisão](VM_USER_SUPERVISION.md) é a referência dos comandos.
O diário registra essa entrega na branch; o CI #70 do PR #13 antecede a supervisão
e não é evidência de aprovação desse novo código.

## Decisões e contratempos da reprodução

| Descoberta | Correção e aprendizado |
| --- | --- |
| Checkout versionado não continha os estados/recibos do laboratório | Produzir vínculos e evidências próprios da VM; não copiar ownership nem fabricar recibos legados |
| Sessão reconciliada não possuía o ensaio histórico exigido pelo portal | Adaptar o aceite/exportação à sessão real, preservando contratos científicos |
| Dashboards legados dependiam de replay concluído | Criar perfil somente sessão e reaproveitar receiver, Prometheus/Grafana e PVCs |
| 200 decisões não sustentavam duas janelas independentes de 1.000 | Exibir drift sem suporte; manter saúde das features e qualidade limitada à coorte |
| Recebimentos HTTP não continham fila/atraso de emissão históricos | Deixar métricas não medidas indisponíveis, sem sintetizar zeros |
| Rollout substituiu pods dos encaminhamentos | Separar workload, port-forward da VM e túnel SSH do Alienware |
| Credencial correta na API e login manual inválido | Conferir instância e credencial própria da VM, sem redefinir senha antes do diagnóstico |
| Segundo observador encontrou lock ocupado | Reutilizar o processo existente; nunca apagar o lock para contornar exclusão |
| Testes misturaram perfil local da VM e proprietário simulado | Isolar fixtures dos arquivos operacionais do host |
| CI falhou em 15 testes históricos de transições | Incluir a cadeia de transições nas fixtures e comparar hashes com bytes correspondentes; preservar rejeição de mudanças científicas |
| Grafana pai continuava vivo após perda do port-forward | Acrescentar health check ao acesso supervisionado, além do reinício por saída do processo |
| Preflight recusou porta sem listener, apenas em TIME-WAIT | Corrigir o probe de bind com SO_REUSEADDR; teste real preserva recusa de listener ativo, sem alterar sysctl |

## Código, testes e evidências

Os CIs #66, #67 e #68 falharam antes da correção de fixtures; logs de #66 e #68
consultados mostraram 15 falhas, não encerramento por timeout. O teste direcionado
corrigido passou com 51 testes. A suíte completa da preparação passou com
1.852 aprovados, 59 pulados, 166 warnings e 12 subtests, em Python 3.14.4.
Os skips dependem das ferramentas disponíveis; esse total não é apresentado como
contagem do runner GitHub. O [CI #70](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/40864553711)
aprovou qualidade/testes, qualificação da imagem e consumo em outro runner.
O [PR #13](https://github.com/wanderson42/fraud-detection-mlops/pull/13) foi integrado.

Para supervisão: 15 testes no tox foram informados pelo autor na VM; a revisão
TIME-WAIT teve 18 testes aprovados na preparação, incluindo sockets reais.
A recuperação dos dois serviços é evidência nativa fornecida pelo autor, distinta
desses testes. O [recibo documental](../../references/evidence/vm_reproduction_and_supervision_author_validation_2026-10-29.json)
reúne a origem e o escopo. Não substitui os recibos e bancos locais.

## Gates pendentes

1. Integrar supervisão e esta documentação com CI próprio; concluir inventário e acessos restantes.
2. Fechar desempenho, SHAP e aceite integral do portal da VM com fontes próprias.
3. Qualificar instalação integral do produto a partir de checkout vazio.
4. Definir/verificar baseline própria da VM quando seus pré-requisitos existirem.
5. Ensaiar logout controlado, ciclo completo, reboot e backup/restauração separadamente.
6. Ensaiar novas chegadas e firing de alertas de monitoramento se incluídos no escopo.

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
