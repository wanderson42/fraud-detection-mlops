# Stack local de observabilidade

Prometheus + Grafana OSS + exporter stdlib. RustFS é um perfil `storage` opcional,
selecionado para o próximo marco de persistência S3 local. Não há serviço cloud
ou conta paga obrigatória. CPU, RAM, disco e energia locais continuam consumidos.

O [runbook](../../docs/operations/OBSERVABILITY.md) concentra comandos, evidência,
segurança local e recuperação. [Roadmap](../../docs/project/ROADMAP.md).

| Serviço | Porta no host, somente 127.0.0.1 | Persistência |
| --- | --- | --- |
| Exporter | 19100 | Somente leitura do novo ensaio; UID/GID local |
| Prometheus | 19090 | Volume nomeado; retenção 48 h/512 MB |
| Grafana OSS | 13000 | Volume nomeado; datasource/painéis por arquivos |
| RustFS S3, perfil opcional | 19000 | Volume nomeado `/data` |
| RustFS console, perfil opcional | 19001 | Mesmo serviço; credenciais geradas no `.env` |

As portas 9000/9001 ficam internas aos containers. As publicadas evitam conflito
com outros projetos locais. `images.lock.json` registra tags/digests conferidos;
Compose e Dockerfile usam os digests. Atualizações exigem novo diff e validação.
O exporter não instala dependências ML nem monta Silver, modelos ou oráculo.
`Dockerfile.dockerignore` limita seu contexto aos scripts do monitor e substitui,
apenas nesse build, a allowlist do serving na raiz. Docker/BuildKit moderno é
necessário; o contexto do serving original permanece intacto.

Inicialize uma vez com `python -m scripts.monitoring.local_stack init`; `.env`
ignorado recebe segredos aleatórios e modo 0600. Não publique `docker compose
config` expandido: ele contém os segredos; use `check` (`config --quiet`).
`up`, `status` e `down` delegam ao Docker Compose. `down` preserva volumes.
Para incluir RustFS use `up --storage` e, ao parar, `down --storage`.
O healthcheck RustFS usa `/health/ready`; serviço pronto não comprova backup.

