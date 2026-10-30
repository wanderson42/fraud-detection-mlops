# Reprodução limpa na VM — estado em 2026-10-29

## Estado indispensável e próximo gate — revisão de 2026-10-30

O PR #19 está integrado em `a0a96f6`; o autor informou sincronização da main no
Alienware e na VM. O [contrato de estado](VM_STATE_AND_CLEAN_INSTALL.md) classifica
tracking, datasets, sessão, governança, relatórios, métricas e configuração privada.
Essa revisão não coletou novo inventário nem executou backup/restore.

O próximo gate é um percurso integral em ambiente novo isolado, com recibos e
credenciais próprios. A fonte padrão `data/replay/paced-native-v1` não vem no Git:
seu produtor precisa estar explícito e verificado antes de `session-prepare`.
Preservar o cluster, os PVCs e a sessão aceitos; não recriá-los para testar instalação.
Recuperação integral e perda física do host continuam fora do aceite.

## Aceite do ciclo planejado na VM — 2026-10-29

O autor aplicou a correção em `92ba7b3`, na branch `fix/vm-lifecycle-profile`,
e informou 113 testes aprovados em 2,88 s (tox 6,65 s). Depois criou a baseline,
executou parada/subida/verificação e retomou os serviços do host e o portal.
A evidência é a saída nativa fornecida pelo autor; não é execução do assistente.
CI e merge dessa correção ainda não são afirmados neste registro.

| Recibo | Identidade |
| --- | --- |
| Baseline | `baseline-vm-session-shap-v1.json` |
| SHA-256 da baseline | `ea4cd947fe236b7ffa42f34ee3174c16319ef056957546545c3d35a000aa9aac` |
| Parada | `stop-212763fa4ca245d3b12eebc206518ed7.json`, status stopped |
| Subida | `start-0a7bd68b097d403fa6f3c3890bc1123c.json`, status started |
| Verificação | `verify-cddcb0783a764b67bfecd04155ad6b46.json`, status verified |

Os arquivos ficam em `data/storage/kind-platform/operations/`, fora do Git.
O recibo documental aponta para as identidades acima sem substituir os originais.
O nó foi parado de fato e retomado com o mesmo container, node UID e engine;
os quatro volumes e o plano de réplicas foram preservados, incluindo green em zero.

A verificação repetiu os readbacks de PostgreSQL/RustFS, histórico MLflow de dois
pontos, objeto S3 de controle e API do champion versão 1/blue. A API manteve modelo
`401d7c36fc17bfd4176e506d45da2a559e0e2d44b7305af2f77c4cd8ac04ca67`, release
`308a1a82036a44e49d477a1b16747b6e`, score de controle 0,4178630794049542 e HTTP 422
para entrada inválida. O objeto S3 de teste do tracking tem identidade própria;
não é apresentado como os pesos do champion.

O perfil somente sessão confirmou scrape, duas regras saudáveis, proxy Grafana
e dashboard. Os serviços systemd foram retomados explicitamente: observador PID
181897 e acesso Grafana PID 181898, ambos active/running e NRestarts=0; HTTP saudável.
A sessão manteve cinco hashes aceitos e recursos protegidos, com 269.334 eventos,
200 decisões, 199 clientes, 13 alertas e 200 feedbacks nas decisões. O portal
investigation-shap-v1 voltou em 18501 (PID 184638); smoke confirmou cinco explicações.

Houve inferências HTTP de controle durante o ciclo, fora do ledger da sessão.
O `new_inferences_executed=false` do check do observador refere-se àquela
verificação da sessão, não apaga as inferências de controle. Não houve fit, replay,
promoção ou migração primária. O boot ID permaneceu
`24524ae4-b9a4-464b-8f1a-a1319ffc2acd`: este ensaio não incluiu reboot.
Os flags de reboot, restauração, firing e revisão visual dos recibos originais
permanecem false. A revisão visual do portal anterior não é refeita pelo smoke.
Logout/reboot já tinham evidências separadas; backup/restauração, perda do host e
instalação integral continuam fora deste aceite. Não se infere SLA destes tempos.

[Recibo documental](../../references/evidence/vm_lifecycle_author_validation_2026-10-29.json).


## Inventário operacional e preparação do ciclo — 2026-10-29

Fonte: saídas de terminal fornecidas pelo autor. O inventário distinguiu os
workloads Kubernetes, serviços do host e acessos locais; não mediu consumo por
container nem ensaiou parada/subida.

