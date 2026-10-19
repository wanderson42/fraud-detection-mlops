# Consumidores do tracking e recuperação isolada

Status em 2026-10-17: contribuição, fold do runner Optuna HGB e trio de baselines
tiveram seus ensaios HTTP aprovados pelo autor. Esses aceites não migraram o
histórico nem demonstraram restauração PostgreSQL. O mapa foi conferido no checkout do autor: 48 testes aprovados, 17 registros,
sete bloqueios e nenhuma divergência. A captura coordenada foi aprovada pelo autor
em 2026-10-19: 38 objetos, 8 runs, 6 logged models e 44 linhas de histórico de
métricas; dump de 149.587 bytes, origem saudável e identidades preservadas.
O restore isolado foi aprovado pelo autor em 2026-10-19: histórico API conciliado,
modelo fixado recarregado com erro zero e cleanup concluído. Esse aceite encerra
o recorte de inventário/recuperação; o resultado está registrado ao final.

## Conferir o mapa no checkout

Reutilizamos o coletor e o catálogo de persistência existentes. Não há outro
scanner, cliente MLflow ou serviço para este inventário:

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/storage/test_persistence_inventory.py \
  tests/storage/test_managed_paths.py

poetry run python -m scripts.storage.persistence_inventory --tracking-consumers
```

O modo novo lê o catálogo e fontes Python em `fraud_detection_mlops/`, `scripts/`
e `examples/`, verifica SHA-256 e funções dos registros revisados e procura nomes
de chamadas relacionados a tracking. Não importa essas fontes, abre bancos ou
modelos, lê `.env` ou consulta Docker/MLflow/S3. A única saída é um relatório
privado em `data/storage/inventory/tracking-consumers-*.json`.

No checkout preparado foram revisados **17 registros**, dos quais **7 ainda
bloqueiam uma migração ampla do consumo local**. Foram lidas 124 fontes e
observados 55 pontos sintáticos de chamada, sem divergências. O autor confirmou `tracking_review_valid=true` e `issues=0` nesse recorte.
Os novos módulos de captura aumentam a contagem de fontes; arquivos extras
podem alterar as contagens. O campo `status=review_required` é esperado: a revisão
válida não resolve os bloqueios ou demonstra restauração. Uma revisão divergente
retorna código de saída 1 e preserva o relatório para análise.

Esse é um mapa revisado com detecção sintática de mudanças, não um call graph
completo. Aliases, chamadas dinâmicas, subprocessos e plugins externos podem
escapar dos nomes observados. Consumidores indiretos foram incluídos pela leitura
manual. Não implica inventário global ou permissão para apagar stores locais.
O modo padrão do inventário continua lendo apenas metadados do checkout.

## As sete fronteiras locais

| Fronteira | Vínculo que precisa ser preservado |
| --- | --- |
| `mlflow_tracking.verify_model` e `ui` | Exigem `mlflow.db` local; são ferramentas de histórico, distintas do provider HTTP já validado |
| Diagnósticos da validação | Modelo, owner, tags, baseline/Gold e versão de ambiente precisam reconciliar |
| Ablações de features de terminal | Referência, checkpoints, recuperação de runs e publicação compartilham identidade/destino local |
| Congelamento da referência | `tracking_root`, URI, hashes e arquivos vinculados ficam no freeze; não reescrever o histórico |
| Avaliação final | Lê modelo congelado e publica avaliação no store anterior; não repetir holdout para testar infra |
| Avaliação estatística da referência | Delega ao loader congelado e mantém ledger de acesso à janela já consumida |
| Exportação da release | Lê freeze/store local e confere MLmodel, modelo, ambiente e paridade antes de exportar |

A inferência já distribuída lê release skops/manifest/smoke e **não depende de
MLflow**. Manter essa independência no caminho de serving. Os quatro fluxos de
inventário/backup/recuperação históricos SQLite continuam com sua finalidade:
não devem ser convertidos em um backup PostgreSQL por troca de caminho.

O catálogo detalha funções, hashes, efeitos e critérios por registro. Nenhum
consumidor científico, protocolo, freeze, ledger ou release foi alterado nesta
etapa. Novas execuções HTTP continuam opt-in; o padrão local foi preservado.

## Captura nativa aprovada — não repetir para executar o restore

`scripts/tracking/capture.py` controla a operação; `capture_worker.py` usa as
bibliotecas já fixadas do runtime operacional, sem importar ou inicializar MLflow.
Uma imagem descartável de ferramentas mantém SDK/driver fora do lock científico.
Nenhuma modificação é necessária no controller/probe ou na imagem do servidor.
O teste live abaixo é o procedimento de captura já aprovado. Para continuar,
usar a captura existente e os comandos de restore da seção seguinte.

Primeiro, executar os testes sem serviços:

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/tracking/test_capture.py \
  tests/tracking/test_tracking_lab.py \
  tests/storage/test_persistence_inventory.py \
  tests/storage/test_managed_paths.py &&
.venv/bin/python -m tox -e tracking-capture-sdk
```

