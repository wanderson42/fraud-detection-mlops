# Mural de metas — Fraud Detection MLOps

Atualizado em **2026-10-19**. Backlog principal, em ordem de dependência e valor
para a demonstração. O [README](../../README.md) apresenta o projeto; a
[arquitetura](../ARCHITECTURE.md) descreve os componentes e os
[stakeholders](STAKEHOLDERS.md) interpretam os resultados.

## O que queremos demonstrar

Priorizar investigação com dados e features causais, modelo identificado,
feedback atrasado, operação observável, recuperação e atualização controlada.
O diferencial de portfólio é conseguir explicar uma decisão e demonstrar o que
acontece quando o processo para, os dados mudam ou um candidato é rejeitado.
O painel torna essas evidências visíveis. Dados simulados e laboratório local
não comprovam eficácia antifraude real nem SLA de uma instituição.

## Marcos concluídos e evidência preservada

| Marco | Estado e limite |
| --- | --- |
| Bronze, Silver e Gold | 183 dias; 1.754.155 transações; 19 preditores causais; contratos, manifestos e verificadores |
| Referência e experimentos | HGB congelado, três ablações e 20 trials/63 fits; nenhum candidato elegível |
| Avaliação temporal | Maio: AP 0,640703; referência de 02–15/09: AP 0,621312; reservas consumidas |
| Estatística exploratória | Dependência e reamostragem documentadas; sem intervalo de generalização ou superioridade formal |
| Tracking e serving | MLflow/skops, HTTP/wheel e Docker validados pelo autor; imagem e release identificadas |
| Política e estado causal | Primeiro cruzamento do limiar 0,8826493808370081; até 100 clientes/dia; feedback separado sete dias depois |
| Replay nativo e correção | 556.824 eventos/143.945 scores; 19 features exatas e gate de scores aprovado; 1.453 alertas preservados |
| Resultado descritivo da fila | 768 capturas/358 perdas de clientes-dia fraudulentos em 16–30/09; precisão 52,86% e recall 68,21% agregados |
| Replay programado nativo | 556.824 eventos em 14 sessões a 8.640×; 143.945 decisões medidas, zero lacunas e paridade independente aprovada pelo autor; não avalia SLA |
| Visibilidade local inicial | Stack Docker ativa, scrape Prometheus e valores dos dois dashboards confirmados pelo autor no smoke; o fechamento nativo está registrado na linha seguinte |
| Recuperação PostgreSQL/S3 do tracking | Aceite de 2026-10-19: 38 objetos, 8 runs, 6 modelos e 44 pontos históricos; modelo pelo proxy com erro zero; fonte preservada e alvo removido |
| Coleta nativa e recuperação do exporter | Resumo/Prometheus conferidos no volume final; alerta observado em 56,36 s e retorno em 12,29 s, arquivos preservados; rotina incorporada com pytest/tox |

O replay corretivo já foi executado e verificado pelo autor. Não volta ao backlog
como execução futura. [Recibo e origem](../../references/evidence/native_numerical_correction_author_validation_2026-10-13.json).
Os protocolos, os resultados anteriores e seus checkpoints permanecem preservados.
Os números de testes históricos descrevem a entrega correspondente, não a CI de
um commit ainda não publicado.

## Backlog ordenado

