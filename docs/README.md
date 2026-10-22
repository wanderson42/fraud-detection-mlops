# Índice da documentação

Caminho principal de execução: nova rodada com linhagem dos datasets, comparação, promoção e serving.
[Guia da rodada operacional](operations/OPERATIONAL_ROUND.md). Rodada inicial aceita pelo autor.
[Sucessores: comparação, busca opcional e aprovação de tráfego](operations/MODEL_UPDATES.md); retenção nativa aceita; ativação de sucessor com pesos diferentes ainda pendente.

[Entrega por usuário e execução eventos → HTTP → triagem](operations/PRODUCT_DELIVERY.md#executar-eventos-pela-api-do-champion): ensaio nativo aprovado em 2026-10-22. [Retomada do instalador da release](operations/SERVING_RECOVERY.md): correção preparada; aceite nativo específico pendente.

Histórico dos experimentos, comparação dos três baselines e Registry:
[transferência verificada para o KinD](operations/KIND_HISTORY.md).

<!-- kind-operations-acceptance-2026-10-21:start -->
**Estado em 2026-10-21 — marco KinD/Helm concluído no escopo do laboratório.**
Parada/subida e reboot planejado aprovados no mesmo node e volumes, com inotify
persistente e leituras de tracking, serving e monitoramento conferidas.
[Sequência integrada, aceites e limites](operations/KIND_PLATFORM.md). Próximo: simplificação
e fechamento funcional do portfólio; Terraform permanece posterior. Notas
datadas abaixo preservam o estado de cada incremento, sem reabrir gates aceitos.
<!-- kind-operations-acceptance-2026-10-21:end -->


Leia o [README do projeto](../README.md) para começar e a
[arquitetura](ARCHITECTURE.md) para localizar responsabilidades.

| Pergunta | Referência principal |
| --- | --- |
| Qual problema e quais próximos marcos? | [Contexto](project/PROBLEM_CONTEXT.md), [mural](project/ROADMAP.md), [stakeholders](project/STAKEHOLDERS.md) |
| Como mantemos a documentação? | [Política editorial](project/DOCUMENTATION_POLICY.md) |
| Como dados e features são construídos? | [Pipeline](data/DATA_PIPELINE.md), [Silver](data/SILVER_CONTRACT.md), [Gold](data/GOLD_CONTRACT.md) |
| O que significam os campos e os achados? | [Dicionário](data/DATA_DICTIONARY.md) e [EDA](data/EDA.md) |
| Como adicionar um modelo? | [Contrato e contribuição](modeling/CONTRIBUTING_MODELS.md) |
| Como avaliamos e selecionamos? | [Baseline](modeling/BASELINE.md), [avaliação temporal/Optuna](modeling/EVALUATION_PROTOCOL.md), [ablações](modeling/EXPERIMENT_PROTOCOL.md) |
| O que concluímos em setembro e quais janelas foram consumidas? | [Resultado e limites](modeling/EVALUATION_PROTOCOL.md#resultado-nativo-da-reamostragem-e-fechamento); [protocolo original](modeling/EVALUATION_PROTOCOL.md#protocolo-estatístico-da-referência--v1); [replay e correção](operations/NUMERICAL_CORRECTIVE_REPLAY.md) |
| O que dizem os diagnósticos e limites do modelo? | [Diagnóstico](modeling/DIAGNOSTICS.md) e [Model Card](modeling/MODEL_CARD.md) |
| Como executar, recuperar e migrar? | [Operações](operations/OPERATIONS.md), [ablações](operations/EXPERIMENT_EXECUTION.md), [MLflow](operations/MLFLOW.md) |
| Como verificar uma mudança? | [Testing](operations/TESTING.md) |
| Como exportar, iniciar e conferir HTTP/wheel/Docker? | [Serving](operations/SERVING_CONTRACT.md) |
| Como a CI constrói e qualifica a imagem vinculada ao commit? | [Entrega versionada e artifact](operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19) |
| A troca blue-green e o rollback já foram aprovados? | [Aceite nativo, estratégia e próximo incremento](operations/SERVING_ROLLOUT.md#aceite-nativo--2026-10-19) |
| Como separar transação/feedback e retomar o replay causal local? | [Contratos, executor e limites](operations/CAUSAL_REPLAY.md) |
| Como corrigir e conferir a paridade numérica? | [Correção versionada](operations/NUMERICAL_CORRECTIVE_REPLAY.md) e [resultado nativo](operations/NUMERICAL_CORRECTIVE_REPLAY.md#resultado-nativo-informado-pelo-autor--2026-10-13) |
| Como medir replay e abrir os dashboards locais? | [Observabilidade](operations/OBSERVABILITY.md) e [infra local](../infra/observability/README.md) |
| Como testar a recuperação do exporter com pytest/tox? | [Teste explícito e evidência](operations/OBSERVABILITY.md#falha-do-exporter-e-recuperação-informadas-pelo-autor--2026-10-15) e [isolamento da suíte](operations/TESTING.md#indisponibilidade-e-recuperação-do-exporter) |
| Como mapear arquivos, produtores e destinos fora de `data/`? | [Inventário do checkout e limites](operations/STORAGE.md#inventário-do-checkout-e-catálogo-de-produtores) |
| Como inventariar e recuperar conjuntos via S3? | [Piloto limitado](operations/STORAGE.md#piloto-implementado--execução-gradual), [Silver com contrato](operations/STORAGE.md#silver-completa-preparo-e-recuperação-com-contrato), [Gold com protocolo](operations/STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo) e [etapa 19](../notebooks/stages/19_storage_inventory_and_recovery.ipynb) |
| Por que o backup MLflow bloqueou no store de interface? | [Correção de cobertura dos dois stores](operations/MLFLOW.md#cobertura-dos-dois-stores-mlflow--correção-de-2026-10-16) |
| O ciclo S3 do snapshot MLflow já foi validado? | [Resultado nativo e próximo gate operacional](operations/MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16) |
| Como foi validado o uso do histórico/modelo restaurados? | [Resultado nativo, evidência e limites](operations/MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16) |
| Como publicar e recuperar o snapshot MLflow em S3? | [Job existente, limites e gate nativo](operations/MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow) |
| Como preparar o backup consistente do tracking? | [Captura offline, destinos, verificação e gate nativo](operations/MLFLOW.md#backup-offline-do-tracking-preparo-e-verificação) |
| Como inventariar MLflow antes de PostgreSQL + S3? | [Inventário revisado, cobertura e critérios de backup/migração](operations/MLFLOW.md#inventário-antes-da-migração-postgresql--s3) |
| Como publicar e recuperar o estudo HGB com seu dataset? | [Ciclo nativo aprovado, dependência e limites](operations/STORAGE.md#transferência-s3-do-estudo-hgb) |
| Como preparar um backup consistente do estudo Optuna/SQLite? | [Preparo offline, dependência e verificação](operations/STORAGE.md#backup-consistente-do-estudo-hgb-preparo-offline) |
| Como separar o dataset de desenvolvimento do estudo SQLite? | [Cobertura, ciclo nativo aprovado e próximo gate](operations/STORAGE.md#gold-de-desenvolvimento-dataset-e-estudo) |
| O arquivamento/recuperação Gold foi aprovado? | [Resultado e limites do autor](../references/evidence/gold_archive_author_validation_2026-10-15.json) e [runbook](operations/STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo) |
| Onde ficam datasets, artefatos, estado e métricas? | [Armazenamento e recuperação](operations/STORAGE.md): estado atual, destinos S3, bancos e critérios de migração |
| O replay programado nativo foi concluído? | [Resultado, custos parciais e limites](operations/OBSERVABILITY.md#resultado-nativo-informado-pelo-autor--2026-10-15) e [evidência do autor](../references/evidence/paced_native_author_validation_2026-10-15.json) |

Os protocolos executáveis e recibos históricos estão em
[`references/`](../references/). Os notebooks mantêm narrativa e resultados
selecionados; o runbook é a referência dos procedimentos reutilizáveis.


O inventário do checkout foi executado pelo autor: 4.990 arquivos, sem falhas de
coleta; o marcador vazio de `data/external/` foi revisado e os mounts principais
identificados. A primeira contenção exige áreas de `data/` em quatro CLIs de
exemplos, recibo Docker e inicialização de observabilidade. Não cobre todas as
APIs/CLIs nem migra consumidores. [Destinos, comandos e limites](operations/STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).


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

[Operação e próximos critérios](../infra/tracking/README.md).
[Evidência relatada e limites](../references/evidence/tracking_lab_author_validation_2026-10-17.json).

### Primeiro produtor HTTP — exemplo de contribuição

`examples.model_contribution` passa a aceitar `--tracking-uri` explicitamente,
como alternativa a `--tracking-root`. O exemplo registra um modelo skops e confere
recarga/paridade pelo proxy no experimento `fraud-model-interface-smoke-http-v1`.
São 80 linhas de treino e 20 de validação geradas em memória, sem acesso ao Handbook.
**Aceite nativo PostgreSQL/RustFS aprovado pelo autor em 2026-10-17:** recarga
independente com erro zero e retorno local explícito; detalhes no fechamento abaixo.

[Contrato, destinos e validação](modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

### Contribuição HTTP aprovada; próximo produtor: HGB — 2026-10-17

O autor aprovou a retentativa: **34 testes de regressão e 1 teste nativo**.
O modelo foi recarregado pelo serviço sem a saída local disponível, com erro
máximo zero. O retorno explícito ao tracking local descartável e a preservação
dos containers foram conferidos. A criação de tabelas no log pertence a esse
retorno local deliberado; não indica fallback silencioso do HTTP.
[Evidência do autor](../references/evidence/model_contribution_http_author_validation_2026-10-17.json).

A próxima integração prioriza o **HGB**, começando por um fold sintético com
identidade de destino, recarga independente e reutilização sem fit/publicação.
O estudo de 20 trials/63 tentativas e a decisão `retain_reference` ficam
preservados. O fold HGB foi aprovado pelo autor; o estudo completo não será
reexecutado para testar transporte. [Procedimento e limites](operations/HGB_TRACKING.md).

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
repetir os experimentos Handbook. [Tracking do trio e comandos](modeling/BASELINE.md#tracking-http-do-trio-de-baselines).
[Evidência HGB do autor](../references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).

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
[Evidência do autor](../references/evidence/baseline_tracking_author_validation_2026-10-17.json).

### Consumidores do tracking: revisão antes da recuperação

O inventário existente ganhou o modo explícito `--tracking-consumers`: confere
fontes/funções do catálogo sem importar módulos, abrir modelos/bancos ou consultar
serviços. A revisão preparada tem **17 registros e 7 fronteiras locais**:
ferramentas de histórico, diagnósticos, ablações, freeze, avaliação final,
avaliação estatística e exportação. A inferência da release permanece independente
do MLflow. Esse resultado não altera os consumidores ou migra o histórico.

A captura coordenada PostgreSQL/S3 e o restore em projeto/volumes/rede próprios
foram implementados e aprovados pelo autor. O resultado está resumido abaixo.
[Mapa, comando e protocolo](operations/TRACKING_RECOVERY.md).

**Recuperação da bancada aprovada pelo autor (2026-10-19):** PostgreSQL e 38
objetos S3 restaurados em volumes/rede próprios; 8 runs, 6 modelos e 44 pontos
históricos conferidos pela API. O modelo fixado recarregou pelo proxy e reproduziu
20 scores com erro máximo zero. Fonte/captura preservadas, alvo e credenciais
temporários removidos; pytest live passou em 20,89 s. **Recorte de inventário e
recuperação encerrado.** Próxima prioridade: CD/rollback do serving, seguido de
monitoramento de dados/features. Migração dos sete consumidores históricos,
cutover, limpeza e inventário global seguem em backlog próprio.
[Comandos, escopo e limites](operations/TRACKING_RECOVERY.md).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte. O build e a exportação da entrega
versionada também foram aprovados nativamente; o consumo do arquivo em outro
alvo tem aceite nativo informado pelo autor; CI remota aprovada no aceite abaixo. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](operations/SERVING_ROLLOUT.md).

### Qualificação da imagem na CI

A CI ganhou um job após `quality` que constrói a imagem uma vez, qualifica HTTP e
rollback com release **sintética** e exporta essa mesma imagem com hashes e recibos
vinculados ao commit. Sem dados históricos, publicação em registry ou deploy no
laptop. **Build, gates e exportação aprovados nativamente pelo autor em
2026-10-19:** uma imagem do commit `0b95136`, rollback verificado, 11 arquivos e
arquivo de imagem com 175.020.032 bytes. Recursos temporários e tag própria
removidos. A execução no GitHub Actions, a recarga do arquivo em outro alvo e a
promoção de uma release aprovada têm gates próprios. O artifact da CI está
configurado com retenção de três dias; não há aceite de upload remoto ainda. [Fluxo e limites](operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

### Gate de consumo e CI entre jobs — preparação de 2026-10-19

O consumidor verifica o pacote por commit/hash externos, carrega a imagem exata
e testa HTTP em um container novo, sem rebuild ou fit. A entrega original e
as tags alheias são preservadas. A CI encadeia `quality` → `serving-delivery` →
`serving-consumer`; o último job recebe o artifact e o hash do produtor em outro
runner. Implementação, aceite nativo e **GitHub Actions aprovados**; detalhes no aceite remoto abaixo.
O teste local usa o arquivo já aprovado e não exige repetir o build.
[Procedimento, pins e limites](operations/SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Três jobs do GitHub Actions aprovados; próximo: promoção explícita e provisionamento**, sem repetir
os gates locais encerrados. [Resultado e limites](operations/SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).

### CI de entrega e consumo aprovada — 2026-10-19

A [execução 39306262564](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
passou nos três jobs do commit `6a3782b0ccb9d6c0a098251743483aed73e49079`:
qualidade → build/qualificação → consumo do artifact em outro runner. A API
confirma sucesso e runners distintos; o artifact pequeno do consumidor foi
baixado, teve digest conferido e seus recibos de recarga/HTTP/cleanup lidos.
**Gate remoto encerrado.** Próximo: promoção explícita e provisionamento
reproduzível KinD/Helm; registry e deploy permanente ainda têm critérios próprios.
[Resultado e limites](operations/SERVING_ROLLOUT.md#aceite-do-fluxo-no-github-actions--2026-10-19).

### Monitoramento mínimo das features persistidas — preparação de 2026-10-19

O exporter existente ganhou schema/ausência e relógios das 19 features, além de
quatro distribuições com suporte e bins fixos. Compara primeiras e últimas
1.000 decisões do checkpoint, sem sobreposição; baseline operacional, não de
treino. Acrescenta painel Grafana e alertas sem gatilho de CT. Não carrega modelo,
recalcula features ou altera bancos nativos. Testes controlados concluídos;
**snapshot nativo e stack/painel pendentes**. Relatórios em
`data/reports/feature-monitoring/`, sem sobrescrita.
[Comandos, interpretação e limites](operations/FEATURE_MONITORING.md).

### Aceite nativo mínimo do monitoramento de features — 2026-10-19

O autor confirmou 116 testes aprovados e um opt-in ignorado, demonstração gerada,
snapshot nativo, exporter reconstruído, oito regras válidas e coleta Prometheus.
Os valores do dashboard foram conciliados com o snapshot: 1.000 decisões por
janela sem sobreposição, contagens de qualidade/relógio zeradas e quatro
divergências entre 0,001457 e 0,004398. O limiar descritivo é 0,1.
O aceite cobre esse recorte operacional; não mede concept drift, desempenho
futuro ou frescor de ingestão externa e não dispara CT/promoção. O firing dos
novos alertas não foi ensaiado na bancada. A contagem recebe cor fixa neutra,
com conferência visual após refresh ainda pendente. Segue o provisionamento
incremental do fluxo completo em KinD + Helm.
[Procedimento, explicação conceitual e evidência](operations/FEATURE_MONITORING.md#aceite-nativo-mínimo--2026-10-19).

### Fundação KinD/Helm — preparação de 2026-10-20

Primeiro incremento do fluxo completo: cluster dedicado de um node Kubernetes
1.35.8 fixado por digest, chart com RustFS/PostgreSQL e dois PVCs novos. Imagens
dos serviços preservadas dos pins já utilizados. Kubeconfig, caches, credenciais
privadas e recibos sob `data/storage/kind-platform/`; contexto explícito.
O ensaio grava marcadores próprios SQL/S3 e exige releitura após recriar os pods
com os mesmos PVCs. A execução nativa ainda está pendente. Não migra os dados
históricos, não troca o tracking e não comprova backup ou perda do cluster.
[Runbook, limites e comandos](operations/KIND_FOUNDATION.md).

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](operations/KIND_TRACKING.md#aceite-nativo--2026-10-20).

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.

O [transporte de bundles para o RustFS do KinD](operations/KIND_DATASETS.md#aceite-nativo--2026-10-20)
foi aprovado pelo autor nas quatro camadas: idempotência e restauração independente,
com os caminhos primários dos leitores preservados.

A [execução Bronze → Silver → Gold em Jobs Helm](operations/KIND_PIPELINE.md#aceite-nativo--2026-10-20)
foi aprovada pelo autor: novas saídas S3 restauradas, fundação preservada e
Jobs temporários removidos. Caminhos primários dos leitores mantidos.

- [Replay e recuperação de transação no KinD](operations/KIND_REPLAY.md):
  rollback de transação de feedback, novo pod no mesmo PVC, ledger integral e
  reentrega sem rescoring aprovados; PVC retido para inspeção.

- [Serving via Helm no KinD](operations/KIND_SERVING.md): consumo de runtime
  qualificado, entrega de release por init container, troca de Service e rollback
  Helm aprovados pelo autor em 2026-10-21; baseline instalada, candidata removida,
  fundação preservada. Próximo: observabilidade no KinD.

### Observabilidade no KinD aprovada

O autor aprovou o gate nativo e confirmou a conferência visual em 2026-10-21.
A cópia somente de leitura foi conciliada com a origem: **556.824 eventos,
143.945 decisões e 1.453 alertas**. Scrape Prometheus, oito regras saudáveis,
proxy autenticado do Grafana e quatro dashboards provisionados foram conferidos.
O exporter ganhou novo pod, manteve Deployment/PVC e repetiu as verificações.
Prometheus, Grafana, fundação, tracking, serving e PVC retido do replay
preservaram suas identidades; arquivos da origem permaneceram iguais.
A coleta atual apresenta um replay concluído, sem nova ingestão ou resultado
de qualidade do modelo. Próximo: operação de parada/subida, reboot e fechamento
integrado. [Evidência e limites](operations/KIND_OBSERVABILITY.md#aceite-nativo-e-visual--2026-10-21).

Refinamento visual aprovado em 2026-10-21: [política semântica de cores](operations/DASHBOARD_COLORS.md#aceite-da-política--2026-10-21),
com informações azuis e estados explícitos em texto/cor. Configuração verificada
pela API antes/depois de recriar o exporter e conferência visual confirmada pelo
autor. Métricas, arquivos de origem/cópia e três PVCs iguais ao aceite anterior.
Próximo: operação de parada/subida, retomada após reboot e fechamento integrado.


## Operação diária do KinD — aprovada

[Parada/subida e reboot planejado](operations/KIND_OPERATIONS.md): gate nativo e reboot planejado aprovados, com retomada pelo mesmo baseline. Consulte também o [guia integrado](operations/KIND_PLATFORM.md). Não confundir com backup/restore ou perda do host.


## Guia para executar e apresentar

[Execução e demonstração do portfólio](operations/PORTFOLIO_WALKTHROUGH.md): entradas Make para operar localhost, reproduzir os Jobs desde Bronze e escolher os ensaios. Expõe as ligações já demonstradas e as fronteiras ainda separadas.

- [Explicações dos casos do champion](operations/CASE_EXPLANATIONS.md): operação, vínculo com a decisão e relatório de investigação.
