# MLflow na fundação KinD/Helm

Este incremento instala MLflow 3.17.0 no cluster dedicado `fraud-platform`,
reutilizando os serviços PostgreSQL e RustFS cujo ensaio de persistência foi
[aprovado](KIND_FOUNDATION_ACCEPTANCE.md). O autor aprovou o aceite nativo deste alvo
em 2026-10-20, conforme a seção de resultado abaixo. A bancada Compose e o
tracking histórico permanecem preservados.

## Responsabilidade dos componentes

| Componente | Destino e responsabilidade |
| --- | --- |
| Release Helm `fraud-platform` | PostgreSQL/RustFS e seus PVCs; controlador da fundação preservado |
| Release Helm `fraud-tracking` | Um Deployment MLflow e um Service ClusterIP no namespace `fraud-platform` |
| Banco e login `mlflow_kind` | Metadata MLflow; role sem superuser, createdb, createrole, replication ou bypassrls |
| Bucket `fraud-kind-mlflow` | Artefatos em `v1/`; marcador separado de identidade do fluxo |
| Secret `fraud-kind-tracking-credentials` | URI de backend e credenciais RustFS; valores fora de Helm values |
| Cliente científico | HTTP pelo proxy, sem variáveis AWS/PG ou token MLflow herdado; temporários gerenciados |

A role MLflow é proprietária somente de seu banco dedicado. O bootstrap usa o
administrador da fundação para criar role/banco com comentários de ownership;
serviço não recebe a senha desse administrador. Para S3, este laboratório ainda
reutiliza as credenciais RustFS da fundação, não uma política IAM por aplicação.
Esse limite não é apresentado como isolamento multiusuário de produção.

O servidor serve artefatos; os clientes usam URIs `mlflow-artifacts:/` e
`models:/`. A integração existente desativa redirecionamento multipart, para que
o cliente local não precise resolver o hostname interno do RustFS. O serviço é
ClusterIP e o acesso do host é um port-forward em loopback, sem exposição pública
ou camada de autenticação externa neste incremento.

## Reproduzir a preparação e instalação

Pré-requisito: fundação pronta e identidade preservada; limites inotify já
ajustados conforme o [aceite](KIND_FOUNDATION_ACCEPTANCE.md). Não recriar o cluster.

```bash
.venv/bin/python -m tox -e py314 -- tests/infra

poetry run python -m scripts.platform.kind_tracking validate &&
poetry run python -m scripts.platform.kind_tracking init &&
poetry run python -m scripts.platform.kind_tracking build &&
poetry run python -m scripts.platform.kind_tracking up &&
poetry run python -m scripts.platform.kind_tracking status
```

O runtime reutiliza `infra/tracking/requirements.txt` com hashes; não altera o
lock científico. Docker build usa contexto filtrado, excluindo dados/credenciais.
A imagem recebe tag local única, é carregada somente em `fraud-platform` e tem
identidade Docker/CRI registrada. `imagePullPolicy: Never` impede buscar outra
imagem para essa tag. O controlador confere a identidade efetiva do pod com o
registro CRI; não trata o ID local como digest de registry publicado.

O bootstrap recusa Secret, role, banco, bucket ou recursos de tracking que não
combinem com sua identidade. A reaplicação preserva as senhas. Se uma etapa
interrompida deixar um recurso sem o marcador esperado, preservar e revisar;
não fabricar recibos nem remover bancos ou volumes para contornar a conferência.

## Executar o gate nativo novamente

```bash
.venv/bin/python -m tox -e kind-tracking
```

O ensaio faz uma única publicação de fixture sintética pequena usando uma
Pipeline StandardScaler/GaussianNB, duas features Gold canônicas e formato
skops. O próprio produtor verifica recarga; um segundo processo, que não
recebe o modelo ajustado nem credenciais de banco/S3, baixa e recarrega pelo
proxy. O gate verifica hash e scores (`erro absoluto <= 1e-12`), parâmetro,
lineage e os dois pontos do histórico de uma métrica.

Um pod auxiliar com credenciais S3 do laboratório compara os bytes de
`model.skops` no bucket com o hash obtido pelo HTTP. Depois o ensaio recria
somente o pod MLflow identificado, usando precondição de UID, e repete a leitura
em outro processo. PostgreSQL, RustFS e PVCs devem manter suas identidades.
São removidos os port-forwards, temporários de cliente e pods auxiliares. A run,
o modelo e o serviço MLflow permanecem como evidência operacional.

O Deployment usa uma réplica e estratégia Recreate. O teste envolve uma pausa
do MLflow; não declara disponibilidade contínua. Não interromper produtores
externos apontados a esse serviço durante o ensaio.

