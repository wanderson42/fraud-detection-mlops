# Backup limitado do tracking da VM

Preparado sobre `2054c4d` (PR #17 integrado). **Captura e restauração isolada
aceitas pelo autor em 2026-10-29**, após a correção `93bce79`. O resultado nativo
está registrado abaixo; testes offline são evidência separada.

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

## Correção do caminho montado — 2026-10-29

O autor executou build/preflight e capturou o tracking com sucesso. A captura
`7312832d3d8044999c62d5e556eaf38a`, manifesto
`16aab86ad5fbe0f577641a57b06593f4acbe4e5f2f97709f1ab393f414e1f80e`,
reuniu 26 objetos de payload (987.907 bytes), com marcador tratado separadamente,
e retomou a origem. A verificação de integridade passou; o restore ainda não.

O diagnóstico nativo mostrou falha em `seed`: `Backup identity differs`. O
verificador exige que o nome do diretório seja o UUID da captura; o Compose
montava o conteúdo diretamente em `/capture`, e o worker ignorava a variável
de caminho. Isso era erro de integração do mount, não evidência de dump corrompido.

O mount e `BACKUP_CAPTURE_PATH` agora recebem `/capture/<UUID>` com ID explícito
fornecido pelo controller. O worker exige esse formato em seed/verify e o
verificador continua conferindo o UUID contra o manifesto. Os hashes, orçamento,
cobertura e demais gates permanecem. Os recibos de falha passam a registrar a
etapa e o tipo de erro, sem stderr ou credenciais. Testes offline incluem o
conteúdo montado sob outro pai, troca de UUID e caminhos inválidos.

Reconstruir a imagem auxiliar após commit da correção. Antes de substituir seu
registro, preservar `tracking-backup/image.json` em histórico privado e conferir
seus pins anteriores. Não apagar a imagem antiga nem alterar o manifesto, dump,
objetos ou recibos da captura. O pacote corretivo inclui `rebuild.sh`, que faz
essa preservação com guardas e mantém o lock operacional durante o build.
Depois executar preflight e repetir restore com o ID/hash originais. Nenhum
novo aceite de restauração é declarado até os gates nativos passarem.

## Aceite nativo da restauração — 2026-10-29

Saída de terminal fornecida pelo autor: `status: verified`, `phase: completed` no
projeto `fraud-kind-restore-9d2d3c07ca6f`, reutilizando a captura e o manifesto
originais. O commit `93bce79` corrigiu o mount; o registro da imagem anterior foi
preservado em `image-before-mount-fix-84d12804179caaed.json`. A nova imagem auxiliar
foi construída e o preflight passou antes do restore. O servidor original não foi
reconstruído; o MLflow temporário usou a nova imagem auxiliar.

| Gate | Resultado informado pelo autor |
| --- | --- |
| PostgreSQL/S3 após restore | 65 tabelas e 26 objetos conferidos |
| Cliente MLflow | 4 runs, 4 logged models e 27 linhas de histórico de métricas |
| Registry | Alias champion verificado |
| Modelo | SHA `401d7c36fc17bfd4176e506d45da2a559e0e2d44b7305af2f77c4cd8ac04ca67` |
| Paridade | Uma linha de smoke; erro absoluto máximo `0.0` |
| Credenciais do cliente | Sem acesso direto PostgreSQL/S3 |
| Readback posterior | 65 tabelas e 26 objetos novamente conferidos |
| Origem e captura | `source_unchanged: true`, `capture_unchanged: true` |
| Limpeza | `temporary_resources_removed: true` |
| Treinamento | `fit_executed: false` |

O gate limitado `postgres_backup_restore_validated` passou. O gate global
`platform_backup_restore_validated` permanece false. O reload executou uma
inferência de controle no alvo temporário; não foi novo treinamento nem execução
do fluxo de decisões da sessão. Os testes da correção na VM tiveram 179 aprovados
e 28 pulados em 2,81 s (tox 6,45 s). O SDK havia passado separadamente com 60
aprovados e um pulado; não somar esses totais como uma única execução.

[Registro público do aceite](../../references/evidence/vm_tracking_backup_restore_author_validation_2026-10-29.json).
O recibo privado original fica em
`data/storage/kind-platform/tracking-backup/fraud-kind-restore-9d2d3c07ca6f.json`.
O registro público é síntese do terminal fornecido pelo autor, não uma execução
nativa independente do assistente. Evidências antigas de preparação e falha
permanecem como histórico e não tiveram seus flags reescritos.

Próximos gates: publicar o incremento com CI/PR, definir cópia externa verificada
e restaurar a partir dela, além de cobrir os dados excluídos e qualificar a
recuperação integral. Não há aceite de tolerância à perda do host.

## Revisão dos consumidores do tracking no CI #84

O CI do commit `a1d19216bd12f7e76bfa84bbf795353660f29003` falhou somente em
`test_project_tracking_review_matches_current_sources`: o catálogo revisado não
incluía os novos clientes `tracking_backup/controller.py` e `tracking_backup/worker.py`.
Os outros 1.939 testes passaram. A falha foi reproduzida antes da correção.

Os dois arquivos foram revisados e registrados com hashes exatos, funções e
escopo de recuperação isolada. O teste e o verificador permanecem iguais; não
foi criada exclusão ou aprovação automática de novos consumidores. Os bloqueadores
da migração local e os gates históricos permanecem. Essa revisão de fontes não
executa backup, não abre bancos/modelos e não substitui o aceite nativo específico.
A correção altera somente catálogo/documentação/evidência; não exige rebuild da
imagem auxiliar nem repetição da captura ou do restore aceito. Novo CI pendente.