| Camada | Estado observado |
| --- | --- |
| Controllers | Sete Deployments e dois StatefulSets; blue/gateway e seis serviços ativos; green em 0 |
| Pods | Oito Running/Ready; contadores de reinício não atribuem causa por si só |
| Jobs/CronJobs | Nenhum listado no namespace nesse instante; não significa ausência de execuções históricas |
| Persistência | PostgreSQL 2 GiB, RustFS 4 GiB, Grafana 512 MiB e Prometheus 1 GiB; quatro PVCs Bound |
| Requests contínuos | 825m CPU e 1.216 MiB memória; excluem init containers, sistema Kubernetes e processos do host |
| Nó | Seis CPUs e 8.131.140 Ki de memória alocável informados pelo Kubernetes |
| Host | 7,8 GiB RAM, 4,9 GiB disponíveis, swap usada 780 KiB; disco 27% ocupado, 81 GiB disponíveis |
| Host supervisionado | Observador PID 4455 e acesso Grafana PID 5026, ambos active/running e NRestarts=1; HTTP Grafana saudável |
| Portal | Acesso pronto em 18501, PID 84787; permanece no modo de acesso manual, sem aceite de persistência após reboot desse processo |
| Demais acessos | Fechados; registro manual antigo do Grafana stale, sem invalidar o serviço systemd atual |
| Métricas de consumo | Indisponíveis; nenhum metrics server instalado por esta coleta |
| Ownership/inotify | platform-status inspected, mesmo boot 24524ae4-b9a4-464b-8f1a-a1319ffc2acd; 512 instâncias e 524288 watches suficientes |

A soma de requests não é consumo nem orçamento completo da VM. Capacidade de PVC
não é ocupação real. A leitura não indica pressão imediata de memória/disco, mas
não sustenta ajuste de limits ou conclusão sobre picos de treino.