Com a bancada saudável e sem outros produtores/clientes PostgreSQL ou escritores
S3, preparar somente a imagem de captura e executar o ensaio explícito:

```bash
poetry run python -m scripts.tracking.capture build &&
.venv/bin/python -m tox -e tracking-capture
```

Invocar `tracking-capture` declara ausência desses outros escritores. Durante o
ensaio, o MLflow fica brevemente indisponível: o controller adquire o mesmo lock
dos ensaios anteriores, confere origem e código/runtime do worker antes da pausa,
para apenas `mlflow`, captura e tenta reiniciar **o mesmo container** em `finally`.
PostgreSQL e RustFS permanecem ativos; não há `up`, recriação ou exclusão de volumes.
Não executar produtores ou o MLflow UI enquanto o ensaio estiver em andamento.

Alternativa ao pytest live, para uma única captura pela CLI:
`poetry run python -m scripts.tracking.capture capture --no-other-writers`.
Não é necessário executar as duas alternativas. Cada invocação cria um ID novo.

Os arquivos ficam exclusivamente em
`data/storage/tracking-lab/captures/<capture_id>/`: dump custom, inventário lógico,
objetos numerados e mapa de chaves S3, além do manifesto fechado com hashes.
Chaves S3 nunca viram caminhos do filesystem. Diretório privado (0700), arquivos
0600; os relatórios ficam em `data/storage/tracking-lab/receipts/capture-*.json`.
Não arquiva `.env`, senhas de papéis PostgreSQL ou volumes brutos.

O worker percorre todas as tabelas públicas, sem filtrar status/lifecycle de runs,
e registra contagens e SHA-256 do multiconjunto de linhas, colunas, ownership,
sequências, revisão Alembic e URIs. Usa transação read-only/repeatable-read, cursor
streaming e limite de bytes. Outro cliente conectado, URI externa, owner ou tipo
de relação não revisado bloqueia a captura. A verificação não cria modelos nem
reavalia conjuntos científicos.

O dump usa o `pg_dump` da imagem PostgreSQL fixada, retorna por stream binário
limitado e passa pelo `pg_restore --list`. Este último confere o índice do dump;
**não equivale a restaurar suas tabelas**. O inventário lógico é comparado antes e
depois do dump/cópia. S3 é listado com paginação, copiado integralmente com SHA-256,
relistado e relido por inteiro; mudanças interrompem a operação. São exigidos os
cinco arquivos do formato de modelo skops usado na bancada e cobertura dos objetos
por uma run ou logged model. Runs sem artefatos são válidas; esse mapa não permite
deduzir um arquivo arbitrário perdido que nunca tenha sido registrado.

Os limites são: dump 64 MiB, inventário lógico lido 64 MiB, até 1.024 objetos e
256 MiB de artefatos. O deadline da captura é 300 s e o retorno à saúde tem etapa
própria de até 90 s, além da latência dos comandos de inspeção Docker. Ultrapassar
limites gera falha, sem truncamento declarado como sucesso. O limite delimita
este ensaio, não a capacidade do laptop. Falhas preservam recibo e arquivos parciais
para inspeção; um conjunto incompleto não recebe manifesto de sucesso.