A regressão `test_kind_tracking_client.py` usa HTTP real com SQLite e artefatos
locais descartáveis. Ela protege transporte/contrato, mas não substitui o gate
nativo PostgreSQL/S3/KinD. O teste live é ignorado por padrão e só é habilitado
pelo ambiente tox específico. O servidor HTTP descartável faz parte dos testes
controlados, não cria um tracking local suportado adicional no projeto.

## Arquivos gerenciados e diagnóstico

Tudo que o controlador persiste fica em
`data/storage/kind-platform/tracking/`, na família específica
`kind_tracking_workspace_and_receipts` declarada no catálogo de persistência:

- `credentials.json`: senha e identidade locais, modo 600; diretório modo 700;
- `image.json`: fontes e identidade da imagem carregada, sem credenciais;
- `values-<id>.json`: somente tag/identidade, sem senhas;
- `rehearsal-<id>.json`: status, fase, hashes e resultado do gate, inclusive falha;
- `client-*`: temporários removidos ao encerrar cada processo.

Kubeconfig e caches continuam na raiz gerenciada da fundação. Estado de banco e
objetos está nos dois PVCs existentes; o pod MLflow só recebe `/tmp` efêmero.
Nenhuma credencial é versionada. Uma mudança futura nas fontes da imagem exige
revisar o registro anterior; o controlador recusa reutilizá-lo silenciosamente.

Se falhar, enviar a mensagem e o recibo indicado, além do status:

```bash
poetry run python -m scripts.platform.kind_tracking status
```

O status não imprime variáveis de ambiente ou Secrets. Preservar recursos até
identificar a etapa; `up` e `build` podem manter recursos parciais para revisão.

## Escopo e próximo incremento

Este gate demonstra integração e recuperação após recriação do pod MLflow com
uma fixture. Não migra as 71 runs históricas, não retreina HGB histórico, não
reavalia qualidade antifraude e não promove a referência. Não valida backup do
novo banco/bucket ou recuperação após exclusão do cluster/host. Os gates de
recuperação anteriores pertencem ao ambiente e recibos anteriores.

O aceite nativo foi concluído e registrado. Seguir aos incrementos de
objetos/dados e execução do fluxo, mantendo cada fonte até aceitar o destino.
Serving, observabilidade, jobs e estado/replay ainda têm critérios próprios.
Terraform permanece posterior à simplificação e ao fechamento funcional.

Referências: [MLflow Tracking Server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/)
e [port-forward Kubernetes](https://kubernetes.io/docs/tasks/access-application-cluster/port-forward-access-application-cluster/).

## Aceite nativo — 2026-10-20

O autor executou `py314`: **135 testes aprovados e 1 live ignorado em 14,25 s**,
Ruff check/format (314 arquivos) e verificação do lock aprovados. Depois,
`kind-tracking`: **1 teste aprovado em 21,56 s**; tox concluído em 35,65 s.
Não é necessário repetir o gate para aplicar este fechamento documental.

| Verificação | Resultado nativo |
| --- | --- |
| Run | `da62ff0d145b4304b6827eb01212ac3c` |
| Modelo | `models:/m-9dc865b0dac6450a93eadad2e17e8e73` |
| Leitura HTTP independente | Aprovada antes e depois da recriação do pod |
| Modelo no S3 | 20.265 bytes, hash igual ao obtido pelo HTTP |
| Paridade | Erro absoluto máximo **0,0** |
| Histórico da métrica do probe | Dois pontos preservados |
| MLflow | Novo UID do pod; mesmos Deployment, tag local e identidade CRI da imagem |
| Fundação | Mesmos pods PostgreSQL/RustFS, PVCs e PVs |
| Limpeza | Temporários dos clientes removidos; variáveis AWS/PG retiradas do ambiente dos clientes |

SHA-256 do modelo:
`11ede55b832c9c808183c4f3dd38e4e4b8d45c11276d3630e1d189b9d42c7481`.

Recibo local: `data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json`.
O [registro versionado](../../references/evidence/kind_tracking_author_validation_2026-10-20.json)
transcreve os campos enviados pelo autor; não alega cópia byte a byte ou hash
do arquivo local. O hash do modelo é o informado e conferido pelo gate.
A identidade `docker.io/library/import-2026-10-20@sha256:...` é a identificação
CRI observada após carregar a imagem local, não evidência de push a um registry.

A aprovação encerra este incremento sintético de integração e recriação do pod.
Não comprova backup/restore do novo destino, perda do cluster/host, migração das
runs históricas, qualidade antifraude, retreino histórico ou promoção de modelo.
O próximo recorte é objetos/dados no RustFS do KinD; a infraestrutura completa
permanece no backlog principal.