A tentativa de baseline-vm-session-shap-v1 falhou em
`dashboard_color_policy_verified`, por exigir no perfil somente sessão um campo
de retorno exclusivo do replay. A correção preparada exige as confirmações do
perfil real e transição exata de fontes. O comando não chegou a stop/start; o
aceite funcional ficou inicialmente pendente e foi encerrado pelo ciclo registrado acima. [Diagnóstico e procedimento](KIND_OPERATIONS.md#perfil-somente-sessão-na-vm--correção-preparada-em-2026-10-29).

## Aceite nativo de SHAP e portal da sessão — 2026-10-29

Origem: saídas da VM fornecidas pelo autor e captura revisada no chat. Código
commitado pelo autor em `364f099` na branch `feat/vm-session-shap`; não foi
coletado o SHA completo do checkout durante cada comando. A aprovação de CI e o
merge desta branch não são afirmados por este registro.

| Artefato | Identidade / resultado |
| --- | --- |
| Relatório SHAP `repro-session-shap-v1` | Cinco casos publicados e verificados, mesmo modelo/release da sessão |
| Manifest SHAP | `3ea0243626ced1766c4a11b57eed6639512d24496cf7eb4d250631ea5df9d919` |
| Portal `investigation-shap-v1` | 200 decisões, 199 clientes, 13 alertas, 200 feedbacks e cinco explicações |
| Manifest do snapshot | `b04e55c868ba7179a588f2e26761043d872b87c0df81d7ad7348390aeccdbda3` |
| Desempenho `repro-shap-validation-v1` | 67.255 linhas da validação já observada, vinculadas ao novo snapshot |
| Manifest do desempenho | `29453b640e62f921414f5897ed9e8c2f8a798f8271c6b81fbd3be62f692ea31d` |
| Gate funcional | portal-unified: 1 passed em 1,09 s; tox 15,94 s |

O gate nativo de Streamlit navegou pela sessão, paginação, visão geral e desempenho,
comparando os hashes antes/depois. O smoke confirmou HTTP e fontes. A captura
mostra sessão concluída 200/200, 13 alertas, 199 clientes, 200 feedbacks e o caso
1610254 com score 0,999225, gráfico SHAP, origem verificada e reconstrução do score.
O feedback posterior aparece separado, com aviso de que não participa da explicação.
A inspeção visual desta captura cobre essa página/caso; a página de desempenho foi
validada pelo gate, sem atribuir sua revisão visual a uma imagem que não a mostra.

Os testes direcionados do autor passaram com 104 testes, 10 skips, 13 warnings e
12 subtests em 46,07 s, tox 52,47 s. Não são contagens do CI nem testes pulados
apresentados como aprovados. O gate nativo não carregou modelos; a geração SHAP
e a avaliação de desempenho anteriores carregaram os pesos identificados para
seus cálculos. O champion permaneceu inalterado.

Os flags originais UI_browser_review_validated/browser_visual_review_validated
permanecem false. A revisão da captura é evidência complementar; o gate não valida
novas chegadas. Casos fora dos cinco selecionados mantêm SHAP indisponível.
Este aceite cobre o portal da sessão concluída; não é instalação integral,
restauração, alta disponibilidade ou nova confirmação independente de qualidade.
O snapshot investigation-v1 permanece como referência anterior preservada.

[Recibo documental](../../references/evidence/session_shap_author_validation_2026-10-29.json).


## Portal: snapshot original e candidato com SHAP aceitos

Saídas fornecidas pelo autor confirmam `portal_snapshot_verified`,
`portal_session_readonly_verified`, `portal_performance_verified` e smoke HTTP.
O autor confirmou a conferência visual, incluindo a mensagem correta de SHAP
indisponível. O flag original `UI_browser_review_validated=false` permanece intacto.

| Artefato | Resultado |
| --- | --- |
| Portal `investigation-v1` | 200 decisões, 199 clientes, 13 alertas, 200 feedbacks, 0 explicações |
| Manifest do snapshot | `ec6a658a32c7f0edb6a0e34dca29659938f5d1b593d2d5e4ec5964babe20578d` |
| Sessão | `repro-arrival-200-v1`, mesmos SHA/modelo/release registrados abaixo |
| Desempenho `repro-validation-v1` | 67.255 linhas; rodada `repro-v1`; mesmo modelo do snapshot |
| Manifest do desempenho | `447ee844dc2c841c50852e5fe097cd94e6a620d1ead5c41668621a4c502bc7b0` |
| Runtime da interface | Ambiente separado instalado; ambiente científico preservado |

O primeiro prepare usou por engano o ID da sessão como ID do fluxo. A correção
foi `PRODUCT_FLOW_ID=session-repro-arrival-200-v1`, conservando
`PORTAL_SESSION_ID=repro-arrival-200-v1`; nenhum arquivo foi copiado ou renomeado.
O desempenho recalculou a validação já identificada, sem fit ou promoção.

O gerador SHAP exigia `rehearsal.json`, ausente por desenho na sessão reconciliada.
A adaptação preparada usa a reconciliação existente em memória e os recibos reais,
sem sintetizar o ensaio legado. O candidato `investigation-shap-v1` preservou o
snapshot anterior e recebeu desempenho vinculado ao novo manifest. Geração,
verificação, smoke e navegação nativos passaram; a captura do caso explicado foi
revisada no chat, conforme o aceite complementar abaixo. [Procedimento](CASE_EXPLANATIONS.md#sessão-reconciliada-na-vm--preparação-em-2026-10-29).


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
  A supervisão posterior, iniciada em `22eebf9`, foi integrada pelo PR #14 em
  `9d9c8f25d32747cd406399a45b43d98da06d1c78`. O autor confirmou CI aprovado
  e sincronização antes dos ensaios abaixo; o SHA exato do checkout durante cada
  comando nativo não foi coletado separadamente.
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

### Persistência após logout completo

O autor forneceu `Linger=yes` e check inicial com os PIDs 512559/514819,
ambos com `NRestarts=1`. Confirmou explicitamente o encerramento de todas as
conexões SSH, túnel Grafana e sessão no console, mantendo a VM ligada. Depois da
reconexão, o check manteve os mesmos PIDs, contadores, hashes e contagens, com
Grafana saudável. Aceite manual composto: confirmação do procedimento e saídas
antes/depois; não é detecção automática de ausência de sessões pelo verificador.

### Recuperação após reboot planejado

| Observação | Resultado fornecido pelo autor |
| --- | --- |
| Boot ID anterior | `6aebb2b3-58fc-441e-ad0e-badeecbbb5bb` |
| Boot ID posterior | `24524ae4-b9a4-464b-8f1a-a1319ffc2acd` |
| Observador | PID 4455, active/running, NRestarts=1 |
| Acesso Grafana | PID 5026, active/running, NRestarts=1, HTTP saudável |
| Identidade e contagens | Mesma sessão/modelo/release; 269.334 eventos, 200 decisões e 13 alertas |
| Preservação | Cinco hashes aceitos e recursos protegidos iguais; nenhuma nova inferência |

O autor executou `sudo reboot`, reconectou e forneceu boot ID/check, sem relatar
início manual dos serviços. O journal do boot mostra a sequência abaixo. Horários
são os exibidos pelo guest, sem conversão de fuso; o extrato não informa offset.

| Horário no journal | Observação |
| --- | --- |
| 15:33:26 | systemd iniciou as duas unidades |
| 15:33:32 | Comandos kubectl falharam com exit 1; observador encerrou; detalhes retidos pelo programa |
| 15:33:36 | Supervisor Grafana encerrou após falha do acesso foreground |
| 15:33:42 | systemd reiniciou o observador, contador 1 |
| 15:33:46 | Observador anunciou a mesma sessão; systemd reiniciou acesso Grafana, contador 1 |
| 15:33:49 | Encaminhamento publicou URL no loopback da VM |
| 15:33:51 | Supervisor anunciou grafana_access_ready |

A falha inicial de kubectl é compatível com dependências do cluster ainda
iniciando, mas sua causa específica não foi exposta. O aceite é de recuperação
automática após reboot com nova tentativa bem-sucedida, não de primeira tentativa
sem falhas. Não há medida de SLA, restauração de backup ou perda do host.

Os campos `logout_persistence_validated=false` e `reboot_validated=false` nas
saídas originais foram preservados. O comando verifica o estado corrente; os
aceites externos dependem da confirmação de logout e da comparação dos boot IDs,
checks e journal. O novo [recibo documental de logout/reboot](../../references/evidence/vm_logout_reboot_author_validation_2026-10-29.json)
registra essa composição sem reescrever recibos operacionais nem a evidência anterior.

O [runbook de supervisão](VM_USER_SUPERVISION.md) concentra os comandos.
O merge do PR #14 foi consultado no GitHub; sua aprovação de CI foi informada
pelo autor. O CI #70 do PR #13 antecede a supervisão e não valida esse código.

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

1. Concluir inventário dos processos/acessos restantes e medição de consumo; supervisão integrada e logout/reboot aceitos em evidências próprias.
2. Qualificar instalação integral do produto a partir de checkout vazio; portal/SHAP já integrado no PR #16 e correção do ciclo no PR #17.
3. Registrar o [aceite da cópia fora da VM devolvida/restaurada](VM_TRACKING_BACKUP.md#restauração-da-cópia-fora-da-vm--2026-10-30); delimitar dados indispensáveis à continuidade e o que é reconstruível. Recuperação integral e perda do SSD físico continuam abertas.
4. Ensaiar novas chegadas e firing de alertas de monitoramento se incluídos no escopo.

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

## Preparação do backup do tracking — 2026-10-29

O autor confirmou `mlflow_kind` com 65 tabelas públicas e 10.958.515 bytes;
`fraud_platform` vazio é outro banco. Os quatro PVCs são hostPath com política
Delete. O incremento prepara dump + artefatos MLflow com pausa limitada do
tracking, guardas da origem e restauração em projeto Compose temporário, sem
substituir o KinD. Os 269.334 eventos/200 decisões são preservados por hashes,
mas seus SQLite não estão incluídos nesse backup. O [contrato operacional](VM_TRACKING_BACKUP.md)
separa integridade, restaurabilidade e proteção contra perda do host. Testes
offline aprovados não fecham esses gates nativos.

### Captura nativa do tracking e bloqueio do restore

O autor confirmou build/preflight, captura `7312832d3d8044999c62d5e556eaf38a`
com origem retomada e integridade verificada. O restore foi interrompido em seed
porque o mount `/capture` não preservava o UUID exigido pelo verificador. O recibo
inspecionado confirmou limpeza do projeto temporário; o preflight posterior
continuou aprovado. Correção e testes offline do caminho montado preparados;
novo build e restore nativos pendentes. [Detalhes](VM_TRACKING_BACKUP.md#correção-do-caminho-montado--2026-10-29).

### Restauração nativa do tracking aceita

Após o commit `93bce79`, a imagem auxiliar foi reconstruída e a mesma captura
`7312832d3d8044999c62d5e556eaf38a` restaurada em projeto isolado. Resultado
`verified/completed`: 65 tabelas, 26 objetos, quatro runs/modelos, 27 linhas de
histórico de métricas, champion/alias conferidos e uma linha de smoke com erro
zero. Readback posterior, origem/captura preservadas e limpeza dos recursos
temporários confirmados. Nenhum fit. [Recibo documental](../../references/evidence/vm_tracking_backup_restore_author_validation_2026-10-29.json).

O aceite fecha a restaurabilidade do tracking neste escopo. Cópia externa,
perda do host, demais dados e recuperação integral não foram demonstradas.
A publicação deste incremento e seus gates de CI/PR ainda são pendentes.

### Cópia fora da VM e restauração de retorno aceitas — 2026-10-30

O PR #18 integrou o backup limitado em `a5629db`; CI #87 do PR e #88 da main
aprovados. Depois o autor verificou a cópia no Alienware, devolveu-a e confirmou
`external_tracking_backup_restore_verified` no projeto `fraud-kind-restore-237aeb23ed4f`.
65 tabelas/26 objetos, quatro runs/modelos e 27 linhas de histórico de métricas;
champion/alias e erro de smoke zero, origem/captura preservadas, limpeza completa.
A captura original voltou ao caminho anterior e a devolvida foi reverificada.
[Recibo documental](../../references/evidence/vm_external_tracking_restore_author_validation_2026-10-30.json).

Esse aceite cobre a cópia fora do disco virtual e o restore neste ambiente,
sem treinamento. Não cobre SSD físico, recuperação integral ou VM vazia.
O próximo recorte é delimitar os dados críticos e consolidar a instalação limpa;
não ampliar a infraestrutura nem repetir os ensaios já aceitos.