A saída esperada é `status=captured_verified`, `source_recovered=true`, containers
antes/depois idênticos e `manifest_sha256`. `postgres_backup_restore_validated`
permanece **false**. Guardar ID e hash do manifesto para a restauração posterior.
A conferência offline, sem serviços, pode ser repetida:

```bash
poetry run python -m scripts.tracking.capture verify --capture-id <ID_INFORMADO>
```

O handler de SIGTERM e `finally` cobrem falhas/interrupções normais. SIGKILL,
queda do host ou indisponibilidade do daemon impedem uma garantia de recuperação
automática. Em falha, conferir `poetry run python -m scripts.tracking.local_stack status`;
se o MLflow ficou parado, usar `docker compose --env-file infra/observability/.env
--env-file infra/tracking/.env -f infra/tracking/compose.yaml start mlflow` e conferir
a saúde. Não reconstruir ou recriar containers para apagar a evidência da falha.

Os testes do assistente usam subprocessos reais, o SDK fixado com Stubber e
transporte SQL simulado. Não houve Docker ou PostgreSQL nativo no ambiente do
assistente; o teste live é o gate que confirma os comandos na bancada do autor.

### Captura coordenada da bancada

A fonte é exclusivamente `fraud-tracking-lab`: banco `mlflow_lab`, artefatos em
`fraud-tracking-lab/v1`, versões/imagens já fixadas. Não incluir o histórico
SQLite, outros bancos, buckets, volumes de métricas ou credenciais no conjunto.

O PostgreSQL oferece backup consistente de um banco com `pg_dump`; isso não
coordena, por si, os arquivos de outro serviço. Para o ensaio, declarar ausência
de outros escritores, drenar/parar apenas o MLflow da bancada durante a captura,
verificar que não há escritor da aplicação e manter PostgreSQL/RustFS ativos.
Ao terminar ou falhar, devolver o MLflow ao estado anterior com saúde conferida.
Essas ações pertencem ao novo job explícito; o inventário de consumidores continua sem acessar serviços.

Capturar o banco em formato custom e os objetos da namespace autorizada, com
manifest fechado: chaves, tamanhos e SHA-256 de bytes lidos integralmente. Conferir
referências de experimentos/runs/logged models e bloquear URIs externas, objetos
faltantes ou dependências sem cobertura. Preservar IDs e histórico de métricas,
inclusive estados falhos/excluídos; não limpar dados para facilitar o teste.

Orçamento implementado, a conferir com o volume real da bancada:
dump até 64 MiB, até 1.024 objetos/256 MiB e captura até 5 minutos. Exceder limite
deve interromper com recibo, sem truncar ou declarar sucesso. O limite protege o
escopo do ensaio e não é uma restrição à capacidade do laptop.

Não copiar diretórios de volume PostgreSQL como substituto de dump. Um dump de
banco não inclui todos os objetos globais do cluster; papéis e ownership do alvo
precisam de bootstrap declarado, com credenciais do alvo fora do relatório.

## Restauração independente — gate nativo aprovado em 2026-10-19

A entrada é fixada pelo aceite versionado:
`5a2feb3556834dcfa73d14072bdc5182`, manifesto
`5579aa74e0ebe8de4f51ecd762a43dd0621244a61d2cc95318a6f766c376da8b`.
O controller confere todos os arquivos com esse hash antes de criar o alvo.
Não fazer outra captura ou repetir os produtores para esse ensaio.

Testes de preparação (sem Docker):

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/tracking/test_restore.py \
  tests/tracking/test_capture.py \
  tests/tracking/test_tracking_lab.py \
  tests/storage/test_persistence_inventory.py \
  tests/storage/test_managed_paths.py &&
