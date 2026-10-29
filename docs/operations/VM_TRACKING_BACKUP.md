# Backup limitado do tracking da VM

Preparação de 2026-10-29, sobre `2054c4d` (PR #17 integrado). Execução nativa
**pendente**. Os testes offline não demonstram restauração real de PostgreSQL/S3.

## Escopo e limites

Esta etapa captura o banco **`mlflow_kind`**, com a role de mesmo nome, e os
objetos do bucket **`fraud-kind-mlflow`** sob `v1/`, com seu marcador de proprietário.
O inventário informado pelo autor encontrou 65 tabelas públicas e 10.958.515 bytes
no banco. O banco padrão `fraud_platform`, com zero tabelas públicas, é distinto;
a consulta inicial a ele não indicava perda do histórico do MLflow.

Os quatro PVCs do KinD usam `hostPath` e política `Delete`. Parada/subida e reboot
preservando esses volumes não substituem um backup. Os arquivos desta captura
ficam fora dos PVCs, no disco da própria VM, em diretório privado. Isso ainda
**não protege contra perda da VM ou de seu disco**. Cópia externa verificada e
restauração a partir dela serão outro aceite.

Não estão incluídos: demais bancos/roles globais do PostgreSQL, outros buckets,
payloads Bronze/Silver/Gold, SQLite e recibos da sessão, relatórios do portal/SHAP,
credenciais originais, dados internos do Grafana, séries do Prometheus e a VM
inteira. O bundle Bronze listado anteriormente é inventário de manifestos locais,
não inventário integral do RustFS. Os hashes da sessão fornecida são guardas de
preservação; seus arquivos não entram neste backup de tracking.

## Procedimento

Execute do checkout revisado e commitado, sem outros writers ou treinamentos.
A imagem auxiliar usa o lock existente do tracking, sem instalar dependências
no ambiente científico. `build` também baixa as imagens PostgreSQL/RustFS fixadas
por digest para o Docker do host; as imagens dentro do nó KinD não bastam.

```bash
poetry run python -m scripts.platform.tracking_backup.controller build

poetry run python -m scripts.platform.tracking_backup.controller preflight \
  --session-id repro-arrival-200-v1
```

O preflight confirma propriedade da plataforma/tracking, identidade do banco,
versão 17, bucket/marcador, limites de objetos e espaço livre de pelo menos 2 GiB.
Abre encaminhamentos transitórios em loopback, sem publicar portas externas.
Não pausa o MLflow e não executa restauração. Os valores de tamanho e quantidade
retornados devem ser registrados antes da captura.

Quando houver janela sem outros writers:

```bash
poetry run python -m scripts.platform.tracking_backup.controller capture \
  --session-id repro-arrival-200-v1 --no-other-writers
```

A captura salva um plano de recuperação antes de alterar réplicas, confere o
champion contra a release ativa e reutiliza o score do smoke já aceito. Pausa
**somente `deployment/fraud-tracking`**, espera seus pods saírem, exige ausência de
outras conexões ao banco, inventaria esquema/tabelas/sequências, executa `pg_dump`
custom e confere seu índice. Copia os objetos, relê os bytes e exige inventários
iguais antes/depois. O API serving permanece provisionado; ações que dependam do
MLflow ficam indisponíveis durante a captura. Isso não é um ensaio de zero downtime.

O bloco de recuperação tenta devolver o mesmo Deployment a uma réplica, espera
saúde e confere UIDs, especificações, configurações, volumes, modelo/release e
hashes da sessão. Somente então sela `manifest.json`. O resultado contém
`capture_id` e `manifest_sha256`: guarde ambos fora do diretório capturado.
Os recibos privados ficam em `data/storage/kind-platform/tracking-backup/`.

Substitua os dois valores abaixo pelo resultado real; não gere um hash novo para
aceitar um arquivo que falhou na verificação:

```bash
poetry run python -m scripts.platform.tracking_backup.controller verify \
  --capture-id CAPTURE_ID --expected-manifest-sha256 MANIFEST_SHA256

poetry run python -m scripts.platform.tracking_backup.controller restore \
  --capture-id CAPTURE_ID --expected-manifest-sha256 MANIFEST_SHA256 \
  --no-other-writers
```

`verify` demonstra integridade/cobertura do arquivo, **não restaurabilidade**.
O orçamento é 64 MiB de dump, 1.024 objetos e 256 MiB de payloads S3; documentos
de controle são limitados a 2 MiB. Exceder o orçamento interrompe a execução;
não se trunca histórico nem se aumenta o limite silenciosamente.

## Restauração isolada

Docker Compose cria um projeto aleatório novo, em rede interna sem portas do
host e com volumes próprios temporários. Ele serve apenas ao ensaio descartável;
os Helm charts continuam responsáveis pelo ambiente KinD. A ferramenta recusa
recursos preexistentes com o identificador do projeto e confere labels, imagens,
rede e mounts antes de restaurar. Não utiliza `--clean` nem restaura no banco
original. Senhas novas são temporárias, distintas das credenciais de origem.

O dump é restaurado em transação no PostgreSQL 17 fixado. Os objetos são enviados
a um RustFS inicialmente vazio. Depois são comparados esquema, sequências,
hashes de todas as linhas e bytes dos objetos. Reutiliza-se a comparação de
schema já revisada no laboratório; as únicas equivalências aceitas são as formas
específicas dos CHECKs do PostgreSQL 17 e os ordinais físicos removidos pelo dump.

Um MLflow temporário expõe o histórico restaurado. Um cliente separado, sem
credenciais PostgreSQL/S3, verifica experimentos, runs, parâmetros, tags,
históricos de métricas e logged models pela API. Confere a versão/alias champion,
run, URI e hash do modelo, baixa os artefatos pelo proxy, valida skops/signature e
executa o smoke de controle com paridade `rtol=atol=1e-12`. Essa inferência de
controle não gera novas decisões na sessão aceita nem retreina o modelo.

Após parar o MLflow temporário, o banco/S3 são relidos. A origem e o arquivo são
conferidos novamente. O projeto temporário é removido com seus próprios volumes;
falha na limpeza impede aceite. O recibo só declara
`postgres_backup_restore_validated: true` após os gates e limpeza. Mesmo assim,
`platform_backup_restore_validated` permanece **false**, pelo escopo limitado.
Nenhuma credencial temporária deve ser publicada no Git, junto com os dumps ou
nas evidências públicas.

## Interrupção e recuperação

Falhas comuns e interrupções tratáveis tentam retomar o MLflow e gravar recibo,
inclusive quando a retomada falha. SIGKILL, perda de energia e falha do host não
executam `finally`. O plano privado `capture-CAPTURE_ID-plan.json` é persistido
antes da pausa. Se a captura tiver sido interrompida e o MLflow não tiver voltado:

```bash
poetry run python -m scripts.platform.tracking_backup.controller recover \
  --capture-id CAPTURE_ID
```

A recuperação exige código/pins do plano, mesma origem e UID/spec do Deployment,
e permite somente a diferença de réplicas 0 → 1 do tracking. Ela não aceita um
backup parcial. Se a origem tiver mudado, preserve recibos e inspecione antes de
qualquer intervenção manual. Para encontrar o identificador após interrupção,
consulte os nomes dos planos no diretório privado de tracking-backup.

Se houver interrupção abrupta durante a restauração, o projeto aleatório poderá
permanecer. O nome está no diretório `tracking-backup/restores/`; inspecione suas
labels antes de remover somente esse projeto. Não use limpeza global do Docker.
A ferramenta não faz prune nem remove recursos KinD. A execução nativa deverá
registrar também o resultado da limpeza e a preservação da sessão original.

## Validação exigida

Testes offline cobrem integridade, adulteração, nomes de objetos, symlinks,
interrupções/retomada da origem, recuperação por plano, recusa de adoção,
isolamento, cliente sem credenciais e falhas de restauração/limpeza. Não há Docker
no ambiente de preparação desta mudança. Build, preflight, captura e restore
reais permanecem pendentes na VM; nenhum recibo nativo foi fabricado.

O helper compartilhado `scripts/tracking/capture_worker.py` agora aceita banco,
role e bucket explícitos, preservando os defaults do laboratório Compose antigo.
Se uma imagem antiga desse laboratório for usada para novas capturas, reconstrua
sua ferramenta para manter o gate de hash do worker; os servidores ativos do
KinD não são reconstruídos por essa alteração.