| Ordem | Entrega | Estado atual | Critério de conclusão |
| --- | --- | --- | --- |
| 1 | Replay programado: fechamento e desempenho representativo | Ensaio nativo acelerado concluído; idempotência e crash/retomada demonstrados no smoke gerado; custos parciais registrados | Preservar o ensaio aprovado; definir protocolo e orçamento de recorte 1x antes de executar. O ensaio completo acelerado não aprova SLA nem recuperação de todo tipo de falha nativa |
| 2 | Monitoramento e dashboards locais | Coleta nativa e falha/alarme/retorno do exporter validados pelo autor; rotina versionada com controle pytest aprovado e teste real tox explícito; painéis confirmados no smoke | Manter o teste real explícito e concluir conferência visual dos valores nativos finais; a camada de dados/features e o ensaio 1x têm critérios próprios |
| 3 | Armazenamento, recuperação e destinos dos produtores | Archives de datasets/estudo/tracking e recarga da referência aprovados pelo autor; inventário do checkout aprovado no recorte local; contenção inicial de quatro CLIs aprovada pelo autor; ensaio isolado MLflow/PostgreSQL/S3 aprovado após recriação de containers | Contribuição HTTP, fold HGB e trio inicial aprovados pelo autor. Revisão nativa aprovada: 17 registros/sete fronteiras locais, 48 testes. Captura nativa aprovada (38 objetos, 8 runs, 6 modelos, 44 pontos de métricas). Restore isolado aprovado pelo autor em 2026-10-19: história API conciliada, modelo recarregado com erro zero, fonte preservada e cleanup concluído. Recorte de inventário/recuperação encerrado; avançar para CD/rollback e monitoramento. Cutover, consumidores históricos e limpeza ficam no backlog próprio. Classificação não comprova recuperação; sem remoção de originais |
| 4 | CD e rollback de release | Primeiro gate blue-green nativo aprovado em 2026-10-19: A → B → A, rejeição de pacote inválido, falha 502 e rollback verificado em 3,10 s; pesos e imagem preservados | Ensaio de troca/retorno concluído. Próximo incremento: fluxo versionado de entrega com imagem/release fixadas, gates, promoção explícita e rollback documentado; depois provisionamento via Helm. Não reabrir o gate aprovado como pendência |
| 5 | Monitoramento de dados/features e gestão de features | Contratos e paridade existentes; próxima extensão após métricas operacionais | Medir schema, ausência, frescor/atraso e distribuições de features com suporte; baseline temporal versionado e testes de resposta. Não confundir score drift com feature drift |
| 6 | CT controlado | Backlog, depende de 2–5 | Gatilho explícito, labels disponíveis, orçamento, treino e avaliação temporal comparável; candidato rejeitado ou promovido com gates e rollback. Drift sozinho não promove |
| 7 | Orquestração de jobs | Condicional à composição real de jobs | Coordenar treino/avaliação/publicação, retries e estados; escolher ferramenta pelo problema demonstrado. O consumidor de eventos continua processo próprio |
| 8 | Spike de feature store | Condicional aos critérios abaixo | Demonstrar reutilização, joins point-in-time corretos, disponibilidade/frescor online, paridade, recuperação e custo comparados à solução atual |
| 9 | Transporte externo e carga sustentada | Condicional à necessidade demonstrada | Broker, política de atraso/reentrega, backpressure, falha e recuperação medidos sob carga declarada; não inferir entrega distribuída a partir de uma fila em processo |
| 10 | Provisionamento local em KinD + Helm | Destino final escolhido pelo autor; implementação adiada até recuperação e rollback estáveis | Criar cluster limpo, instalar versões fixadas com um procedimento reproduzível, validar persistência, saúde e rollback; comparar custo operacional com a bancada Compose. Terraform/cloud não são pré-requisitos |
| 11 | Novo ciclo de modelos e confirmação estatística | Condicional a hipótese/candidato e avaliação preservada | Novo desenho antes do acesso, efeito relevante e dependência temporal/por entidade tratados; não reutilizar maio ou setembro como confirmação nova |

O replay acelerado da ordem 1 e os checks de coleta/indisponibilidade da ordem 2
estão concluídos no escopo relatado. A conferência visual final dos painéis ainda
precisa ser informada, sem repetir a janela nativa. O recorte de inventário e
recuperação da ordem 3 está encerrado pelo aceite de 2026-10-19. A próxima
prioridade é conectar o gate aprovado da ordem 4 ao fluxo versionado de entrega.
Um recorte 1x precisa de protocolo
e não deve bloquear os demais marcos por exigir semanas de replay integral.
Armazenamento de objetos antecede CD/CT: não fica escondido atrás de Kubernetes ou de novas
buscas de hiperparâmetros. Dados/features, CT, orquestração e inferência por eventos
continuam no plano. O incremento de Prefect de checagem isolada foi retirado;
um futuro orquestrador depende de uma composição de jobs que o justifique.

