# Síntese para stakeholders

<!-- kind-operations-acceptance-2026-10-21:start -->
**Estado em 2026-10-21 — marco KinD/Helm concluído no escopo do laboratório.**
Parada/subida e reboot planejado aprovados no mesmo node e volumes, com inotify
persistente e leituras de tracking, serving e monitoramento conferidas.
[Sequência integrada, aceites e limites](../operations/KIND_PLATFORM.md). Próximo: simplificação
e fechamento funcional do portfólio; Terraform permanece posterior. Notas
datadas abaixo preservam o estado de cada incremento, sem reabrir gates aceitos.
<!-- kind-operations-acceptance-2026-10-21:end -->


Atualizado em 2026-10-16, incorporando a recuperação nativa do histórico e da
referência MLflow em cópia descartável dos restores já aprovados.
O pipeline offline, a referência congelada, o serving de laboratório e o replay
causal acelerado foram exercitados pelo autor. Tanto o replay corretivo quanto
o novo ensaio programado passaram a verificação de features, scores e decisões.
[Metas e prioridades](ROADMAP.md).

## Produto e próximo recorte de entrega

| Usuário | Entrega |
| --- | --- |
| Sistema transacional | API de pontuação com modelo identificado |
| Analista de fraude | Fila priorizada, contexto da transação e explicação |
| Equipe de operação | Grafana com saúde, erros, atrasos e qualidade |
| Equipe de ML | Linhagem, comparação de candidatos e implantação controlada |


O produto ajuda uma equipe a escolher quais clientes investigar. O sistema
transacional integra-se pela API; o analista consulta casos priorizados e os
fatores do score; a operação acompanha saúde/qualidade no Grafana. O fluxo de eventos pela API do champion passou no ensaio nativo de 2026-10-22,
com decisões consultáveis, feedback e retomada. A imagem instaladora foi atualizada
e verificada no KinD: startup repetido reutiliza a release sem reescrever os arquivos.
Modelo, versão do champion e consulta de casos foram preservados.
[Aceite da manutenção](../../references/evidence/serving_seed_recovery_author_validation_2026-10-22.json).
[Evidência](../../references/evidence/product_http_flow_author_validation_2026-10-22.json) e
[operação](../operations/SERVING_RECOVERY.md).
[Jornada, contratos e sequência](../operations/PRODUCT_DELIVERY.md).

O autor validou as explicações SHAP de cinco casos do champion: contexto,
19 contribuições por caso, score reconstruído e vínculos com pesos/features usados.
A verificação offline do relatório passou sem carregar o modelo e o champion foi
preservado. [Aceite](../../references/evidence/case_explanations_author_validation_2026-10-22.json).
[Entrega ao analista](../operations/CASE_EXPLANATIONS.md). Grafana mantém a operação;
a investigação usa o relatório e a consulta de casos, sem outro serviço.

**Os limites numéricos de promoção são específicos do laboratório. Em uma operação
real, precisam ser calibrados com custos de fraude, falsos positivos, capacidade
de investigação e incerteza das métricas. Ganhar 0,005 de AP, sozinho, não comprova
significância estatística nem retorno financeiro.** A retenção do champion
demonstra que terminar um treino não autoriza substituir o modelo.

## A decisão que apoiamos

Uma equipe com capacidade limitada precisa escolher quais clientes investigar
usando somente o histórico disponível naquele instante. O laboratório transforma
scores em uma fila com até 100 clientes por dia, registra a decisão e avalia o
resultado quando os rótulos chegam sete dias depois.

Os dados são simulados pelo Fraud Detection Handbook. O projeto demonstra
engenharia e governança de ML; não comprova eficácia contra golpes reais,
perdas evitadas ou autorização para bloquear pagamentos. Investigação envolve
também regras, autenticação e revisão humana. [Contexto](PROBLEM_CONTEXT.md).

## O que está demonstrado

HGB significa Histogram-based Gradient Boosting, implementado pelo
`HistGradientBoostingClassifier`. A referência foi mantida pelos resultados dos
gates, sem assumir que novos algoritmos necessariamente melhorariam a triagem.

| Capacidade | Evidência e alcance |
| --- | --- |
| Dados rastreáveis | 183 arquivos íntegros; Silver com 1.754.155 transações, 14.681 fraudes e 42 valores monetários zero preservados |
| Avaliação temporal | 19 preditores causais; treino, validação e teste separados; avaliação final de maio: AP 0,640703 e precisão diária @100 de 55%, sem refit |
| Seleção controlada | Três ablações e 20 trials Optuna; nenhum candidato passou os gates, conservando o HGB congelado |
| Modelo distribuível | MLflow/skops, release identificada, HTTP/wheel fora do checkout e Docker validados pelo autor |
| Decisão por eventos | 556.824 eventos e 143.945 decisões no replay; feedback separado, histórico causal e checkpoint durável |
| Consistência corrigida | Igualdade exata dos 19 atributos e paridade dos scores aprovada sem ampliar tolerância; execução original preservada |
| Qualidade da fila emitida | 1.453 alertas, 768 clientes-dia fraudulentos capturados e 358 perdidos na janela de 16–30/09 |
| Emissão programada e telemetria | 556.824 eventos em 14 sessões a 8.640×; todas as 143.945 decisões com tempos medidos, zero lacunas e paridade aprovada |
| Operação visível inicial | Stack Docker, scrape Prometheus e valores dos dois dashboards confirmados pelo autor no smoke; retomada após crash e repetição idempotente demonstradas com dados gerados |
| Falha operacional observável | Coleta nativa completa válida; alerta de exporter observado em 56,36 s e retorno em 12,29 s, com arquivos de estado inalterados |
| Qualidade de implementação | Fechamento da observabilidade: 633 testes aprovados/um real ignorado; armazenamento após correção: 38 testes aprovados pelo autor; escopos e revisões nos recibos |

## O resultado para a investigação

Em 15 dias, houve **1.126 clientes-dia fraudulentos**. Um mesmo cliente pode contar
em dias diferentes. Na fila causal, 768 foram capturados e 358 ficaram fora;
dos 1.453 alertas emitidos, 685 corresponderam a clientes-dia sem fraude.

| Indicador | Fila causal, no instante da decisão | Top-100, após observar o dia |
| --- | ---: | ---: |
| Alertas | 1.453 | 1.500 |
| Clientes-dia fraudulentos capturados | 768 | 800 |
| Clientes-dia fraudulentos perdidos | 358 | 326 |
| Precisão agregada | 52,86% | 53,33% |
| Recall agregado | 68,21% | 71,05% |

