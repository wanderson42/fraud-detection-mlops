# Índice da documentação

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
O gate Docker está concluído neste recorte; o fluxo versionado de entrega é o
próximo incremento. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](operations/SERVING_ROLLOUT.md).

### Qualificação da imagem na CI

A CI ganhou um job após `quality` que constrói a imagem uma vez, qualifica HTTP e
rollback com release **sintética** e exporta essa mesma imagem com hashes e recibos
vinculados ao commit. Sem dados históricos, publicação em registry ou deploy no
laptop. Implementação e controles offline concluídos; **build/gate Docker e
execução GitHub Actions pendentes**. O pacote da CI tem retenção de três dias;
recarga do arquivo em outro engine e promoção de uma release aprovada têm gate
próprio. [Fluxo e limites](operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).
