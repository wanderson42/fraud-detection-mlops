# Fraud Detection MLOps

Rodada canônica aceita no KinD: Bronze → Jobs Silver/Gold → três candidatos com datasets registrados → HGB versão 1 em `fraud-detector@champion` → API e rollback verificados.
[Sucessores com busca opcional e aprovação explícita de tráfego](docs/operations/MODEL_UPDATES.md): retenção nativa de candidatos aceita nas rodadas fixa e de busca; ativação de um sucessor com pesos diferentes ainda pendente.
[Guia da rodada operacional](docs/operations/OPERATIONAL_ROUND.md).
[Usuários e entregas do produto](docs/operations/PRODUCT_DELIVERY.md#usuários-e-entregas): sistema transacional, analista, operação e equipe de ML. O fluxo eventos → HTTP do champion → triagem passou no ensaio nativo. [Retomada idempotente do serving](docs/operations/SERVING_RECOVERY.md): manutenção aplicada e verificada no KinD, preservando o champion e a consulta de casos. [Explicações SHAP por caso](docs/operations/CASE_EXPLANATIONS.md): cinco casos do modelo ativo publicados e verificados no ensaio nativo, com 19 contribuições por caso e score reconstruído.


<!-- kind-operations-acceptance-2026-10-21:start -->
**Estado em 2026-10-21 — marco KinD/Helm concluído no escopo do laboratório.**
Parada/subida e reboot planejado aprovados no mesmo node e volumes, com inotify
persistente e leituras de tracking, serving e monitoramento conferidas.
[Sequência integrada, aceites e limites](docs/operations/KIND_PLATFORM.md). Próximo: simplificação
e fechamento funcional do portfólio; Terraform permanece posterior. Notas
datadas abaixo preservam o estado de cada incremento, sem reabrir gates aceitos.
<!-- kind-operations-acceptance-2026-10-21:end -->


Laboratório de MLOps para priorização de investigação de fraude, desenvolvido por
Wanderson Ferreira. Usa dados simulados do Fraud Detection Handbook, histórico
causal, feedback atrasado e evidências auditáveis. A inferência por streaming
para triagem próxima do tempo real é um objetivo central do projeto.

Com capacidade limitada de investigação, quais clientes priorizar usando apenas
a informação disponível no momento da decisão?

## Estado do projeto

Atualizado em **2026-10-17**. O laboratório percorre Bronze → Silver → Gold,
validação temporal, tracking MLflow/skops, serving HTTP/Docker e replay causal com
feedback atrasado. A referência HGB (Histogram-based Gradient Boosting) de 19 features foi conservada após ablações e
20 trials Optuna/63 fits: nenhum candidato passou os gates de substituição.

**O replay corretivo de 16–30/09/2018 foi executado e verificado pelo autor.**
São 556.824 eventos, 143.945 transações pontuadas e 1.453 alertas. A regra
`exact_binary64_mean_v1` resolveu a divergência SQLite/DuckDB: os 19 atributos
coincidem exatamente e os scores passaram o gate original (`atol=1e-12`, `rtol=0`).
O checkpoint anterior, o modelo e o limiar permaneceram preservados.

| Resultado nos 15 dias | Fila causal | Top-100 retrospectivo |
| --- | ---: | ---: |
| Alertas de cliente-dia | 1.453 | 1.500 |
| Clientes-dia fraudulentos capturados | 768 | 800 |
| Clientes-dia fraudulentos fora dos alertas | 358 | 326 |
| Precisão agregada | 52,86% | 53,33% |
| Recall agregado | 68,21% | 71,05% |

A fila usa o primeiro cruzamento do limiar e até 100 clientes por dia. O
retrospectivo ordena pelo máximo score após observar o dia completo. A comparação
é descritiva; as 32 capturas adicionais não provam superioridade operacional.
Cliente-dia não é pessoa única, e captura não equivale a perda financeira evitada.
AP transacional no replay: **0,578783**; ROC AUC: **0,876322**. Os resultados de
maio (AP 0,640703) e de 02–15/09 (AP 0,621312) pertencem a outras janelas e não
constituem, isoladamente, evidência de degradação. Essas janelas foram consumidas.

A [evidência reconciliada](references/evidence/native_numerical_correction_author_validation_2026-10-13.json)
transcreve o `run` e o `verify` fornecidos pelo autor. Os bytes dos checkpoints,
scores e arquivos de medição continuam locais e não foram inspecionados pelo
assistente. [Resultado, procedimento e limites](docs/operations/NUMERICAL_CORRECTIVE_REPLAY.md#resultado-nativo-informado-pelo-autor--2026-10-13).

O ensaio blue-green também foi aprovado: troca de release pelo NGINX e retorno
verificado após falha injetada, com os mesmos pesos.
[Resultado e limites](docs/operations/SERVING_ROLLOUT.md#aceite-nativo--2026-10-19).

HTTP/wheel e Docker também foram validados pelo autor. A imagem reportada tem
166,8 MiB; o snapshot de memória foi 178,5 MiB e o p95 HTTP foi 4,74 ms em 100
requisições sequenciais ao caso de controle. Essa medição é de serving local e
não mede a latência do fluxo de eventos. [Serving](docs/operations/SERVING_CONTRACT.md).
A preparação da correção aprovou **595 testes/93 avisos**, com tox `py314`, Ruff,
formatação e lockfile; isso é evidência de fixtures, separada do replay nativo.

**Replay programado validado pelo autor:** 556.824 eventos em 14 sessões a
8.640×, com 143.945 decisões, 1.453 alertas e zero commits sem medição.
A avaliação independente aprovou as 19 features exatas, scores e decisões;
reproduziu a qualidade já conhecida da janela, sem nova confirmação estatística.
O p95 de processamento foi 23,71 ms; agendado→commit, 379,57 s sob aceleração.
Ritmo 1x contínuo e SLA permanecem sem avaliação.
Prometheus e o coletor foram conferidos no volume nativo final. O teste de falha
do exporter observou alerta em 56,36 s e recuperação em 12,29 s, sem alterar os
arquivos do ensaio. A rotina tem pytest no tox padrão e um ambiente explícito
para o teste real. Os dashboards foram confirmados no smoke; falta a conferência
visual dos valores nativos finais.
[Resultado, origem e limites](docs/operations/OBSERVABILITY.md#resultado-nativo-informado-pelo-autor--2026-10-15).
**Armazenamento validado pelo autor:** inventário de `data/` e piloto S3 de 64 arquivos/64 MiB,
com manifesto, idempotência e restauração isolada. O autor validou inventário
de 1.643 arquivos (2,92 GiB) e recuperação de documentos e da release completa
no RustFS, com reenvio sem novos uploads. O modelo restaurado passou o gate
pytest com identidade histórica preservada e erro zero no score de controle.
O autor também aprovou o preparo completo da Bronze: **183 partições**, 188 arquivos
no payload e 107.309.243 bytes, com inventário de origem incluído. O novo cliente
S3 reutiliza uma sessão em um job Compose por comando, fora do ambiente científico.
O autor validou publicação e recuperação completas no RustFS: **189 objetos**,
reenvio com **zero uploads/189 reutilizados** e restore com **183 partições
verificadas**, usando o inventário baixado. O arquivamento/recuperação da Bronze
está fechado; adoção pelos consumidores, cobertura dos demais artefatos e
tracking seguem abertos. [Evidência e escopo](references/evidence/bronze_transfer_author_validation_2026-10-15.json).
O autor também validou preparo e recuperação completos da Silver no RustFS:
**187 arquivos/40.288.931 bytes**, contrato, inventário e auditoria incluídos;
**188 objetos**, reenvio sem uploads e restore aprovado em schema/contagens SQL:
**183 partições e 1.754.155 linhas/IDs distintos**. Os gates de arquivamento e
recuperação Bronze/Silver estão fechados; consumidores ainda usam paths locais.
[Evidência Silver](references/evidence/silver_archive_author_validation_2026-10-15.json).
O inventário Gold mapeou 506 arquivos/326.821.198 bytes entre datasets, estudos e
resultados. O primeiro perfil de arquivamento cobre `gold_v1` completo, auditorias,
inventário, contratos e protocolo temporal; usa o mesmo transporte S3 e
`verify_gold` com schema/features/datas/IDs e contagens por split. O autor aprovou
preparo, publicação e recuperação: **48 arquivos/25.196.448 bytes**, **49 objetos**,
reenvio com zero uploads e restore com **42 partições, 19 features e 402.877 linhas**.
Inventário, contratos e protocolo vieram da cópia baixada, sem ler os originais ou
Silver/Bronze. Gate de arquivamento/recuperação de `gold_v1` fechado; variantes,
estudos e adoção pelos leitores permanecem em etapas próprias.
[Evidência Gold](references/evidence/gold_archive_author_validation_2026-10-15.json).
[Procedimento Gold](docs/operations/STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo).
O inventário de desenvolvimento distinguiu **64 arquivos/38.159.334 bytes de dataset**
e **319 arquivos/232.943.522 bytes de estudo**, incluindo SQLite. O autor aprovou
arquivamento e recuperação do dataset com inventário e protocolo HGB:
**66 arquivos/38.202.741 bytes**, **67 objetos**, reenvio sem uploads e restore
com **63 partições/19 features/603.669 linhas**, preservando a identidade do
manifesto. Gate do dataset fechado. O preparo offline do backup do estudo está
implementado/testado: lock do escritor, API de backup SQLite, checagem lógica e
verificador existente sobre cópia temporária, ligado ao bundle do dataset.
O autor aprovou o preparo nativo: **322 arquivos/233.048.483 bytes**, 20 trials
concluídos/63 tentativas, integridade/digest lógico conferidos e decisão histórica
`retain_reference` preservada. Em 2026-10-16, o autor aprovou o ciclo S3 do estudo:
**323 objetos enviados/verificados**, reenvio com **zero uploads/323 reutilizados**
e restore de **322 arquivos**, mantendo trials, tentativas, hash lógico SQLite e
vínculo com o dataset local verificado. Gate de recuperação do estudo concluído.
[Evidência nativa do ciclo S3](references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
[Evidência do preparo](references/evidence/hgb_study_backup_author_validation_2026-10-15.json).
[Backup do estudo](docs/operations/STORAGE.md#backup-consistente-do-estudo-hgb-preparo-offline).
[Evidência de desenvolvimento](references/evidence/development_gold_archive_author_validation_2026-10-15.json).
[Desenvolvimento e estudo](docs/operations/STORAGE.md#gold-de-desenvolvimento-dataset-e-estudo).
[Procedimento](docs/operations/STORAGE.md#silver-completa-preparo-e-recuperação-com-contrato). O
[plano de persistência](docs/operations/STORAGE.md) define o papel de S3, DuckDB,
SQLite/PostgreSQL, Prometheus e eventual feature store. PostgreSQL é o destino
escolhido do servidor MLflow; a troca preservará runs e referências em etapa
própria. Depois vêm CD/rollback, dados/features e atualização controlada (CT).
**Destino final escolhido: Kubernetes local com KinD + Helm**, após recuperação e
rollback demonstrados; Compose é a bancada atual. Feature store e orquestração
dependem de necessidade demonstrada. Não há serviço cloud pago obrigatório.
O [mural](docs/project/ROADMAP.md) mantém critérios e prioridades; a
[síntese para stakeholders](docs/project/STAKEHOLDERS.md) explica a utilidade.
A fonte é simulada; não há operação bancária real, publicação externa de alertas
ou promoção em produção. A organização inicial usou
[Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/).

## Começar

Python 3.14.4 e Poetry 2.4.3, na raiz do checkout:

```bash
poetry install
make validate
```

`make validate` executa tox, Ruff e pytest com as versões de `poetry.lock`.
Os testes usam dados sintéticos e rede simulada; não baixam o dataset real.
Comandos de aquisição, preparação e experimentação ficam no
[runbook](docs/operations/OPERATIONS.md). Preserve dados, manifestos, estudos e
tracking juntos; `data/` é ignorado pelo Git.

## Organização

| Área | Responsabilidade |
| --- | --- |
| `data/contracts/` | Schemas, aceitação dos datasets e protocolo temporal |
| `data/ingestion/` | Inventário fixado, aquisição Bronze e CLI de download |
| `data/datasets/` | Construção, verificação e consulta de Silver e Gold |
| `data/quality/` | Perfil diagnóstico da Bronze e EDA de treino |
| `features/` | Schema, cálculo causal e validação de valores |
| `modeling/contracts/` | Interface executável dos modelos e schema das predições |
| `modeling/algorithms/` | Uma factory por algoritmo e catálogo explícito |
| `modeling/experiments/` | Políticas, treino compartilhado e executores específicos |
| `evaluation/` | Métricas de ranking, comparações pareadas e diagnósticos |
| `integrations/` | MLflow, persistência skops e exportação da referência |
| `serving/` | Contrato HTTP, carga da release e pontuação sem estado |
| `storage/` | Validação de destinos em pontos de entrada operacionais explicitamente cobertos |
| `streaming/contracts/`, `features/`, `replay/`, `decisions/`, `evaluation/` | Eventos, estado causal, replay durável, triagem e avaliação da fila emitida |

Os caminhos são relativos a `fraud_detection_mlops/`. A
[arquitetura](docs/ARCHITECTURE.md) explica dependências e migração. Os adaptadores
antigos foram retirados; use os módulos explícitos dos runbooks.

## Navegar e contribuir

| Objetivo | Referência |
| --- | --- |
| Entender o problema e os limites | [Contexto](docs/project/PROBLEM_CONTEXT.md) e [Model Card](docs/modeling/MODEL_CARD.md) |
| Acompanhar decisões e resultados | [Notebook principal](notebooks/fraud_detection_mlops.ipynb) e [mural](docs/project/ROADMAP.md) |
| Conferir dados e causalidade | [Pipeline](docs/data/DATA_PIPELINE.md), [dicionário](docs/data/DATA_DICTIONARY.md) e [Gold](docs/data/GOLD_CONTRACT.md) |
| Adicionar um modelo compatível | [Guia de contribuição](docs/modeling/CONTRIBUTING_MODELS.md) e [exemplo executável](examples/model_contribution.py) |
| Executar e recuperar uma etapa | [Operações](docs/operations/OPERATIONS.md), [MLflow](docs/operations/MLFLOW.md) e [testes](docs/operations/TESTING.md) |
| Exportar e pontuar o modelo existente | [Serving de laboratório](docs/operations/SERVING_CONTRACT.md) |
| Encontrar todos os documentos | [Índice](docs/README.md) |

Protocolos e recibos versionados ficam em `references/`; dados e modelos reais
ficam no armazenamento operacional. Compatibilidade com a interface não autoriza
abrir um holdout, alterar um gate ou promover um modelo.


### Tracking: inventário revisado e snapshot recuperado no RustFS

O inventário somente leitura levantou 70 runs finalizadas e 66 logged models no
host do autor. A nova coleta confirmou **zero referências não resolvidas** e
**zero localizações gerenciadas ausentes**. O Default não tem runs/logged models
e seu diretório declarado não existe: revisão de cobertura concluída, sem
reescrever URIs ou o status conservador do inventário.
[Evidência nativa](references/evidence/mlflow_inventory_author_validation_2026-10-16.json).
O inventário levanta experimentos/runs/logged models, schema/histórico,
artefatos/checksums e referências dos JSONs, sem inicializar o SQLite original
ou carregar modelos. [Comando, cobertura e critérios PostgreSQL + S3](docs/operations/MLFLOW.md#inventário-antes-da-migração-postgresql--s3).
O relatório orientará backup restaurável, preservação de IDs/URIs, recarga e
rollback antes da troca. O serviço isolado PostgreSQL/S3 foi provisionado e validado; o histórico local não foi migrado.


O preparo/verificação offline do tracking está implementado/testado; execução
nativa aprovada para os dois stores. Captura banco normalizado via SQLite Online Backup API sobre
cópia temporária, artefatos completos, arquivos auxiliares regulares e diretórios,
com inventário/proveniência e verificação antes da publicação local. Destinos
fixos sob data/storage; não há saída arbitrária na raiz ou home.
[Comando e limites](docs/operations/MLFLOW.md#backup-offline-do-tracking-preparo-e-verificação).
O gate global de persistência continua aberto: inventariar todos os produtores
e volumes, corrigir destinos, validar recuperação e só então retirar caminhos
antigos. [Contrato de persistência](docs/operations/STORAGE.md#critério-de-fechamento-da-persistência-global).


O preparo nativo detectou o segundo store documentado em
`data/tracking/interface-smoke`: 1 run concluída, 1 logged model, 6 arquivos/32.088
bytes, sem localizações gerenciadas ausentes. A correção inclui os dois stores
com bancos/WAL e artefatos conferidos independentemente e limites agregados,
preservando a separação do smoke sintético. Bancos desconhecidos continuam
bloqueados. Retentativa de preparo e verificação nativos aprovadas para os dois stores;
publicação, reenvio idempotente e recuperação S3 aprovados pelo autor. Migração,
recarga operacional e limpeza permanecem abertas. [Cobertura e comando](docs/operations/MLFLOW.md#cobertura-dos-dois-stores-mlflow--correção-de-2026-10-16).
[Registro do diagnóstico e preparação](references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).


O backup MLflow teve **preparo e verificação locais aprovados pelo autor**:
dois stores, 71 runs, 67 logged models, 633 artefatos; pacote de 640 arquivos e
256.354.588 bytes. O verificador conferiu os dois bancos e artefatos sem ler
originais. Tox nativo: 70 testes aprovados, um aviso, Ruff/formatação/lockfile
aprovados. [Registro nativo](references/evidence/mlflow_backup_author_validation_2026-10-16.json). A transferência S3 agora está
implementada/testada com o job existente, incluindo restauração em diretório
separado e nova verificação por store; publicação/reenvio/restore nativos aprovados.
A recuperação sobre cópia descartável foi validada pelo autor em 2026-10-16; o resultado local via SDK está no runbook MLflow.
[Procedimento](docs/operations/MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow).
O acesso local ao histórico e à referência pelo SDK foi validado depois desse
preparo. Migração PostgreSQL, adoção pelos consumidores e inventário/limpeza
globais mantêm critérios próprios. Código científico, contratos,
evidências e URIs históricas permanecem preservados.


**Ciclo S3 do snapshot MLflow aprovado pelo autor em 2026-10-16:** publicação com
641 objetos enviados/verificados, reenvio com zero uploads/641 objetos reutilizados
e restore em diretório separado com 640 arquivos/256.354.588 bytes. Os dois stores
mantiveram 71 runs, 67 logged models, 633 artefatos e os hashes SQLite/lógicos;
integridade/FKs/tabelas/URIs conferidas, sem leitura dos originais pelo verificador.
16 testes nativos passaram no tox. [Evidência nativa](references/evidence/mlflow_transfer_author_validation_2026-10-16.json).
Gate de acesso ao histórico e recarga do modelo concluído em cópia descartável,
com paths históricos mapeados somente nessa cópia e sem depender dos originais.
O autor validou esse uso local pelo SDK em 2026-10-16.
O snapshot conferido permanece preservado; PostgreSQL, adoção pelos leitores,
rollback, inventário global e limpeza continuam abertos.
[Resultado e próximo critério](docs/operations/MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16).

## Recuperação do uso do MLflow: validação nativa concluída

O ensaio descartável consulta histórico dos dois stores e recarrega o modelo
fixado pela release, conferindo hash e score. Implementação e testes com fixtures
concluídos; a execução sobre os restores do autor foi aprovada em 2026-10-16.
[Comando e limites](docs/operations/MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).
O [contexto do problema](docs/project/PROBLEM_CONTEXT.md) foi revisto para distinguir
ranking retrospectivo, fila causal, evidência científica e maturidade operacional.

**Recuperação local MLflow aprovada pelo autor em 2026-10-16:** os dois stores
forneceram 71 runs, 67 logged models e 321 registros de histórico. A referência
recarregada reproduziu o score com erro zero; restores e tabelas foram preservados
e a cópia descartável removida. O gate fecha acesso local pelo SDK e um smoke de
inferência; serviço HTTP/PostgreSQL, migração primária e inventário global mantêm
critérios próprios. [Resultado e evidência](docs/operations/MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

## Próximo recorte: inventário dos destinos

O coletor de metadados agora inclui `data/storage/` e arquivos na raiz do checkout,
com catálogo de produtores e fronteiras externas declaradas. Implementação/testes e
coleta nativa aprovados no recorte do checkout. Não migra nem remove arquivos. [Executar e interpretar](docs/operations/STORAGE.md#inventário-do-checkout-e-catálogo-de-produtores).


O inventário do checkout foi executado pelo autor: 4.990 arquivos, sem falhas de
coleta; o marcador vazio de `data/external/` foi revisado e os mounts principais
identificados. A primeira contenção exige áreas de `data/` em quatro CLIs de
exemplos, recibo Docker e inicialização de observabilidade. Não cobre todas as
APIs/CLIs nem migra consumidores. [Destinos, comandos e limites](docs/operations/STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).


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

[Operação e próximos critérios](infra/tracking/README.md).
[Evidência relatada e limites](references/evidence/tracking_lab_author_validation_2026-10-17.json).

### Primeiro produtor HTTP — exemplo de contribuição

`examples.model_contribution` passa a aceitar `--tracking-uri` explicitamente,
como alternativa a `--tracking-root`. O exemplo registra um modelo skops e confere
recarga/paridade pelo proxy no experimento `fraud-model-interface-smoke-http-v1`.
São 80 linhas de treino e 20 de validação geradas em memória, sem acesso ao Handbook.
**Aceite nativo PostgreSQL/RustFS aprovado pelo autor em 2026-10-17:** recarga
independente com erro zero e retorno local explícito; detalhes no fechamento abaixo.

[Contrato, destinos e validação](docs/modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

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

[Diagnóstico, retentativa e limites](infra/tracking/README.md#retentativa-do-produtor-após-falha-de-download-por-chunks).

### Contribuição HTTP aprovada; próximo produtor: HGB — 2026-10-17

O autor aprovou a retentativa: **34 testes de regressão e 1 teste nativo**.
O modelo foi recarregado pelo serviço sem a saída local disponível, com erro
máximo zero. O retorno explícito ao tracking local descartável e a preservação
dos containers foram conferidos. A criação de tabelas no log pertence a esse
retorno local deliberado; não indica fallback silencioso do HTTP.
[Evidência do autor](references/evidence/model_contribution_http_author_validation_2026-10-17.json).

A próxima integração prioriza o **HGB**, começando por um fold sintético com
identidade de destino, recarga independente e reutilização sem fit/publicação.
O estudo de 20 trials/63 tentativas e a decisão `retain_reference` ficam
preservados. O fold HGB foi aprovado pelo autor; o estudo completo não será
reexecutado para testar transporte. [Procedimento e limites](docs/operations/HGB_TRACKING.md).

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
repetir os experimentos Handbook. [Tracking do trio e comandos](docs/modeling/BASELINE.md#tracking-http-do-trio-de-baselines).
[Evidência HGB do autor](references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).

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
[Evidência do autor](references/evidence/baseline_tracking_author_validation_2026-10-17.json).

### Consumidores do tracking: revisão antes da recuperação

O inventário existente ganhou o modo explícito `--tracking-consumers`: confere
fontes/funções do catálogo sem importar módulos, abrir modelos/bancos ou consultar
serviços. A revisão preparada tem **17 registros e 7 fronteiras locais**:
ferramentas de histórico, diagnósticos, ablações, freeze, avaliação final,
avaliação estatística e exportação. A inferência da release permanece independente
do MLflow. Esse resultado não altera os consumidores ou migra o histórico.

A captura coordenada PostgreSQL/S3 e o restore em projeto/volumes/rede próprios
foram implementados e aprovados pelo autor, com conciliação de metadados,
objetos e modelo fixado. O resultado está resumido abaixo.
[Mapa, comando e protocolo](docs/operations/TRACKING_RECOVERY.md).

**Recuperação da bancada aprovada pelo autor (2026-10-19):** PostgreSQL e 38
objetos S3 restaurados em volumes/rede próprios; 8 runs, 6 modelos e 44 pontos
históricos conferidos pela API. O modelo fixado recarregou pelo proxy e reproduziu
20 scores com erro máximo zero. Fonte/captura preservadas, alvo e credenciais
temporários removidos; pytest live passou em 20,89 s. **Recorte de inventário e
recuperação encerrado.** Próxima prioridade: CD/rollback do serving, seguido de
monitoramento de dados/features. Migração dos sete consumidores históricos,
cutover, limpeza e inventário global seguem em backlog próprio.
[Comandos, escopo e limites](docs/operations/TRACKING_RECOVERY.md).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte. O build e a exportação da entrega
versionada também foram aprovados nativamente; o consumo do arquivo em outro
alvo tem aceite nativo informado pelo autor; CI remota aprovada no aceite abaixo. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](docs/operations/SERVING_ROLLOUT.md).

### Primeiro incremento da entrega versionada

A CI ganhou um job após `quality` que constrói a imagem uma vez, qualifica HTTP e
rollback com release **sintética** e exporta essa mesma imagem com hashes e recibos
vinculados ao commit. Sem dados históricos, publicação em registry ou deploy no
laptop. **Build, gates e exportação aprovados nativamente pelo autor em
2026-10-19:** uma imagem do commit `0b95136`, rollback verificado, 11 arquivos e
arquivo de imagem com 175.020.032 bytes. Recursos temporários e tag própria
removidos. A execução no GitHub Actions, a recarga do arquivo em outro alvo e a
promoção de uma release aprovada têm gates próprios. O artifact da CI está
configurado com retenção de três dias; não há aceite de upload remoto ainda. [Fluxo e limites](docs/operations/SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

### Gate de consumo e CI entre jobs — preparação de 2026-10-19

O consumidor verifica o pacote por commit/hash externos, carrega a imagem exata
e testa HTTP em um container novo, sem rebuild ou fit. A entrega original e
as tags alheias são preservadas. A CI encadeia `quality` → `serving-delivery` →
`serving-consumer`; o último job recebe o artifact e o hash do produtor em outro
runner. Implementação, aceite nativo e **GitHub Actions aprovados**; detalhes no aceite remoto abaixo.
O teste local usa o arquivo já aprovado e não exige repetir o build.
[Procedimento, pins e limites](docs/operations/SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Três jobs do GitHub Actions aprovados; próximo: promoção explícita e provisionamento**, sem repetir
os gates locais encerrados. [Resultado e limites](docs/operations/SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).

### CI de entrega e consumo aprovada — 2026-10-19

A [execução 39306262564](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
passou nos três jobs do commit `6a3782b0ccb9d6c0a098251743483aed73e49079`:
qualidade → build/qualificação → consumo do artifact em outro runner. A API
confirma sucesso e runners distintos; o artifact pequeno do consumidor foi
baixado, teve digest conferido e seus recibos de recarga/HTTP/cleanup lidos.
**Gate remoto encerrado.** Próximo: promoção explícita e provisionamento
reproduzível KinD/Helm; registry e deploy permanente ainda têm critérios próprios.
[Resultado e limites](docs/operations/SERVING_ROLLOUT.md#aceite-do-fluxo-no-github-actions--2026-10-19).

### Monitoramento mínimo das features persistidas — preparação de 2026-10-19

O exporter existente ganhou schema/ausência e relógios das 19 features, além de
quatro distribuições com suporte e bins fixos. Compara primeiras e últimas
1.000 decisões do checkpoint, sem sobreposição; baseline operacional, não de
treino. Acrescenta painel Grafana e alertas sem gatilho de CT. Não carrega modelo,
recalcula features ou altera bancos nativos. Testes controlados concluídos;
**snapshot nativo e stack/painel pendentes**. Relatórios em
`data/reports/feature-monitoring/`, sem sobrescrita.
[Comandos, interpretação e limites](docs/operations/FEATURE_MONITORING.md).

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
[Procedimento, explicação conceitual e evidência](docs/operations/FEATURE_MONITORING.md#aceite-nativo-mínimo--2026-10-19).

### Fundação KinD/Helm — preparação de 2026-10-20

Primeiro incremento do fluxo completo: cluster dedicado de um node Kubernetes
1.35.8 fixado por digest, chart com RustFS/PostgreSQL e dois PVCs novos. Imagens
dos serviços preservadas dos pins já utilizados. Kubeconfig, caches, credenciais
privadas e recibos sob `data/storage/kind-platform/`; contexto explícito.
O ensaio grava marcadores próprios SQL/S3 e exige releitura após recriar os pods
com os mesmos PVCs. A execução nativa ainda está pendente. Não migra os dados
históricos, não troca o tracking e não comprova backup ou perda do cluster.
[Runbook, limites e comandos](docs/operations/KIND_FOUNDATION.md).

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](docs/operations/KIND_TRACKING.md#aceite-nativo--2026-10-20).

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.

## Datasets no RustFS do KinD — aceite nativo

Bronze, Silver, Gold e development Gold foram publicados, reenviados sem novos
uploads e restaurados com seus verificadores no RustFS do KinD: 489 arquivos,
493 objetos verificados e 210.997.363 bytes de payload. Fundação preservada;
os caminhos primários dos leitores permanecem os atuais.
[Procedimento e limites](docs/operations/KIND_DATASETS.md).

## Jobs da cadeia de dados no KinD — aceite nativo

Os dois Jobs Helm executaram Bronze → Silver → Gold com entrada S3 identificada,
novas saídas publicadas e restauração independente no host. Silver: 1.754.155
transações; Gold: 402.877 linhas e 19 features. Fundação preservada e Jobs
removidos após sucesso. Gold recalculou features, sem treino ou nova avaliação
de modelos; os caminhos primários dos leitores permanecem os atuais. [Procedimento](docs/operations/KIND_PIPELINE.md).

### Retomada do replay no KinD — aceite nativo

Pod recriado durante a transação de feedback aberta, mesmo PVC e rollback
aprovados. Ledger sintético final igual à execução sem interrupção: 20 eventos,
10 scores e 6 alertas; reentrega sem rescoring. Fundação preservada, processamento
temporário removido e PVC retido. Aceite do autor em 2026-10-20.
[Operação e limites](docs/operations/KIND_REPLAY.md).

### Serving no KinD — aceite nativo

O autor aprovou os seis eventos do gate em 2026-10-21: baseline, bloqueio de
pacote inválido, candidata direta, troca na mesma URL, falha HTTP 502 e retorno
por `helm rollback`. A imagem qualificada da API foi reutilizada, sem rebuild
do runtime ou fit; somente o carrier foi construído. Os mesmos pesos sintéticos
produziram score `0.03463335444035967` antes/depois da troca e do rollback.
Baseline/gateway mantiveram seus pods e a fundação permaneceu igual; candidata
removida, baseline instalada no laboratório e port-forward temporário encerrado.
Rollback observado em 1,99 s neste ensaio, sem garantia de SLA ou zero downtime.
[Operação e evidência](docs/operations/KIND_SERVING.md#aceite-nativo--2026-10-21).
Próximo recorte: observabilidade no KinD.

### Observabilidade no KinD — aceite nativo e visual

O autor aprovou o gate nativo e confirmou a conferência visual em 2026-10-21.
A cópia somente de leitura foi conciliada com a origem: **556.824 eventos,
143.945 decisões e 1.453 alertas**. Scrape Prometheus, oito regras saudáveis,
proxy autenticado do Grafana e quatro dashboards provisionados foram conferidos.
O exporter ganhou novo pod, manteve Deployment/PVC e repetiu as verificações.
Prometheus, Grafana, fundação, tracking, serving e PVC retido do replay
preservaram suas identidades; arquivos da origem permaneceram iguais.
A coleta atual apresenta um replay concluído, sem nova ingestão ou resultado
de qualidade do modelo. Próximo: operação de parada/subida, reboot e fechamento
integrado. [Evidência e limites](docs/operations/KIND_OBSERVABILITY.md#aceite-nativo-e-visual--2026-10-21).

Refinamento visual aprovado em 2026-10-21: [política semântica de cores](docs/operations/DASHBOARD_COLORS.md#aceite-da-política--2026-10-21),
com informações azuis e estados explícitos em texto/cor. Configuração verificada
pela API antes/depois de recriar o exporter e conferência visual confirmada pelo
autor. Métricas, arquivos de origem/cópia e três PVCs iguais ao aceite anterior.
Próximo: operação de parada/subida, retomada após reboot e fechamento integrado.


## Operação do KinD — aceite nativo

Parada/subida e reboot planejado aprovados em 2026-10-21: mesmo node, volumes e plano de réplicas; tracking/S3, identidade/score da API e monitoramento conferidos. O inotify persistiu em 512/524288. O marco KinD/Helm passa para os concluídos no escopo de laboratório. Próximo: simplificação e fechamento funcional; Terraform depois. [Operação](docs/operations/KIND_OPERATIONS.md) e [guia integrado](docs/operations/KIND_PLATFORM.md).


## Executar e apresentar o laboratório

Use `make help`, `make platform-status` e os acessos `platform-api`, `platform-mlflow` e `platform-grafana`. O [guia de execução e demonstração](docs/operations/PORTFOLIO_WALKTHROUGH.md) separa ambiente pronto, reprodução Bronze–Silver–Gold e ensaios blue/green/recuperação. O Make delega aos controladores existentes. Replay, API sintética e monitoramento da cópia concluída não passam a formar uma única nova execução de transações HTTP por esses atalhos.


### Portal de investigação

A jornada do analista reúne visão da janela, fila com filtros e caso detalhado
com contexto, feedback e waterfall SHAP. A API pontua; Grafana opera; MLflow
mantém linhagem e governança. O portal reutiliza as decisões aceitas e distingue
os cinco casos explicados dos demais. [Guia e comandos](docs/operations/FRAUD_PORTAL.md).
Implementação e testes locais preparados; aceite nativo da interface pendente.


O portal ganhou uma área de [desempenho e transparência](docs/operations/PORTAL_PERFORMANCE.md),
com avaliação temporal, comparação de candidatos e carga da política de alertas,
sempre vinculadas ao modelo/release do snapshot. A interface é independente do
algoritmo; explicações são capacidades opcionais. O produto apoia priorização e
investigação, com feedback posterior e decisões governadas. Aceite nativo funcional e visual concluído em 2026-10-22, com 67.255 transações
de validação e o mesmo modelo dos casos.


[Operação do produto e próximo recorte](docs/operations/PRODUCT_OPERATIONS.md):
responsabilidades, dados históricos como fluxo reproduzível e consolidação da
sessão operacional. O portal atual consulta snapshots; atualização contínua da
fila e das métricas da mesma sessão é o próximo aceite, sem novo treinamento.

### Serving operacional após a simplificação

A instalação sintética `fraud-serving` foi aposentada em 2026-10-23. O destino
ativo é `fraud-detector`; os gates HTTP compartilhados e a qualificação/consumo
sintéticos da CI foram preservados. A checagem cotidiana é `platform-check`, com
o baseline reduzido identificado no guia operacional. Não é um novo ensaio de
parada/subida nem promoção de sucessor. [Evidência](references/evidence/synthetic_serving_retirement_author_validation_2026-10-23.json).

A [sessão operacional](docs/operations/PRODUCT_SESSION.md) conecta chegada gradual à API e à fila de investigação atualizada automaticamente; aceite nativo concluído para a sessão de 200 decisões.

Sessões com [janela e volume configuráveis](docs/operations/PRODUCT_SESSION_SCALE.md) reutilizam a API e o ledger causal, com fila paginada e medição separada de contexto, chegada e verificação. O ensaio de 200 decisões foi aceito; a ampliação aguarda aceite nativo.