Precisão agregada divide capturas pelo total de alertas efetivamente emitidos;
recall agregado divide capturas pelos 1.126 clientes-dia fraudulentos. Esses
valores não são médias diárias nem usam transações como denominador.
Uma captura considera qualquer fraude naquele cliente-dia, inclusive posterior
ao alerta; não significa que a transação disparadora era fraudulenta.

A fila utilizou **96,87% das 1.500 vagas** possíveis: 47 ficaram ociosas e o
orçamento esgotou em 9 dias. Houve 91 clientes-dia elegíveis suprimidos pelo limite;
essa contagem não identifica, por si só, fraudes perdidas.
O retrospectivo capturou 32 clientes-dia fraudulentos adicionais, dispondo do dia
completo para ordenar. A comparação é descritiva, sem superioridade formal.

## O incidente de paridade e sua resolução

A execução original processou todos os eventos, mas reprovou 2/143.945 scores.
Diferenças minúsculas nas médias calculadas por SQLite/DuckDB atravessaram cortes
das árvores. A regra versionada soma exatamente os valores binários e arredonda
uma vez depois da divisão. O replay corretivo passou o verificador independente
com o mesmo modelo, limiar e tolerância.

Em relação à execução original, apenas um score persistido mudou. Não houve
mudança de elegibilidade, status de triagem ou dos 1.453 alertas. O checkpoint
original conservou o hash. Essa correção demonstra diagnóstico, regressão e
recuperação auditável, sem substituir a evidência da falha anterior.

## Operação visível: resultado e próximos critérios

O autor concluiu o replay programado nativo, com fila limitada e telemetria:
556.824 eventos, 143.945 decisões e 1.453 alertas em 14 sessões, sem commits sem
medição. A avaliação independente confirmou features, scores e política causal.
Os resultados de qualidade acima foram reproduzidos, não constituem uma nova
confirmação independente. Prometheus recebeu métricas e os valores dos dois
dashboards foram confirmados pelo autor no smoke gerado.

O p95 de processamento foi **23,71 ms** (máximo de 2,44 s). O p95 do agendamento
até commit foi **379,57 s**, com atraso de emissão p95 de 377,78 s, sob aceleração
de 8.640×. Isso mostra a importância de medir o atraso anterior ao processamento;
não comprova latência de produção nem identifica sozinho o recurso limitante.
Os quantis são acumulados das 14 sessões, com nova âncora a cada retomada.

O monitor distingue falha de coleta de qualidade zero; exibe suporte, atraso/fila,
lacunas e mudança nos scores. Precisão/recall/AP exigem coortes completas.
O autor confirmou coleta/contagens no estado nativo final e executou a falha
controlada do exporter: alerta observado em 56,36 s e retorno em 12,29 s, com os
arquivos do ensaio inalterados. Esses tempos incluem comandos e polling, não são
SLA de produção. A rotina agora tem testes pytest e ambiente tox explícito para
uso real; essa integração nova não é a ferramenta que produziu o relato anterior.
Falta a conferência visual dos painéis nativos finais. O crash/retomada do replay
usa o smoke gerado; não valida toda falha possível no ensaio nativo.