As metas condicionais de CT, orquestração, feature store e broker não são
pré-requisitos para KinD + Helm. A migração depende dos critérios operacionais
de persistência/restauração e rollback, não de instalar toda a lista de ferramentas.

## Monitoramento local — marco exercitado

O autor concluiu o ensaio nativo: 556.824 eventos, 143.945 decisões e 1.453 alertas,
em 14 sessões, com zero commits sem medição. A verificação confirmou features,
scores e política; a qualidade repetiu a referência já consultada.
[Evidência e limites](../../references/evidence/paced_native_author_validation_2026-10-15.json).
O p95 de processamento foi 23,71 ms, enquanto agendado→commit chegou a 379,57 s
no p95 sob aceleração 8.640×. Não somar quantis das etapas nem interpretar o
processamento como inferência isolada. Os máximos e a ausência de ensaio contínuo
1x permanecem registrados no runbook.

O autor confirmou coleta válida e os 556.824 eventos no Prometheus; o teste do
exporter retornou alerta em 56,36 s, recuperação em 12,29 s e arquivos inalterados.
[Evidência](../../references/evidence/exporter_recovery_author_validation_2026-10-15.json).
O utilitário foi incorporado fora dos scripts identificados do replay, com testes
de controle no tox padrão e execução real explícita. Não é necessária nova pontuação.

[Runbook de observabilidade](../operations/OBSERVABILITY.md) e
[protocolo](../../references/paced_monitoring_protocol_v1.json) concentram execução,
identidades, métricas e limites. A fila limitada fica entre produtor programado e
consumidor durável. O contexto anterior ao início da janela é aquecido sem pacing
e excluído das latências. Cada retomada tem uma nova âncora; lacunas de tempo
após commit são explícitas, sem fabricar amostras de latência.

O exporter lê somente o estado do novo ensaio. Não reabre Silver, não pontua
novamente e não consulta o oráculo. Qualidade usa dias completos com feedback
amadurecido; AP é transacional, precisão/recall da fila são por cliente/data.
Jensen–Shannon dos scores é um sinal descritivo com suporte, sem CT automático.
A observabilidade de features completas e o monitoramento de recursos do host
continuam com evidência própria; os custos do replay são coletados por invocação.

Velocidade > 1 é aceleração. O alvo local de p95 agendado→commit ≤ 1 s só pode
ser examinado em execução 1x contínua, completa, sem lacunas de tempo e no escopo
de fila local. Uma janela inteira em ritmo 1x dura semanas: comece pelo ensaio
acelerado, registre seu custo e planeje um recorte 1x sob protocolo antes de
alterar a janela. Não tratamos o p95 HTTP sequencial como latência do replay.

## Objetos S3 e custo inicial