.venv/bin/python -m tox -e tracking-capture-sdk
```

Com os serviços originais saudáveis e a porta 15002 disponível:

```bash
poetry run python -m scripts.tracking.restore build &&
.venv/bin/python -m tox -e tracking-restore
```

Esse é um único ensaio, não uma nova sequência de lotes/inventários. Usa a imagem
de ferramentas `fraud-tracking-restore:lab-v1`, sem alterar o servidor existente.
Ela reaproveita os requirements com hashes já fixados. O job é opt-in, fora da
suíte padrão, e usa o lock compartilhado da bancada.

Antes do alvo existir, prepara controles de 20 linhas da contribuição sintética
aprovada: fonte do gerador com SHA conferido, receipt anterior, scores salvos e
IDs de transação. Confere o SHA do modelo contra o aceite e o objeto capturado,
e os bytes de metrics.json contra o snapshot. Não ajusta nem carrega o modelo
original. Os scores vêm do arquivo salvo naquela execução, não da predição do
alvo. Esses três arquivos anteriores precisam estar preservados:

- `data/storage/tracking-lab/receipts/contribution-50d616d89641404cbf25eb50e438515b.json`;
- `data/examples/contribution-http-50d616d89641404cbf25eb50e438515b/metrics.json`;
- `data/examples/contribution-http-50d616d89641404cbf25eb50e438515b/validation_predictions.parquet`.

Os controles são congelados em `data/storage/tracking-lab/restores/<id>/proof`.
Depois dessa preparação, o cliente temporário monta somente `proof` read-only;
não monta o snapshot, originais ou credenciais. O modelo é baixado pelo proxy
MLflow do alvo, com multipart delegado desligado. O índice é verificado via API,
inclusive runs falhas/excluídas, params/tags e todos os pontos das métricas.

O alvo usa projeto aleatório `fraud-restore-<id>`, rede interna própria, volumes
novos PostgreSQL/RustFS e senhas temporárias distintas. O bucket mantém o mesmo
nome lógico, mas vive no RustFS novo. Não há rede externa ou compartilhada com a
origem. Antes de pg_restore, o controller confere labels, rede, imagem e mounts
do container alvo; o comando nunca recebe o ID do PostgreSQL original.

O dump é restaurado com `--single-transaction --exit-on-error --clean --if-exists`;
`--clean` está restrito ao alvo novo inspecionado. Bootstrap declara usuário
`mlflow`, propriedade do banco e credenciais próprias; não importa senhas do cluster
original. Faz upload dos objetos arquivados e readback integral. Metadados,
colunas, índices, constraints, ownership, sequências e Alembic precisam coincidir
com o inventário fechado. Após API/modelo, para apenas o MLflow temporário e repete
a comparação de PostgreSQL/S3 para detectar efeitos das leituras.

`status=success` exige paridade do modelo (tolerância 1e-12), história completa,
fonte saudável com IDs preservados, captura inalterada e remoção de containers,
rede e volumes **do projeto temporário**. Somente então o recibo pode registrar
`postgres_backup_restore_validated=true`. Recibos e controles locais ficam
preservados; senhas temporárias são removidas após cleanup comprovado. Se o
cleanup falhar, o .env privado fica na pasta desse ensaio para permitir nova
tentativa restrita ao projeto informado. Falha não vira sucesso.

Em interrupção normal, `finally` tenta limpar o alvo; SIGKILL ou perda do daemon
podem exigir intervenção. O recibo identifica exatamente o projeto temporário;
não executar `down --volumes` contra as stacks originais. O deadline do ensaio é
600 s, com até 120 s adicionais para cleanup. Não é teste de SLA ou de perda
física do laptop. O assistente não executou Docker/PostgreSQL/RustFS nativos;
a prova HTTP usa servidor real em fixture e os transportes de banco/SDK têm
contratos offline, declarados nas evidências.

### Encerramento e pendências que não ampliam este ensaio

Aprovado o gate nativo, este recorte de inventário e recuperação estará encerrado:
destinos conhecidos, snapshots e recuperação exercitados. As sete dependências
históricas locais continuam mapeadas e preservadas. Não exigiremos migrá-las ou
repetir inventários para avançar em CD/rollback e monitoramento de features.
Cutover dos produtores, migração histórica, retenção de legados e Helm/PVCs são
entregas separadas com justificativa própria. O campo de inventário **global**
continua false: este fechamento não afirma inspeção de todo o Linux nem autoriza
exclusão de bases originais.

### Desenho e critérios preservados

O ensaio implementa um projeto Compose temporário distinto, com **novos volumes para
PostgreSQL e RustFS**, rede própria e MLflow na porta de loopback proposta 15002.
Usar as imagens fixadas existentes, sem introduzir uma plataforma de backup.
O objetivo dos três serviços temporários é impedir que a prova dependa dos bancos
ou objetos originais; eles não passam a ser parte permanente da infraestrutura.
A política de retenção/remoção desses recursos próprios deve ser explícita no job.

Carregar apenas o dump e os objetos arquivados. O alvo não monta `data/tracking`,
volumes originais nem participa da rede dos serviços fonte. Restaurar com falha
imediata em erro; não usar `--clean` contra o banco original. Configurar o backend,
o endpoint S3 e o destino de artefatos para o alvo. Manter IDs/URIs proxy quando
compatíveis; qualquer remapeamento necessário exige lista revisada e uma cópia
isolada, sem alterar o dump ou o histórico original.

### Critério de aprovação

O primeiro aceite deve exigir: conteúdo lógico de metadados e revisão Alembic
conciliados, ownership e sequências compatíveis, parâmetros/tags e histórico
completo de métricas, objetos íntegros e modelo fixado recarregado pelo proxy do
alvo. O controle de modelo/score deve vir de um recibo anterior aprovado e de
dados sintéticos arquivados no conjunto, não ser aprendido a partir do restore.
O cliente não recebe credenciais S3/PG e usa explicitamente o endpoint restaurado.

Demonstrar leitura usando só o alvo e os arquivos arquivados, containers/volumes
fonte preservados e retorno da fonte à saúde anterior. Preservar recibo em falha
e impedir que um ensaio incompleto receba `status=success`. A prova continua
laboratorial no mesmo host: não demonstra recuperação após perda física do laptop.

Depois desse gate, consumidores/cutover ficam no backlog próprio; o projeto
pode prosseguir em entrega e monitoramento sem ampliar esta etapa de inventário. Não repetir avaliação de maio/setembro, retreinar modelos ou
reescrever freezes antigos para obter uma prova de infraestrutura.

## Fontes do protocolo

Documentação primária PostgreSQL 17 consultada em 2026-10-17:
[pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html) e
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html).
A coordenação com S3, os limites e o isolamento são decisões propostas para esta
bancada; não são garantias fornecidas pelo dump do banco. As regras reais da
bancada estão em [infra/tracking](../../infra/tracking/README.md).

### Falhas do worker: diagnóstico preservado

Uma falha nativa em 2026-10-19 chegou à etapa seed, mas o wrapper ocultou seu
motivo. Não inferimos uma causa de S3/PostgreSQL a partir desse erro genérico.
O worker agora emite JSON com status failed, fase, tipo/código seguro e, quando
cabível, nomes/hashes de campos ou tabelas divergentes. O controller valida o
hash do worker, imprime o diagnóstico seguro e falha o ensaio, preservando o
objeto worker_failure no recibo. O transporte do JSON não significa sucesso do
job. Erros SDK/driver não expõem mensagens com URLs ou credenciais; diferenças
não incluem valores de tabelas. Falhas de daemon/transporte ainda podem anteceder
esse protocolo. Reconstruir somente a imagem de restore antes de repetir o gate;
não repetir a captura nem produtores.

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
e check foram rejeitadas. O autor então reconstruiu somente a imagem de restore
e aprovou `tox -e tracking-restore`, reutilizando a captura aceita, sem retreinar.
Esta comparação cobre os atributos presentes no inventário; não é uma auditoria
de todos os atributos possíveis do catálogo PostgreSQL.

Referências: [colunas](https://www.postgresql.org/docs/17/infoschema-columns.html),
[pg_attribute](https://www.postgresql.org/docs/17/catalog-pg-attribute.html) e
[reconstrução de definições](https://www.postgresql.org/docs/17/functions-info.html).
A equivalência dos cinco casts foi demonstrada no ensaio; a documentação não
substitui a comparação e os controles negativos.

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