O roadmap foi reordenado: **operação/monitoramento → objetos S3 locais e
restauração → entrega/rollback → gestão de features e atualização controlada**.
RustFS foi escolhido como backend local inicial, com perfil opcional preparado.
Armazenamento de objetos abrange Bronze/Silver/Gold, modelos, avaliações, tracking
e cópias de estado, com inventário completo e migração por conjuntos verificados.
O [plano de persistência](../operations/STORAGE.md) distingue S3, DuckDB, bancos e
métricas. Inventário e piloto de upload/restauração (64 arquivos/64 MiB) estão
implementados e testados; o autor validou inventário de 1.643 arquivos (2,92 GiB),
publicação/reenvio e restauração de dois documentos no RustFS (17.559 bytes).
A repetição reutilizou os três objetos sem novo upload. Os dez arquivos antes sem classe foram identificados. A release completa também
passou upload/reenvio/restauração: quatro arquivos/735.485 bytes, cinco objetos
reutilizados sem novos uploads. O autor aprovou a recarga via pytest/TestClient:
identidade preservada, score histórico `0.4178630794049542` reproduzido com erro
zero, readiness 200 e entrada inválida 422. A verificação leu somente a release
restaurada e não retreinou o modelo.
[Evidência da recarga](../../references/evidence/storage_release_reload_author_validation_2026-10-15.json).
O autor aprovou o preparo/verificação da Bronze completa: 183 partições,
188 arquivos/107.309.243 bytes, com proveniência e inventário de origem incluídos.
[Evidência do preparo](../../references/evidence/bronze_archive_author_validation_2026-10-15.json).
A transferência usa um único job/cliente S3 por comando. O autor aprovou build,
publicação e recuperação no RustFS: **189 objetos publicados/conferidos**, reenvio
com **zero uploads/189 reutilizados**, restore com **188 arquivos/107.309.243 bytes
e 183 partições completas**, usando o inventário baixado e sem consultar os originais.
[Evidência e limites](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
O gate de arquivamento/recuperação da Bronze está fechado. Os leitores ainda usam
paths locais. O autor também aprovou preparo e round-trip da Silver:
**187 arquivos/40.288.931 bytes**, contrato/inventário e uma auditoria incluídos;
**188 objetos**, reenvio sem uploads e restore com **183 partições completas e
1.754.155 linhas/IDs distintos**. Schema e contagens SQL foram conferidos com os
metadados baixados, sem ler os originais ou Bronze. Esse gate Silver está fechado.
[Evidência nativa](../../references/evidence/silver_archive_author_validation_2026-10-15.json).
O inventário Gold mapeou 506 arquivos/326.821.198 bytes. A ampliação começa
por `gold_v1`, com auditorias, inventário, contratos Silver/Gold e protocolo.
O preparo/restore usa o transporte existente e verificador de schema, features,
datas, IDs e splits. O autor aprovou o ciclo nativo: **48 arquivos/25.196.448 bytes**,
**49 objetos**, reenvio sem uploads e restore com **42 partições, 19 features e
402.877 linhas**, usando as quatro referências baixadas. O gate de recuperação
de `gold_v1` está fechado; o restore não leu as camadas originais.
[Evidência e limites](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
O inventário seguinte separou 38.159.334 bytes de dataset de desenvolvimento de
232.943.522 bytes de estudo, incluindo um SQLite de 540.672 bytes. O perfil do
dataset passou pelo ciclo nativo: **66 arquivos/38.202.741 bytes**, **67 objetos**,
reenvio sem uploads e restore com **63 partições/19 features/603.669 linhas**.
Run/hash do manifesto foram preservados com política/inventário baixados, sem
consultar originais ou estudo. Gate do dataset fechado.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
O preparo offline desse backup está implementado/testado: lock, API de backup
SQLite e verificação conjunta de trials/modelos/predições/recibos com dataset
arquivado. Somente uma cópia descartável adapta o caminho de verificação;
relatório e referências históricos são preservados. O autor aprovou o preparo:
**322 arquivos/233.048.483 bytes, 20 trials concluídos e 63 tentativas**. A decisão
`retain_reference` continua a mesma. O autor aprovou publicação/reenvio/restauração
S3 em 2026-10-16: **323 objetos**, **zero uploads no reenvio** e **322 arquivos
restaurados**, com integridade/foreign keys, hash lógico e vínculo com o dataset
verificados. O restore não leu a fonte original. Gate do estudo concluído fechado.
[Evidência nativa S3](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
Nenhum limite foi ampliado nem banco migrado. Retomada de treino não foi validada.
[Evidência do preparo](../../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
[Preparação do estudo](../../references/evidence/hgb_study_backup_preparation_2026-10-15.json).
[Preparação e limites](../../references/evidence/development_gold_archive_preparation_2026-10-15.json).
Cobertura dos demais modelos/avaliações, bancos de tracking e adoção pelos
consumidores seguem abertas. O inventário do backend MLflow, artefatos e URIs
teve coleta nativa inicial: 70 runs finalizadas, 66 logged models e 627 arquivos.
Dois avisos de IDs eram UUIDs locais de diagnóstico classificados incorretamente;
a nova coleta confirmou zero referências não resolvidas e nenhuma localização
gerenciada ausente. Default sem runs/modelos ou diretório: revisão concluída,
conservando o aviso bruto. Preparo/verify de backup offline completo banco +
artefatos tiveram preparo/verificação e ciclo S3 nativos aprovados para os dois
stores, sem alteração do servidor de tracking. [Contrato](../operations/MLFLOW.md#backup-offline-do-tracking-preparo-e-verificação).
[Evidência nativa](../../references/evidence/mlflow_inventory_author_validation_2026-10-16.json). A função é definir
cobertura, backup restaurável e preservação de histórico antes de transferir
para PostgreSQL + S3; não há servidor novo nem migração validada nesta etapa.
[Procedimento e critérios](../operations/MLFLOW.md#inventário-antes-da-migração-postgresql--s3).
[Evidência de preparação e limites](../../references/evidence/silver_archive_preparation_2026-10-15.json).
[Evidência e limites](../../references/evidence/storage_pilot_author_validation_2026-10-15.json).
Isso não declara os datasets migrados. PostgreSQL é o destino
escolhido para o servidor MLflow, com migração própria preservando runs/URIs. Não há conta cloud ou serviço pago obrigatório; a máquina consome
recursos locais. Um único nó não protege contra perda do disco/máquina.

Uma feature store pode ajudar se houver reutilização entre consumidores e gestão
de frescor/materialização. O spike precisa preservar as 19 definições causais,
a disponibilidade de rótulos e a paridade offline/online, e justificar seu custo.
**KinD + Helm é o destino final escolhido para provisionamento reproduzível**,
com implementação após persistência/restauração e rollback. Compose é a bancada
atual. Novas buscas de modelos permanecem condicionadas a hipótese e avaliação próprias.
[Critérios e prioridades](ROADMAP.md), [runbook](../operations/OBSERVABILITY.md).

## O que continua em aberto

| Próximo marco | Evidência necessária |
| --- | --- |
| Operação no ritmo de chegada | Ensaio acelerado concluído; recorte 1x contínuo exige protocolo e orçamento próprios antes da execução |
| Observabilidade | Coleta e alerta/retorno nativos validados; confirmar visualmente os painéis finais e ampliar dados/features |
| Persistência S3 local | Release recuperada/recarregada e Bronze/Silver/Gold v1 arquivadas/recuperadas; dataset de desenvolvimento também recuperado/verificado; estudo Optuna/SQLite recuperado/verificado com 323 objetos e 20 trials/63 tentativas preservados; inventário MLflow corrigido/revisado, Default sem payload existente observado; preparo/verify de backup offline implementados/testados, com preparo/verificação nativos aprovados e publicação, reenvio idempotente e recuperação S3 aprovados pelo autor; adoção primária segue aberta |
| Entrega e reversão | Imagem/modelo identificados, gates e demonstração de rollback |
| Atualização controlada | Gatilhos, orçamento, disponibilidade de rótulos e avaliação temporal do candidato antes de promoção |
| Provisionamento reproduzível | KinD + Helm como destino escolhido; instalação limpa, persistência, saúde e rollback demonstrados após os marcos anteriores |

O ensaio programado aprovado usou produtor/consumidor locais, fila em processo e
14 sessões aceleradas. Não comprova ritmo 1x, SLA,
transporte distribuído ou disponibilidade contínua. O p95 HTTP de 4,74 ms do
Docker é uma medição anterior de serving sequencial e não a latência ponta a
ponta do replay. Foram fornecidos custos de três invocações iniciais, com pico de
memória do processo entre 337 e 341 MiB; os custos de todas as sessões e da stack
Docker não foram consolidados. Não estimamos retorno financeiro, fraude evitada ou efeito sobre
clientes reais. Todos os rótulos chegam após sete dias no cenário assumido;
investigação seletiva e atrasos variáveis de feedback continuam fora do ensaio.

Maio, 02–15/09 e 16–30/09 já foram consumidos em seus respectivos protocolos.
AP 0,578783 no replay e AP 0,621312 na primeira quinzena pertencem a janelas
diferentes; essa diferença isolada não prova degradação. Novas hipóteses de
qualidade exigem desenho próprio e uma janela de avaliação preservada.

## Evidências e leitura

O [registro da preparação](../../references/evidence/observability_closure_preparation_2026-10-15.json)
identifica os checks do novo runner pytest/tox e a preservação do núcleo;
não representa uma nova execução de Docker no computador do autor.

A [evidência de recuperação do exporter](../../references/evidence/exporter_recovery_author_validation_2026-10-15.json)
registra o resumo de terminal fornecido pelo autor, incluindo contagens nativas e
coleta Prometheus válidas. O relatório completo e os hashes individuais não foram
recebidos; a preservação dos arquivos é um resultado reportado pelo utilitário.
O teste real foi anterior à nova integração pytest/tox; não confundir as duas validações.

A [evidência do replay programado](../../references/evidence/paced_native_author_validation_2026-10-15.json)
registra a avaliação final e o relatório de qualidade fornecidos pelo autor:
14 sessões, zero commits sem medição e as mesmas 768 capturas/358 perdas.
O recibo operacional foi informado como
`c1192d5481f890f9bf878e8f8aa730011007f13c9f576cf0d5a84161b4e129d1`.
Seu hash não foi recalculado pelo assistente; os bancos e relatórios completos
permanecem no ambiente do autor. A revisão publicada de referência é `939cf34`;
isso não prova independentemente o SHA de todo o checkout durante a execução.

A [evidência do replay](../../references/evidence/native_numerical_correction_author_validation_2026-10-13.json)
reconcilia as duas saídas de terminal (`run` e `verify`) fornecidas pelo autor.
O assistente conferiu igualdade dos relatórios, contagens e taxas agregadas;
não inspecionou os bytes dos checkpoints, scores ou medições no computador do autor.
A publicação da implementação `c819d65` e sua [CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/38487732538)
foram conferidas separadamente. O arquivo de terminal não comprova
independentemente o SHA exato do checkout usado na execução.

[Model Card](../modeling/MODEL_CARD.md),
[runbook corretivo](../operations/NUMERICAL_CORRECTIVE_REPLAY.md#resultado-nativo-informado-pelo-autor--2026-10-13),
[notebook principal](../../notebooks/fraud_detection_mlops.ipynb) e
[política de documentação](DOCUMENTATION_POLICY.md) mantêm método, origem e limites.
Os recibos históricos permanecem preservados; não há promoção em produção.

Após maturar persistência, recuperação e rollback, o fechamento inclui enxugar
scripts temporários, interfaces duplicadas e documentação obsoleta. Exclusões
seguirão revisão de referências e regressão dos fluxos suportados; versões
históricas permanecem no Git. [Critério de simplificação](ROADMAP.md#simplificação-após-maturação-da-infraestrutura).


O preparo nativo detectou o segundo store documentado em
`data/tracking/interface-smoke`: 1 run concluída, 1 logged model, 6 arquivos/32.088
bytes, sem localizações gerenciadas ausentes. A correção inclui os dois stores
com bancos/WAL e artefatos conferidos independentemente e limites agregados,
preservando a separação do smoke sintético. Bancos desconhecidos continuam
bloqueados. Retentativa de preparo e verificação nativos aprovadas para os dois stores;
publicação, reenvio idempotente e recuperação S3 aprovados pelo autor. Migração,
recarga operacional e limpeza permanecem abertas. [Cobertura e comando](../operations/MLFLOW.md#cobertura-dos-dois-stores-mlflow--correção-de-2026-10-16).
[Registro do diagnóstico e preparação](../../references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).


O backup MLflow teve **preparo e verificação locais aprovados pelo autor**:
dois stores, 71 runs, 67 logged models, 633 artefatos; pacote de 640 arquivos e
256.354.588 bytes. O verificador conferiu os dois bancos e artefatos sem ler
originais. Tox nativo: 70 testes aprovados, um aviso, Ruff/formatação/lockfile
aprovados. [Registro nativo](../../references/evidence/mlflow_backup_author_validation_2026-10-16.json). A transferência S3 agora está
implementada/testada com o job existente, incluindo restauração em diretório
separado e nova verificação por store; publicação/reenvio/restore nativos aprovados.
A recuperação sobre cópia descartável foi validada pelo autor em 2026-10-16; o resultado local via SDK está no runbook MLflow.
[Procedimento](../operations/MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow).
O acesso local ao histórico e à referência pelo SDK foi validado depois desse
preparo. Migração PostgreSQL, adoção pelos consumidores e inventário/limpeza
globais mantêm critérios próprios. Código científico, contratos,
evidências e URIs históricas permanecem preservados.


**Ciclo S3 do snapshot MLflow aprovado pelo autor em 2026-10-16:** publicação com
641 objetos enviados/verificados, reenvio com zero uploads/641 objetos reutilizados
e restore em diretório separado com 640 arquivos/256.354.588 bytes. Os dois stores
mantiveram 71 runs, 67 logged models, 633 artefatos e os hashes SQLite/lógicos;
integridade/FKs/tabelas/URIs conferidas, sem leitura dos originais pelo verificador.
16 testes nativos passaram no tox. [Evidência nativa](../../references/evidence/mlflow_transfer_author_validation_2026-10-16.json).
Gate de acesso ao histórico e recarga do modelo concluído em cópia descartável,
com paths históricos mapeados somente nessa cópia e sem depender dos originais.
O autor validou esse uso local pelo SDK em 2026-10-16.
O snapshot conferido permanece preservado; PostgreSQL, adoção pelos leitores,
rollback, inventário global e limpeza continuam abertos.
[Resultado e próximo critério](../operations/MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16).

### O que este incremento entrega — 2026-10-16

O backup recuperado precisa servir ao uso, além de preservar bytes. O ensaio
implementado consulta os dois históricos MLflow e recarrega a referência por sua
URI, comparando hash e score com a release já validada. Adapta caminhos somente
em cópia descartável; stores originais e restores são preservados. Testes com
fixtures concluídos; **execução nativa aprovada em 2026-10-16**.
[Procedimento e critério](../operations/MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).

O [contexto do problema](PROBLEM_CONTEXT.md) também foi revisto: a finalidade é
priorizar revisão, métricas usam unidades distintas e os dados são simulados.
Monitoramento/replay/Docker deixam de aparecer como futuros; S3 primário, serviço
MLflow/PostgreSQL, rollback, inventário global e limpeza continuam com gates abertos.
Isso explicita o valor da infraestrutura sem afirmar prontidão de produção.

**Correção de integração em 2026-10-16:** o autor aprovou 14 testes, mas a recarga
nativa expôs diferença entre os tipos do pedido HTTP e a assinatura histórica
MLflow. A adaptação agora respeita a assinatura sem alterar valores; conversão
inexata é recusada. Fixtures passam a reproduzir assinaturas mistas e todas-double.
Modelo e evidências anteriores foram preservados; a retentativa nativa posterior
foi aprovada, conforme o registro abaixo.
[Diagnóstico e retentativa](../operations/MLFLOW.md#compatibilidade-do-smoke-com-a-assinatura-histórica--correção-de-2026-10-16).

**Recuperação local MLflow aprovada pelo autor em 2026-10-16:** os dois stores
forneceram 71 runs, 67 logged models e 321 registros de histórico. A referência
recarregada reproduziu o score com erro zero; restores e tabelas foram preservados
e a cópia descartável removida. O gate fecha acesso local pelo SDK e um smoke de
inferência; serviço HTTP/PostgreSQL, migração primária e inventário global mantêm
critérios próprios. [Resultado e evidência](../operations/MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

A abertura do [contexto do problema](PROBLEM_CONTEXT.md) foi reescrita para quem
está começando: exemplo fictício, vocabulário, decisão da fila, atraso do feedback
e interpretação das métricas. Os exemplos não são resultados do dataset e as
regras/métricas efetivas do projeto permanecem preservadas.

### Saber quem grava e onde antes de mudar os destinos

A recuperação do MLflow está aprovada no escopo local. O próximo recorte é conhecer
os arquivos e seus responsáveis antes da adoção PostgreSQL/S3 pelos consumidores.
O coletor novo cobre metadados do checkout, incluindo cópias/recibos em `data/storage/`
e saídas soltas na raiz. O catálogo declara produtores, volumes Docker e overrides,
sem ler credenciais, modelos ou bancos. Implementação/testes e coleta nativa
aprovados no recorte do checkout. Classificar um arquivo não comprova backup nem elimina os caminhos externos.
Nenhum dado, writer ou script foi movido/removido. [Procedimento e limites](../operations/STORAGE.md#inventário-do-checkout-e-catálogo-de-produtores).


O inventário do checkout foi executado pelo autor: 4.990 arquivos, sem falhas de
coleta; o marcador vazio de `data/external/` foi revisado e os mounts principais
identificados. A primeira contenção exige áreas de `data/` em quatro CLIs de
exemplos, recibo Docker e inicialização de observabilidade. Não cobre todas as
APIs/CLIs nem migra consumidores. [Destinos, comandos e limites](../operations/STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).


### Serviço MLflow PostgreSQL/S3 — ensaio isolado validado

**Gate nativo aprovado pelo autor em 2026-10-17:** uma run operacional foi
conferida por SQL, e seu artefato JSON por proxy HTTP e leitura S3 independente.
Após recriar PostgreSQL e MLflow, os registros e bytes permaneceram disponíveis.
Os IDs dos containers mudaram; imagens e volume PostgreSQL foram preservados.
O RustFS permaneceu em execução. Pytest: **1 aprovado em 21,56 s**; tox: **OK**.

A UI em `http://127.0.0.1:15001` mostra o experimento 2, run
`87ac0b4a310f4ab8952a366932c0d12a`. O ensaio cobre uma run com dois pontos de
histórico e um JSON pequeno. Adoção pelos produtores, recarga de modelo pelo novo
serviço, histórico e backup PostgreSQL seguem com critérios próprios.

[Operação e próximos critérios](../../infra/tracking/README.md).
[Evidência relatada e limites](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

### Primeiro produtor HTTP — exemplo de contribuição

`examples.model_contribution` passa a aceitar `--tracking-uri` explicitamente,
como alternativa a `--tracking-root`. O exemplo registra um modelo skops e confere
recarga/paridade pelo proxy no experimento `fraud-model-interface-smoke-http-v1`.
São 80 linhas de treino e 20 de validação geradas em memória, sem acesso ao Handbook.
**Aceite nativo PostgreSQL/RustFS aprovado pelo autor em 2026-10-17:** recarga
independente com erro zero e retorno local explícito; detalhes no fechamento abaixo.

[Contrato, destinos e validação](../modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

### Correção do transporte do produtor HTTP — 2026-10-17

A regressão nativa teve 79 testes aprovados, mas a primeira execução do produtor
parou ao baixar os cinco arquivos do modelo por chunks. No MLflow 3.17, o cliente
pode habilitar URLs assinadas automaticamente a partir das capacidades do backend
S3 anunciadas pelo servidor. O fixture anterior com arquivos locais não cobria
essa diferença. A recarga e o rollback local não foram aprovados nessa tentativa.

O contexto HTTP agora fixa `MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD=false` e
`MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD=false`: o transporte deste produtor passa
pelo servidor MLflow. As duas variáveis, a URI e os limites anteriores são
restaurados ao sair. Não foi aumentado timeout ou removida a revisão skops/paridade.
A retentativa nativa cria outra contribuição e preserva os registros da falha.

[Diagnóstico, retentativa e limites](../../infra/tracking/README.md#retentativa-do-produtor-após-falha-de-download-por-chunks).

### Contribuição HTTP aprovada; próximo produtor: HGB — 2026-10-17

O autor aprovou a retentativa: **34 testes de regressão e 1 teste nativo**.
O modelo foi recarregado pelo serviço sem a saída local disponível, com erro
máximo zero. O retorno explícito ao tracking local descartável e a preservação
dos containers foram conferidos. A criação de tabelas no log pertence a esse
retorno local deliberado; não indica fallback silencioso do HTTP.
[Evidência do autor](../../references/evidence/model_contribution_http_author_validation_2026-10-17.json).

A próxima integração prioriza o **HGB**, começando por um fold sintético com
identidade de destino, recarga independente e reutilização sem fit/publicação.
O estudo de 20 trials/63 tentativas e a decisão `retain_reference` ficam
preservados. O fold HGB foi aprovado pelo autor; o estudo completo não será
reexecutado para testar transporte. [Procedimento e limites](../operations/HGB_TRACKING.md).

### Tracking dos produtores — situação em 2026-10-17

O autor aprovou o fold sintético do runner de otimização HGB: **84 linhas de
treino, 21 de validação, erro zero e uma tentativa**, reutilizada sem fit ou
publicação repetidos. Os containers permaneceram intactos. Essa prova não é a
migração do estudo Optuna ou do trio inicial de baselines.

| Fluxo | Situação |
| --- | --- |
| Exemplo de contribuição | Recarga HTTP independente e retorno local aprovados pelo autor |
| Runner Optuna do HGB | Fold HTTP aprovado; busca completa e migração histórica não executadas |
| Trio inicial: dummy, regressão logística e HGB | Opção HTTP aprovada pelo autor para o trio sintético; histórico preservado |

O autor aprovou o ensaio dos três modelos com dados sintéticos pequenos, sem
repetir os experimentos Handbook. [Tracking do trio e comandos](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).
[Evidência HGB do autor](../../references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).

### Trio de baselines aprovado no serviço — 2026-10-17

O autor aprovou **três runs independentes**, uma para dummy, regressão logística
e HGB, com 160 linhas de treino e 160 de validação sintéticas. Os três modelos
foram recarregados pelo HTTP com os arquivos locais indisponíveis, e todos tiveram
erro máximo **zero**. Não houve tracking SQLite novo nem mudança nos containers.
Pytest: **1 aprovado em 6,55 s**; tox `tracking-baseline`: **OK em 19,96 s**.

Essa etapa de integração dos produtores está concluída: contribuição, fold do
runner Optuna HGB e trio inicial têm aceites próprios. O modo local continua
padrão, e as runs históricas não foram migradas. Recuperação PostgreSQL/S3,
consumidores locais restantes e cutover mantêm critérios próprios; não há
motivo para repetir treino ou criar mais serviços para encerrar este aceite.
[Evidência do autor](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).

### Consumidores do tracking: revisão antes da recuperação

O inventário existente ganhou o modo explícito `--tracking-consumers`: confere
fontes/funções do catálogo sem importar módulos, abrir modelos/bancos ou consultar
serviços. A revisão preparada tem **17 registros e 7 fronteiras locais**:
ferramentas de histórico, diagnósticos, ablações, freeze, avaliação final,
avaliação estatística e exportação. A inferência da release permanece independente
do MLflow. Esse resultado não altera os consumidores ou migra o histórico.

A captura coordenada PostgreSQL/S3 e o restore em projeto/volumes/rede próprios
foram implementados e aprovados pelo autor, com conciliação de metadados,
objetos e modelo fixado. O aceite final está registrado ao final.
[Mapa, comando e protocolo](../operations/TRACKING_RECOVERY.md).

### Captura recuperável da bancada — 2026-10-19

O autor confirmou o mapa de consumidores: **48 testes aprovados**, 17 registros,
sete fronteiras locais e nenhuma divergência. Isso fecha a revisão do recorte;
não fecha o inventário global nem migra consumidores históricos.

A captura coordenada foi implementada com pausa curta apenas no MLflow,
dump PostgreSQL e cópia integral dos objetos S3 autorizados, com limites, hashes e
verificação de retorno à saúde do container original. A preparação foi testada
sem serviços; **o aceite live da captura foi aprovado em 2026-10-19**. Um índice de dump válido
e arquivos íntegros são pré-requisitos: a recuperação exigiu restauração em volumes separados e leitura independente
do modelo fixado, aprovadas no registro final deste documento.
[Procedimento e limites](../operations/TRACKING_RECOVERY.md).

### Último gate do recorte de recuperação — 2026-10-19

A captura nativa passou: **38 objetos S3, 8 runs, 6 modelos, 44 pontos históricos**;
dump de 149.587 bytes, MLflow novamente saudável e containers/volume preservados.
O teste levou 9,40 s. O ambiente apenas estava parado na primeira tentativa;
iniciar os containers existentes e o RustFS resolveu a pré-checagem sem mudar código.

O restore isolado foi implementado: PostgreSQL e RustFS novos, MLflow próprio e
cliente sem credenciais de infraestrutura. Confere conteúdo/estrutura dos dados,
histórico API e modelo da contribuição sintética contra scores salvos. Após a
prova, remove somente seus recursos temporários e mantém o recibo.
**Aceite nativo posteriormente aprovado em 2026-10-19**, conforme o registro ao final; preparação offline e recarga HTTP real mantêm seus escopos próprios.

Esse é o último gate de recuperação deste recorte. As sete fronteiras locais
mapeadas permanecem no backlog de migração, sem impedir o avanço para entrega com
rollback e monitoramento de features. Não transformar inventário em fase permanente,
nem declarar inventário global ou apagar legados para encerrar o marco.
[Procedimento](../operations/TRACKING_RECOVERY.md).

### Histórico da falha de restore (resolvida no aceite abaixo)

O ensaio nativo de 2026-10-19 falhou no worker seed após o comando pg_restore.
O motivo foi ocultado pelo wrapper, uma deficiência de diagnóstico corrigida
neste incremento. Naquela tentativa a causa ainda não havia sido determinada; o aceite só ocorreu
após a reprodução e correção descritas abaixo. A falha agora preserva fase e diferenças estruturais sem valores privados,
mantendo o mesmo escopo e a captura original para a nova tentativa.

### Comparação do esquema após pg_restore — 2026-10-19

A falha seguinte foi reproduzida com PostgreSQL 17.11 e MLflow 3.17.0 em uma
bancada descartável: os quatro hashes de colunas/constraints coincidem com a
saída nativa. Em `spans`, uma coluna removida deixou uma lacuna nos ordinais;
o dump lógico recria somente as colunas existentes. Cinco checks de enumeração
reescrevem o cast de `varchar[]` para `text[]` nos elementos do array, preservando
a regra. A comparação literal desses descritores produzia um falso negativo.

O worker compara a ordem relativa das colunas existentes, mantendo nome, tipo,
udt, nulabilidade e default capturados. Aceita somente os cinco pares exatos de
checks reproduzidos, identificados por tabela/nome/tipo; outras definições
permanecem literais. A ordenação de registros entre tabelas/constraints não tem
significado de esquema. Dados, índices, sequências, ownership, URIs e Alembic
continuam exigindo igualdade; duplicatas falham. O formato da captura e seus
bytes permanecem intactos.

A reprodução local passou e alterações SQL reais de tipo, default, nulabilidade
e check foram rejeitadas. O autor depois aprovou o gate nativo completo com S3,
API e modelo, conforme o registro abaixo. A comparação cobre os atributos do
inventário, sem alegação de auditoria exaustiva do catálogo PostgreSQL.

## Aceite nativo de recuperação — 2026-10-19

O autor executou `tracking-restore`: **1 teste aprovado em 20,89 s**, tox OK em
23,30 s. Foram restaurados e conferidos **38 objetos S3, 8 runs, 6 modelos e
44 pontos históricos de métricas**. O modelo fixado foi recarregado pelo proxy
HTTP do alvo, sem credenciais PG/S3 no cliente, e reproduziu **20 scores com erro
máximo absoluto 0,0**. A releitura após uso da API confirmou dados/objetos intactos.

A fonte manteve os mesmos containers, imagens e volume PostgreSQL. A captura
permaneceu inalterada; volumes/rede/containers e credenciais temporários do alvo
foram removidos. `postgres_backup_restore_validated=true` no recibo
`restore-d7097b34092a4929ae460a411cf30843.json`. O teste SDK passou com 56 casos;
o caso opcional de binários PostgreSQL locais foi pulado, como previsto.

**Recorte de inventário e recuperação encerrado.** Esta é uma prova laboratorial
de restauração em novos recursos no mesmo host. Não migra SQLite histórico,
não realiza cutover, não fecha inventário global e não comprova recuperação após
perda física do laptop. As sete fronteiras históricas e a limpeza ficam em
backlog próprio; a próxima prioridade é CD/rollback do serving, seguida de
monitoramento de dados/features. Este fechamento não requer repetir captura,
replay, treino, build ou o gate aprovado.

[Evidência do autor](../../references/evidence/tracking_restore_author_validation_2026-10-19.json).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte. O build e a exportação da entrega
versionada também foram aprovados nativamente; o consumo do arquivo em outro
alvo tem aceite nativo informado pelo autor; CI remota aprovada no aceite abaixo. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](../operations/SERVING_ROLLOUT.md).

### Correção da rede do ensaio de rollout — 2026-10-19

A primeira execução encontrou o backend A saudável, mas o gate HTTP do host expirou antes do roteamento. O cleanup foi confirmado. A e B passam a ficar sem portas publicadas na rede interna; os gates fazem HTTP dentro dos containers e preservam identidade, score e HTTP 422. Só o NGINX publica loopback por uma bridge adicional de entrada. A execução nativa posterior aprovou a correção e todo o ensaio, conforme o fechamento abaixo.

### Fechamento e decisão de entrega — 2026-10-19

O teste mostrou que uma release pode ser validada antes de receber tráfego e que
o serviço pode voltar à referência após falha explícita da candidata. O retorno
foi observado em 3,10 s, com score preservado; isso é uma medição de laboratório.
A suíte de serving teve 110 testes aprovados e dois opt-in ignorados; o ensaio
Docker teve um aprovado. O assistente conciliou os hashes e a saída informada
pelo autor, sem acesso independente ao engine ou aos bytes do recibo nativo.

A estratégia escolhida permanece blue-green. Uma réplica ativa e uma candidata
durante a entrega atendem ao objetivo de demonstrar gates e rollback. A retenção
da anterior por uma janela definida é o plano para o fluxo permanente; hoje o
ensaio remove todos os seus recursos ao terminar. Canary não é necessário neste
recorte de baixo tráfego e rótulos atrasados. O próximo incremento conecta a
evidência ao fluxo versionado de entrega, antes de levar o provisionamento a Helm.

[Evidência](../../references/evidence/serving_rollout_author_validation_2026-10-19.json) e
[procedimento e limites](../operations/SERVING_ROLLOUT.md#aceite-nativo--2026-10-19).

### Entrega versionada: aceite local e execução CI pendente

A CI ganhou um job após `quality` que constrói a imagem uma vez, qualifica HTTP e
rollback com release **sintética** e exporta essa mesma imagem com hashes e recibos
vinculados ao commit. Sem dados históricos, publicação em registry ou deploy no
laptop. **Build, gates e exportação aprovados nativamente pelo autor em
2026-10-19:** uma imagem do commit `0b95136`, rollback verificado, 11 arquivos e
arquivo de imagem com 175.020.032 bytes. Recursos temporários e tag própria
removidos. A execução no GitHub Actions, a recarga do arquivo em outro alvo e a
promoção de uma release aprovada têm gates próprios. O artifact da CI está
configurado com retenção de três dias; não há aceite de upload remoto ainda. [Fluxo e limites](../operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

### Correção antes do aceite da entrega versionada

O primeiro build nativo terminou, mas o processo sem privilégios não conseguiu
ler o manifesto da fixture sintética (modo 0600). A entrega não foi qualificada.
O ajuste se limita à cópia gerada para o job; mantém o usuário sem privilégios,
a montagem somente leitura e a integridade dos arquivos. Foram acrescentados
casos de regressão para permissões restritivas e preservação da origem. O gate
Docker foi depois aprovado pelo autor, conforme o fechamento abaixo. A execução
GitHub Actions permanece pendente; não se repete treino histórico.
[Procedimento](../operations/SERVING_ROLLOUT.md#correção-das-permissões-da-fixture--2026-10-19).

### Entrega construída e qualificada no laboratório — aceite de 2026-10-19

O teste nativo passou em 62,04 s: uma imagem vinculada ao commit foi construída,
validada por HTTP, submetida à troca/rollback sintéticos e exportada. O pacote
contém 11 arquivos; a imagem exportada tem 175.020.032 bytes. O descritor tem
hash informado e conferido no host pelo autor. Recursos temporários e tag
exclusiva foram removidos, sem acesso ou novo treino da referência histórica.

O resultado demonstra o preparo de uma entrega identificada, não sua execução
em outro alvo. O próximo gate carrega o arquivo verificado e confirma identidade
e HTTP; a execução da CI no GitHub é uma prova separada. Não há publicação em
registry, promoção de modelo ou implantação permanente neste aceite.
[Evidência e limites](../../references/evidence/serving_delivery_author_validation_2026-10-19.json).

### Gate de consumo e CI entre jobs — preparação de 2026-10-19

O consumidor verifica o pacote por commit/hash externos, carrega a imagem exata
e testa HTTP em um container novo, sem rebuild ou fit. A entrega original e
as tags alheias são preservadas. A CI encadeia `quality` → `serving-delivery` →
`serving-consumer`; o último job recebe o artifact e o hash do produtor em outro
runner. Implementação, aceite nativo e **GitHub Actions aprovados**; detalhes no aceite remoto abaixo.
O teste local usa o arquivo já aprovado e não exige repetir o build.
[Procedimento, pins e limites](../operations/SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Três jobs do GitHub Actions aprovados; próximo: promoção explícita e provisionamento**, sem repetir
os gates locais encerrados. [Resultado e limites](../operations/SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).

### CI de entrega e consumo aprovada — 2026-10-19

A [execução 39306262564](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
passou nos três jobs do commit `6a3782b0ccb9d6c0a098251743483aed73e49079`:
qualidade → build/qualificação → consumo do artifact em outro runner. A API
confirma sucesso e runners distintos; o artifact pequeno do consumidor foi
baixado, teve digest conferido e seus recibos de recarga/HTTP/cleanup lidos.
**Gate remoto encerrado.** Próximo: promoção explícita e provisionamento
reproduzível KinD/Helm; registry e deploy permanente ainda têm critérios próprios.
[Resultado e limites](../operations/SERVING_ROLLOUT.md#aceite-do-fluxo-no-github-actions--2026-10-19).

## MLflow no KinD — aceite nativo de 2026-10-20

MLflow instalado via Helm sobre PostgreSQL/RustFS dedicados. O autor aprovou
publicação, recarga independente HTTP, comparação dos bytes do modelo no S3 e
recarga após recriar o pod MLflow, com erro zero e fundação preservada. O gate
nativo passou em 21,56 s. Isso demonstra persistência e integração operacional
com uma fixture sintética; a migração histórica e o backup/restore deste novo
destino continuam pendentes. Próximo recorte: objetos/dados no RustFS do KinD,
com aceites próprios. [Resultado e evidência](../operations/KIND_TRACKING.md#aceite-nativo--2026-10-20).

## Transporte dos datasets no KinD aprovado

O autor confirmou o ciclo das quatro camadas em 2026-10-20: 489 arquivos de
payload e quatro manifestos recuperados com verificadores próprios; os reenvios
não fizeram novos uploads e a fundação permaneceu com as mesmas identidades.
O resultado preserva dados e proveniência no novo destino. Os leitores ainda
usam seus caminhos atuais; recuperação após perda da máquina não foi avaliada.
[Aceite e evidência](../operations/KIND_DATASETS.md#aceite-nativo--2026-10-20).

## Produtores em Jobs aprovados no KinD

O autor confirmou Bronze → Silver → Gold em dois Jobs Helm em 2026-10-20:
Silver com 1.754.155 transações e Gold com 402.877 linhas/19 features. As novas
saídas foram publicadas e recuperadas independentemente; o segundo Job usou
exatamente o bundle produzido pelo primeiro. Fundação preservada e Jobs
removidos após sucesso. O resultado demonstra execução e recuperação da cadeia,
com transformação separada das credenciais. Não altera a evidência de qualidade
do modelo nem os caminhos primários dos leitores.
[Aceite e limites](../operations/KIND_PIPELINE.md#aceite-nativo--2026-10-20).

## Retomada do replay no KinD aprovada

Em 2026-10-20 o autor confirmou que o replay sintético retoma após interromper
um pod durante uma transação de feedback: o novo pod usa o mesmo armazenamento,
reverte o evento incompleto e preserva os resultados confirmados. A comparação
integral resultou em 20 eventos, 10 scores e 6 alertas iguais aos da execução
sem interrupção. Reentrega não recalculou scores. O ensaio foi encerrado e seu
PVC ficou retido; histórico e modelo de referência permanecem preservados.
O resultado demonstra recuperação operacional neste cenário, sem nova afirmação
de qualidade do modelo ou proteção contra perda da máquina.
[Aceite e evidência](../operations/KIND_REPLAY.md#aceite-nativo--2026-10-20).
Próximo recorte: serving no KinD, com identidade da release, troca e rollback.

## Serving no KinD aprovado

O autor aprovou os seis eventos do gate em 2026-10-21: baseline, bloqueio de
pacote inválido, candidata direta, troca na mesma URL, falha HTTP 502 e retorno
por `helm rollback`. A imagem qualificada da API foi reutilizada, sem rebuild
do runtime ou fit; somente o carrier foi construído. Os mesmos pesos sintéticos
produziram score `0.03463335444035967` antes/depois da troca e do rollback.
Baseline/gateway mantiveram seus pods e a fundação permaneceu igual; candidata
removida, baseline instalada no laboratório e port-forward temporário encerrado.
A atualização controlada agora tem evidência no Kubernetes: Service apontando
para o pod candidato, indisponibilidade observada e recuperação da baseline na
mesma URL do ensaio. O retorno foi observado em 1,99 s, sem constituir SLA.
O próximo recorte integra observabilidade ao cluster. Parada/subida e retomada
após reboot continuam pendentes; não há evidência de que reboot causou a falha
anterior de importação. [Aceite e limites](../operations/KIND_SERVING.md#aceite-nativo--2026-10-21).

## Observabilidade no KinD aprovada

O autor aprovou o gate nativo e confirmou a conferência visual em 2026-10-21.
A cópia somente de leitura foi conciliada com a origem: **556.824 eventos,
143.945 decisões e 1.453 alertas**. Scrape Prometheus, oito regras saudáveis,
proxy autenticado do Grafana e quatro dashboards provisionados foram conferidos.
O exporter ganhou novo pod, manteve Deployment/PVC e repetiu as verificações.
Prometheus, Grafana, fundação, tracking, serving e PVC retido do replay
preservaram suas identidades; arquivos da origem permaneceram iguais.
A coleta atual apresenta um replay concluído, sem nova ingestão ou resultado
de qualidade do modelo. Próximo: operação de parada/subida, reboot e fechamento
integrado. [Evidência e limites](../operations/KIND_OBSERVABILITY.md#aceite-nativo-e-visual--2026-10-21).

Refinamento visual aprovado em 2026-10-21: [política semântica de cores](../operations/DASHBOARD_COLORS.md#aceite-da-política--2026-10-21),
com informações azuis e estados explícitos em texto/cor. Configuração verificada
pela API antes/depois de recriar o exporter e conferência visual confirmada pelo
autor. Métricas, arquivos de origem/cópia e três PVCs iguais ao aceite anterior.
Próximo: operação de parada/subida, retomada após reboot e fechamento integrado.


## Laboratório retomado após reboot

O autor demonstrou parada/subida e retomada depois de reiniciar a máquina: mesmos volumes, identidade e score da API, histórico de tracking e indicadores operacionais. O ajuste inotify persistiu. Isso encerra o marco KinD/Helm no escopo do laboratório; agora a prioridade é simplificar a reprodução e a apresentação do portfólio. Recuperação por perda do host e interrupção abrupta seguem fora desse recorte. [Runbook](../operations/KIND_OPERATIONS.md).


## Portal orientado à investigação — preparação em 2026-10-22

| Usuário | Entrega |
| --- | --- |
| Sistema transacional | API com score e identidade do modelo |
| Analista | Fila priorizada, contexto, feedback e explicação SHAP ligada ao caso |
| Gestor de risco | Indicadores da janela e cobertura das informações |
| Operação | Grafana com saúde, erros, atrasos e qualidade de dados |
| Equipe de ML | MLflow com linhagem, comparação e implantação governada |

O portal Streamlit/Plotly reúne a jornada do analista em três telas conectadas.
Inclui todas as decisões do fluxo HTTP aceito; somente cinco têm a explicação
SHAP já aceita. Não transforma feedback tardio em sinal disponível na decisão.
Implementação/testes locais preparados; aceite visual e captura nativa pendentes.
[Guia](../operations/FRAUD_PORTAL.md).


### Evidência do modelo e interpretação do produto

A área de desempenho preparada permite ao gestor e à equipe de ML comparar
candidatos na validação temporal, ver Precision–Recall/ROC e entender recall,
falsos positivos e carga por cliente/dia. O analista conserva o contexto por caso.
A UI lê evidência identificada e não depende de HGB: uma nova versão/algoritmo
entra por outro snapshot e relatório compatível. Explicações ausentes são
informadas; não há explicações fabricadas nem score tratado como certeza.
[Aceite da nova página ainda pendente](../operations/PORTAL_PERFORMANCE.md).