O [plano de armazenamento](../operations/STORAGE.md) inclui desde Bronze/Silver/Gold
até tracking, estado, métricas e eventual feature store. DuckDB é o motor de
consulta; S3 guarda conjuntos publicados; bancos ativos exigem disco persistente
e backup consistente. PostgreSQL é o destino escolhido para o servidor MLflow,
com migração versionada/reconciliada própria. Inventário e piloto S3 limitados
passaram testes locais e o autor validou o round-trip de dois documentos no RustFS.
Os dez arquivos foram interpretados e a release completa foi restaurada e
recarregada: identidade preservada e erro zero no score histórico. Agora ampliar
com critérios específicos por camada; o limite de 64 arquivos/64 MiB pertence ao
piloto e não cobre todos os conjuntos nativos. A ampliação precisa ter orçamento
explícito e preservar manifestos/bytes, sem fragmentação manual só para contornar o teto.
O preparo Bronze tem perfil separado e verificação com inventário arquivado;
o autor aprovou 183 partições, 188 arquivos e 107.309.243 bytes. A transferência
para `fraud-datasets` já usa um job por comando e um cliente S3 por job, com
manifesto por último, reenvio idempotente e restore submetido ao verificador
Bronze. O autor aprovou o build e as três operações: 189 objetos publicados,
zero uploads/189 reutilizados e 183 partições restauradas/verificadas.
[Evidência](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
Os gates de arquivamento/recuperação Bronze e Silver estão fechados. O autor
aprovou o preparo e round-trip Silver: 187 arquivos/40.288.931 bytes, contrato,
inventário e auditoria, 188 objetos e reenvio sem uploads. A cópia passou schema
e SQL: 183 partições/1.754.155 linhas e IDs distintos, com contagens históricas
reconciliadas. [Evidência Silver](../../references/evidence/silver_archive_author_validation_2026-10-15.json).
O inventário Gold foi recebido: 506 arquivos/326.821.198 bytes em datasets,
estudos e resultados. O primeiro perfil cobre `gold_v1` (43 arquivos no snapshot,
mais auditorias próprias e quatro referências), pois já tem verificador específico.
O autor aprovou preparo, publicação/reenvio/restore Gold no RustFS: 48 arquivos,
25.196.448 bytes, 49 objetos e zero uploads no reenvio. `verify_gold` sobre a cópia
com quatro referências baixadas aprovou 42 partições/19 features/402.877 linhas
(268.668 treino, 67.255 validação, 66.954 teste). Gate `gold_v1` fechado.
[Evidência nativa Gold](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
O inventário de desenvolvimento delimitou 64 arquivos/38.159.334 bytes de dataset
e 319 arquivos/232.943.522 bytes de estudo, com SQLite de 540.672 bytes. O perfil
do dataset foi validado pelo autor com inventário/protocolo HGB: 66 arquivos,
38.202.741 bytes, 67 objetos e reenvio sem uploads. O restore aprovou 63 partições,
19 features e 603.669 linhas sem ler originais ou estudo, preservando run ID e
hash do manifesto. Gate de arquivamento/recuperação do dataset fechado.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
O preparo offline do estudo foi implementado/testado: lock de escritor real,
SQLite Online Backup API e cópia coordenada de artefatos, com integridade/digest
lógico e verificador existente sobre visão descartável. Vínculos por run ID,
hash do manifesto e bundle do dataset; relatórios/URIs históricos preservados.
Preparo nativo aprovado: 322 arquivos/233.048.483 bytes, 20 trials concluídos,
63 tentativas e decisão `retain_reference` preservada.
[Evidência](../../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
A transferência agora usa o worker existente em `fraud-artifacts/hgb-study/v1/`,
com dependência explícita e verificador antes do destino final. O autor aprovou
publicação/reenvio/restore em 2026-10-16: 323 objetos, zero uploads no reenvio e
322 arquivos/233.048.483 bytes restaurados, preservando 20 trials, 63 tentativas,
hash lógico e vínculo com o dataset. Gate de recuperação do estudo fechado.
[Evidência nativa S3](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
Não há migração para PostgreSQL ou retomada de treino neste passo.
[Preparação e limites](../../references/evidence/hgb_study_backup_preparation_2026-10-15.json).
[Preparação](../../references/evidence/development_gold_archive_preparation_2026-10-15.json).
Adoção pelos leitores, cobertura dos demais modelos/avaliações,
tracking e seus backups continuam com gates próprios. O grupo de
desenvolvimento mistura dataset e estudo; o código Optuna usa SQLite, que exige
backup consistente. Não ampliar o teto só para copiar a família como um conjunto.
[Evidência de preparação](../../references/evidence/silver_archive_preparation_2026-10-15.json).
Incremento atual: inventário MLflow somente leitura implementado/testado, com
critérios de backup, transferência PostgreSQL + S3, IDs/URIs, recarga e rollback.
Coleta corrigida e revisão do Default concluídas pelo autor: zero referências
não resolvidas/localizações gerenciadas ausentes; Default sem runs/modelos ou
diretório. Gate de inventário fechado; preparo e verificação de backup offline banco + artefatos
implementados/testados, com preparo/verificação nativos aprovados e publicação, reenvio idempotente e recuperação S3 aprovados pelo autor.
[Procedimento e gates](../operations/MLFLOW.md#inventário-antes-da-migração-postgresql--s3).
Não é necessário migrar o SQLite do estudo concluído para avançar.
Inventário e restauração precedem a adoção de S3 como cópia canônica; nenhuma
camada está declarada migrada nesta entrega.

KinD + Helm é a meta final de provisionamento reproduzível, conforme decisão do
autor. Compose permite medir e validar os componentes agora; a migração virá
depois dos critérios de persistência/restauração e rollback. Não implica
duplicar imediatamente toda a infraestrutura nem contratar nuvem.

**Decisão: armazenamento S3 compatível local, com RustFS como backend inicial.**
Não exige conta de nuvem. Prometheus, Grafana OSS e RustFS rodam em perfis locais;
RustFS é opcional; o piloto real de documentos passou, sem concluir migração ampla.
[Configuração](../../infra/observability/README.md). Imagens são fixadas por digest,
credenciais são geradas localmente e as portas publicadas ficam no loopback.

A meta de objetos inclui datasets/artefatos imutáveis, releases, manifests,
relatórios e backups consistentes de estado. SQLite ativo continua em disco
local: S3 não substitui seu sistema de arquivos, locking ou commit atômico.
Restaurar e conferir hashes é requisito; apenas iniciar o serviço não fecha o
marco. Um único disco/nó RustFS não fornece redundância ou proteção contra perda
da máquina. Outra cópia independente continua necessária para disaster recovery.

Sem despesa de serviço cloud nesta fase; CPU, RAM, disco, energia e manutenção
da máquina continuam sendo recursos consumidos. Medir tempo/CPU/RSS por
invocação e volumes em disco, limitar retenção do Prometheus a 48 h/512 MB e
usar somente os perfis necessários. Não adicionar serviços pagos como requisito.

## Quando uma feature store vale a pena

A gestão atual já inclui schema, 19 features causais, versão da regra numérica,
features offline e estado online com paridade independente. Uma feature store
pode ajudar quando duas ou mais aplicações/treinos realmente reutilizarem as
mesmas features e o trabalho de materialização/frescor precisar de gestão comum.
O spike, por exemplo com Feast local, só começa com esse caso e orçamento claros.

| Pergunta do spike | Evidência exigida |
| --- | --- |
| Qual consumidor adicional reutiliza quais features? | Consulta e materialização comparadas com o fluxo atual, sem duplicar lógica |
| O join histórico respeita o instante de decisão? | Testes de point-in-time e de disponibilidade: feedback conhecido só sete dias depois; um timestamp de ocorrência sozinho não resolve disponibilidade |
| O online respeita frescor e atraso? | TTL, freshness, política de eventos atrasados, materialização, falha e recuperação explícitos |
| A semântica fica igual? | As 19 features e os scores mantêm a regra numérica e a paridade; não trocar a definição para acomodar a ferramenta |
| Qual custo e ganho operacional? | RAM, disco, processos, latência e esforço de manutenção medidos contra o estado SQLite atual |

Adoção depende do resultado. Não instalar uma feature store apenas para completar
uma lista de ferramentas. Não confundir bucket de Parquet com feature store online.

## Leitura e evidência

- [Stakeholders](STAKEHOLDERS.md), [Model Card](../modeling/MODEL_CARD.md) e [avaliação](../modeling/EVALUATION_PROTOCOL.md).
- [Replay corrigido](../operations/NUMERICAL_CORRECTIVE_REPLAY.md) e [monitoramento](../operations/OBSERVABILITY.md).
- [Prometheus: instrumentação e cardinalidade](https://prometheus.io/docs/practices/instrumentation/).
- [Grafana: provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
- [RustFS: implantação local](https://docs.rustfs.com/en/installation/container).
- [Feast: point-in-time joins](https://docs.feast.dev/getting-started/concepts/point-in-time-joins).

As prioridades são decisões deste projeto. Não representam certificação de
maturidade ou promessa de qualidade futura.

## Simplificação após maturação da infraestrutura

Critério de fechamento solicitado pelo autor: depois de persistência, recuperação
e rollback demonstrados, revisar scripts temporários, comandos redundantes,
dependências, testes e documentação substituídos. Remover o que não atende mais
a um fluxo suportado e consolidar interfaces por responsabilidade. Não criar
pastas permanentes de legados para evitar exclusões; o Git preserva o histórico.

Antes de excluir, verificar chamadas em código, CI, Makefile, notebooks e runbooks,
além dos vínculos dos verificadores com identidades históricas. Versionar adaptações
necessárias sem reescrever evidências antigas. Validar instalação limpa, execução
e recuperação dos fluxos atuais após a limpeza. Não há exclusões neste fechamento.


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

### Recuperação do tracking — marco concluído em 2026-10-16

Ensaio nativo concluído: histórico dos dois stores, hash/score da referência,
preservação dos restores e limpeza da cópia aprovados. Resultado e limites em [MLflow](../operations/MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).
A prova encerra esse recorte de recuperação, sem liberar automaticamente conversão
PostgreSQL, adoção S3 por leitores ou remoção de scripts/dados. Esses passos exigem
cobertura dos produtores, destinos, dependências e rollback. Feature store continua
condicional; KinD/Helm/PVCs permanecem após os critérios de persistência.

**Recuperação local MLflow aprovada pelo autor em 2026-10-16:** os dois stores
forneceram 71 runs, 67 logged models e 321 registros de histórico. A referência
recarregada reproduziu o score com erro zero; restores e tabelas foram preservados
e a cópia descartável removida. O gate fecha acesso local pelo SDK e um smoke de
inferência; serviço HTTP/PostgreSQL, migração primária e inventário global mantêm
critérios próprios. [Resultado e evidência](../operations/MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

## Inventário de persistência do checkout — recorte nativo revisado

Justificativa: o inventário inicial exclui `data/storage/` e arquivos fora de `data/`.
O novo coletor cobre metadados do checkout, com catálogo de responsáveis/produtores e
fronteiras externas declaradas. Implementado com 14 testes pytest/tox; coleta nativa
aprovada no recorte do checkout. Não cria serviço, migra storage, modifica writers ou autoriza limpeza.
[Comando e interpretação](../operations/STORAGE.md#inventário-do-checkout-e-catálogo-de-produtores).

Coleta e revisão inicial concluídas; os limites externos continuam explícitos.
Depois, ordenar cobertura faltante, contenção dos produtores e ensaio de serviço
MLflow/PostgreSQL + S3, antes do cutover. Inventário global e recuperação fora da
máquina permanecem abertos; Helm/PVCs e feature store mantêm seus critérios anteriores.

## Primeira contenção dos destinos — validada pelo autor

Quatro CLIs passam a recusar saídas fora de suas áreas em `data/`, antes de efeitos
caros; defaults de relatórios migram para `data/reports/`, sem mover arquivos.
Funções internas e outras CLIs mantêm cobertura pendente. Testes de regressão e
recusa antecipada aprovados nativamente: 82 testes no tox.
[Escopo e comandos](../operations/STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).

Próximo: ensaio isolado MLflow com PostgreSQL e S3, separado dos stores históricos,
validando escrita, leitura de artefato e recuperação antes de ligar consumidores.
Não há cutover ou exclusões neste incremento. Expansão da política, retenção,
1× contínuo, fechamento visual, rollback, Helm/PVCs e feature store conservam os
gates existentes; a feature store exige necessidade demonstrada.

## Incremento atual — serviço de tracking isolado

Justificativa: backups/recarga e destinos conhecidos permitem testar o serviço
sem transformar uma migração histórica em pré-requisito. O Compose dedicado
adiciona PostgreSQL com volume nomeado e MLflow com proxy para um bucket novo.
O papel da aplicação não é superusuário; o cliente do ensaio não recebe chaves S3.
A UI em `127.0.0.1:15001` permite observar o registro operacional na prática.

**Gate concluído em 2026-10-17:** o autor aprovou SQL, S3 e proxy HTTP antes e
depois da recriação dos dois containers dedicados. Os IDs mudaram e as imagens,
o volume PostgreSQL, histórico e bytes permaneceram iguais. Resultado: 1 teste
aprovado; tox `tracking-lab` OK.
[Evidência](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

Depois: adotar um produtor de novas runs com URI explícita e rollback; conferir
um artefato de modelo pelo serviço. Migração histórica/URIs, backup PostgreSQL,
leitores de datasets, contenção restante, limpeza e Helm/PVCs mantêm gates próprios.
Não exigir inventário de todo o Linux para iniciar esse ensaio. Feature store,
monitoramento de features, ensaio 1× e CD/rollback seguem priorizados pelos critérios
já descritos acima, sem acrescentar serviços sem necessidade demonstrada.

### Retentativa do ensaio: mensagens do SDK separadas do JSON

A primeira execução nativa parou no parser após a escrita/leitura HTTP do probe.
O worker passa a enviar mensagens do SDK para stderr, mantendo stdout como JSON.
A regressão usa um subprocesso real; o parser do controlador permanece estrito.
A retentativa posterior foi aprovada pelo autor: conferências SQL/S3/HTTP e
recriação concluídas. O registro anterior da falha permanece preservado.
[Retentativa](../../infra/tracking/README.md#retomar-após-a-falha-no-canal-json--2026-10-16).

### Primeiro produtor HTTP — aceite concluído

Justificativa: o serviço agora demonstrou persistência, mas os produtores ainda
usam os destinos anteriores. Conectar um único produtor de novas runs com URI
HTTP explícita e validar a recarga de seu artefato de modelo pelo proxy torna
o serviço útil no fluxo do projeto. O aceite deve incluir destino conferido,
paridade, ausência de saídas locais imprevistas e retorno ao destino anterior.
Adoção do exemplo de contribuição aprovada no serviço nativo, com recarga
independente e retorno local explícito. A integração HGB é o próximo gate. Histórico, backup
PostgreSQL, datasets primários, limpeza e Helm/PVCs conservam gates próprios.

O exemplo usa 80/20 linhas em memória e exercita modelo skops completo pelo proxy,
sem consumir Handbook. Isso justifica a primeira integração antes de alterar
baseline/HGB e suas identidades persistidas. A falha HTTP é explícita; voltar ao
local muda apenas o destino de novas runs. O aceite nativo deve preservar os
containers e comprovar recarga/paridade sem os arquivos locais disponíveis.
[Procedimento](../modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

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

### Incremento atual — tracking HTTP do HGB

O exemplo de contribuição foi aprovado pelo autor em 2026-10-17: 34 testes de
regressão e 1 teste nativo, recarga sem saída local disponível, erro zero e retorno
local explícito. [Evidência](../../references/evidence/model_contribution_http_author_validation_2026-10-17.json).
O gate antes descrito como pendente está concluído para esse produtor.

O HGB vem antes da ampliação ao baseline nesta etapa. A justificativa é avançar
no produtor de estudo temporal e proteger seu vínculo entre destino, identidade,
retomada e orçamento. A opção HTTP foi acrescentada para novos estudos; o próximo
aceite nativo é limitado a um fold sintético, sem repetir a busca Handbook.
SQLite permanece no estado Optuna; PostgreSQL recebe metadados MLflow do serviço.

Gate atual: recarga independente do HGB pelo proxy, erro até `1e-12`, saída local
indisponível, uma tentativa e reutilização sem fit/publicação, containers intactos.
O autor aprovou o fold nativo com erro zero e reutilização, em 2026-10-17.
Busca completa, consumidores locais,
inventário entre stores e backup PostgreSQL seguem abertos antes de adoção ampla.
[Procedimento](../operations/HGB_TRACKING.md).

### Incremento atual — os três baselines pelo tracking HTTP

O fold HGB foi aprovado pelo autor: 84 linhas de treino, 21 de validação, uma
tentativa e erro zero, sem publicação repetida ou alteração dos containers.
[Evidência](../../references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).

O próximo aceite cobre o trio inicial de `baseline_experiment.py`: dummy,
regressão logística e HGB, com uma run por modelo e recarga independente de todos.
O transporte HTTP é explícito; o local continua padrão. O teste usa uma Gold
sintética pequena, não repete a avaliação Handbook nem o estudo Optuna.

Gate: três runs concluídas e vinculadas, métricas conferidas, recarga sem modelos
locais disponíveis, paridade até `1e-12`, sem tracking SQLite ou mudança nos
containers. Falha parcial deve preservar audit e não publicar baseline concluído.
O autor aprovou o gate nativo do trio em 2026-10-17. Consumidores/recuperação dos
novos destinos precisam de critérios próprios antes da adoção primária; ampliar
infraestrutura exige justificativa.
[Procedimento](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Aceite do trio concluído — 2026-10-17

Dummy, regressão logística e HGB tiveram suas três runs e recargas independentes
aprovadas pelo autor: dados sintéticos 160/160, erro zero, modelos locais
indisponíveis e containers intactos. [Evidência](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).
Este ensaio não volta ao backlog como validação pendente.

A integração dos produtores desta etapa está fechada. O próximo planejamento
é localizar os consumidores que ainda assumem store local e delimitar uma prova
de recuperação PostgreSQL com os artefatos S3 correspondentes. A inspeção dos
consumidores pode ser somente leitura; uma mudança de destino primário deve
aguardar recuperação e rollback demonstrados. O estado remoto já existe, e
ampliar consumo antes dessa prova aumentaria a dependência de estado sem
restauração ensaiada. Este fechamento não implementa a próxima infraestrutura.
Helm/PVCs, limpeza, feature store e novas buscas continuam sujeitos aos critérios
existentes; nenhum novo serviço é necessário para registrar este aceite.

### Incremento atual — consumidores revisados e protocolo de recuperação

A revisão do consumo foi materializada no catálogo existente e no modo opt-in
do coletor. São 17 registros, com sete fronteiras locais que dependem de
identidade/hashes/ownership ou ferramentas históricas. Há detecção sintática de
fontes novas e divergência de código; sem alegar resolução completa de aliases
ou inventário global. O relatório do checkout do autor foi observado e aprovado no recorte declarado.

A captura coordenada e o restore PostgreSQL/S3 foram implementados e aprovados
pelo autor: volumes/rede isolados, histórico API completo e modelo/score fixados
por recibo anterior. O resultado encerra este recorte de recuperação. A próxima
entrega é CD/rollback do serving; as sete fronteiras de consumidores históricos
continuam mapeadas no backlog, sem ampliar esta etapa de inventário.
[Mapa, comando e critérios](../operations/TRACKING_RECOVERY.md).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte; o fluxo versionado de entrega é o
próximo incremento. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](../operations/SERVING_ROLLOUT.md).

### Continuação da entrega após o gate aprovado

Manter blue-green como direção do portfolio, com uma réplica ativa e candidata
durante a atualização. A versão anterior ficará disponível por uma janela de
rollback a definir; depois poderá ser reduzida a zero. Essa política e o fluxo
versionado ainda não estão implementados. Canary fica condicionado a tráfego
representativo, roteamento ponderado e critérios de observação; não é requisito
para encerrar este marco. [Decisão e próximos gates](../operations/SERVING_ROLLOUT.md#estratégia-escolhida-para-o-portfolio).
