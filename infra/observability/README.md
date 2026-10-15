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

O perfil RustFS está **preparado, sem fluxo de upload/restore implementado nesta
entrega**. Um nó/disco local não tem redundância. SQLite ativo permanece em disco
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