O perfil RustFS tem um **piloto limitado de upload/restore implementado e
validado pelo autor para documentos e uma release completa**, com reenvio
idempotente e restauração em outro diretório. A recarga do modelo restaurado
passou em pytest/TestClient com erro zero no score histórico. Isso não conclui migração de datasets/bancos. O cliente `storage-client` fica no
perfil `storage-tools` e só executa via `run --rm --no-deps`; não participa de
`up --storage`. Para o piloto, iniciar apenas `rustfs`, sem reconstruir/reiniciar
monitoramento. [Procedimento e limites](../../docs/operations/STORAGE.md#piloto-implementado--execução-gradual). Um nó/disco local não tem redundância. SQLite ativo permanece em disco
local; futuramente arquive um backup consistente, com manifesto e hashes, e
prove restauração em outro diretório. Não copie um banco ativo como se fosse
backup consistente nem trate S3 como seu filesystem transacional.

Sem Docker, o exporter pode rodar no host com `--state ...`; os testes de
aplicação continuam executáveis. Os painéis provisionados têm UIDs estáveis:
`fraud-replay-ops` e `fraud-triage-quality`. Ausência de suporte aparece como
“Sem suporte”, sem preencher precisão/recall/AP com zero.

Validação opcional com ferramentas locais:

```bash
promtool check rules infra/observability/alerts.yml
promtool test rules infra/observability/alerts.test.yml
```

O teste de regras resolve caminhos relativos ao arquivo. Para `promtool check
config`, use o arquivo dentro do container (paths `/etc/prometheus/...`) ou uma
cópia temporária com caminhos locais. Não edite a fonte para executar um check.


Para a Bronze completa, `storage-transfer` usa o mesmo perfil `storage-tools`,
com build `Dockerfile.storage`/allowlist específica e lock operacional do SDK.
Executa uma vez por publicação ou download, mantendo um único cliente S3; não
participa de `up --storage`. Monta somente `data/storage/`, sem fonte original ou
pacote científico. O CLI no host confere a restauração com o verificador Bronze
antes de publicar o destino. O autor aprovou o build e o round-trip no RustFS:
189 objetos, reenvio sem uploads e 183 partições recuperadas/verificadas;
[evidência](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json) e [comandos e critérios](../../docs/operations/STORAGE.md#transferência-bronze-um-job-cliente-reutilizado).


A Silver reutiliza `storage-transfer`, com perfil de bundle/prefixo próprios e
verificação de contrato/schema/contagens no host. O autor reconstruiu apenas o
job e aprovou o round-trip: 188 objetos, reenvio sem uploads e restore com
183 partições/1.754.155 linhas verificadas, contrato/inventário e auditoria.
O hash do worker recusa imagem desatualizada; este fechamento documental não
muda a imagem. [Evidência Silver](../../references/evidence/silver_archive_author_validation_2026-10-15.json) e
[procedimento](../../docs/operations/STORAGE.md#silver-completa-preparo-e-recuperação-com-contrato).


O perfil Gold também reutiliza esse job, em `gold/v1/<bundle_id>/`. O CLI Gold
no host aplica o verificador existente sobre a cópia com inventário, contratos
Silver/Gold e protocolo temporal arquivados. O autor reconstruiu `storage-transfer`
e aprovou publicação/reenvio/restore Gold no RustFS: 49 objetos, zero uploads no
reenvio e 48 arquivos/42 partições/19 features/402.877 linhas recuperadas.
SDK/lock/imagens base não mudaram. O fechamento documental não exige rebuild;
futuras alterações do worker exigem imagem atual para o handshake.
[Evidência Gold](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
[Runbook](../../docs/operations/STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo).


O dataset de desenvolvimento reutiliza `storage-transfer` com perfil explícito
`development-gold/v1/<bundle_id>/`. O CLI do host verifica a cópia com inventário
e protocolo HGB arquivados. O estudo SQLite/modelos não participa desse perfil.
O autor reconstruiu o job e aprovou o ciclo nativo: 67 objetos, reenvio sem
uploads e restore de 66 arquivos/38.202.741 bytes, com 63 partições/19 features/
603.669 linhas e identidade do manifesto preservada. Estudo não foi lido/incluído.
Este fechamento não exige rebuild; futuras alterações do worker exigem imagem
atual para o handshake. SDK/lock/imagens base permanecem iguais.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
[Procedimento](../../docs/operations/STORAGE.md#gold-de-desenvolvimento-dataset-e-estudo).


O preparo/verificação do estudo HGB é offline no host e foi aprovado pelo autor
(322 arquivos/233.048.483 bytes, 20 trials/63 tentativas). A transferência usa o
mesmo `storage-transfer`, agora em `fraud-artifacts/hgb-study/v1/<bundle_id>/`.
O autor reconstruiu esse job e aprovou o ciclo S3; este fechamento documental
não exige rebuild. Alterações futuras do worker exigem imagem atual pelo handshake.
SDK/lock/imagens base e serviços permanentes permanecem iguais. O snapshot
existente não precisa ser recriado. Container continua montando somente
`data/storage/`, sem fonte do estudo/credenciais em imagem e sem pacote científico.
O host exige dataset local verificado e reaplica o verificador do estudo no restore.
Ciclo nativo aprovado pelo autor em 2026-10-16: 323 objetos, reenvio zero uploads
e 322 arquivos recuperados/verificados, preservando 20 trials/63 tentativas,
hash lógico e dataset vinculado. O build não inicia RustFS: `--no-deps` exige
serviço disponível; conferir `/health/ready` antes de transferir.
[Evidência nativa](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).
[Procedimento](../../docs/operations/STORAGE.md#transferência-s3-do-estudo-hgb).


## Snapshot MLflow no mesmo job de transferência

O preparo e verify nativos do tracking foram aprovados para os dois stores.
`storage-transfer` agora aceita o perfil MLflow (1.024 arquivos/256 MiB) no bucket
fraud-artifacts, prefixo mlflow-tracking/v1/<id>/. Limites dos perfis anteriores
permanecem iguais. Dockerfile/allowlist incluem quatro módulos stdlib do storage;
SDK, imagem-base, Compose e credenciais permanecem com os mesmos contratos.
Reconstruir somente esse job para atualizar seu handshake. O job monta apenas
data/storage, não o tracking ativo. Publicação/reenvio/restore nativos aprovados para o snapshot;
recarga MLflow e migração PostgreSQL continuam separadas.
[Comandos e recuperação isolada](../../docs/operations/MLFLOW.md#transferência-e-recuperação-s3-do-snapshot-mlflow).


O autor concluiu o ciclo MLflow: 641 uploads/readbacks, reenvio sem uploads e
640 arquivos restaurados/verificados, preservando os dois stores. Gate de
arquivamento S3 desse snapshot fechado. Não houve novo provisionamento, recarga
operacional MLflow ou migração PostgreSQL. [Resultado nativo](../../docs/operations/MLFLOW.md#ciclo-s3-nativo-do-snapshot-mlflow--2026-10-16).

### Monitoramento mínimo das features persistidas — preparação de 2026-10-19

O exporter existente ganhou schema/ausência e relógios das 19 features, além de
quatro distribuições com suporte e bins fixos. Compara primeiras e últimas
1.000 decisões do checkpoint, sem sobreposição; baseline operacional, não de
treino. Acrescenta painel Grafana e alertas sem gatilho de CT. Não carrega modelo,
recalcula features ou altera bancos nativos. Testes controlados concluídos;
**snapshot nativo e stack/painel pendentes**. Relatórios em
`data/reports/feature-monitoring/`, sem sobrescrita.
[Comandos, interpretação e limites](../../docs/operations/FEATURE_MONITORING.md).

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
[Procedimento, explicação conceitual e evidência](../../docs/operations/FEATURE_MONITORING.md#aceite-nativo-mínimo--2026-10-19).
