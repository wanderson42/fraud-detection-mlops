# Estado persistente e instalação limpa do produto

## Atualização da implementação — 2026-10-30

A classificação foi integrada no PR #20 (`730a1d2`). A lacuna da fonte padrão
descrita abaixo agora tem um [produtor explícito a partir do Silver](SESSION_EVENT_SOURCE.md),
com formato de eventos sem decisões, plano por metadados, geração/verificação e
compatibilidade dos checkpoints antigos. Implementação/testes preparados;
execução nativa da nova fonte e instalação integral continuam pendentes.
O diagnóstico original e os aceites anteriores permanecem como referência.

Revisão documental de 2026-10-30, sobre `a0a96f6` (PR #19 integrado).
Este contrato classifica dependências do produto demonstrado na VM. Não é um
inventário completo do disco nem comprova um novo backup ou instalação integral.
Os recibos históricos conservam seus escopos e flags originais.

## Dois objetivos diferentes

**Retomar a mesma investigação:** preservar os bytes dos eventos, decisões,
respostas HTTP, checkpoint, modelo e recibos que vinculam essas identidades.
Reprocessar eventos ou treinar de novo não recupera o histórico original.

**Instalar para outro usuário:** obter dados por uma origem declarada, construir
as camadas, executar sua rodada e sessão e gerar credenciais/recibos próprios.
Não precisa importar o laboratório do autor. O código e os contratos são comuns;
UUIDs, PIDs, ownership, kubeconfig e evidências privadas são de cada ambiente.

## O que preservar e o que reconstruir

| Estado e localização | Continuidade da mesma instalação | Caminho numa instalação nova | Cobertura de recuperação atual |
| --- | --- | --- | --- |
| Código, Helm, políticas, referências e `poetry.lock` no Git | Fixar commit e contratos usados pelos recibos | Checkout de revisão identificada e ambientes instalados pelo lock | Versionamento; não contém dados privados |
| MLflow: banco `mlflow_kind` e artefatos em `fraud-kind-mlflow` | Preservar metadados e objetos em conjunto: runs, modelos, histórico e aliases | Criar tracking próprio; treinar/registrar sua rodada | Captura `7312832d3d8044999c62d5e556eaf38a` restaurada isoladamente, inclusive a cópia devolvida do Alienware; 65 tabelas/26 objetos e modelo conferidos |
| Bronze em `data/raw/` e bundles em `data/storage/kind-platform/datasets/` / bucket `fraud-datasets` | Preservar payloads, manifestos e proveniência; disponibilidade futura da origem não é garantia de backup | Adquirir e verificar Bronze; publicar bundle próprio | Não cobertos pela captura do tracking; listar manifestos não verifica todos os payloads S3 |
| Silver/Gold/desenvolvimento em `data/interim/`, `data/processed/` e bundles S3 | Reconstruíveis somente com Bronze exato, contratos, código e parâmetros disponíveis; guardar saídas aceitas quando necessárias à auditoria | Executar produtores e verificadores de cada camada | Roundtrip de camadas tem aceites próprios; recuperação integral desses dados não é afirmada aqui |
| Fonte de eventos em `data/replay/<estado>/` | Preservar eventos e procedência que alimentaram a sessão; reconstrução exige produtor explícito e entradas verificadas | Produzir fonte própria antes de preparar a sessão | Não coberta pelo backup do tracking; percurso integral de geração ainda precisa ser qualificado |
| `data/storage/kind-platform/product-flow/session-<ID>/` | Preservar `input.json`, `session.json`, `events.sqlite`, `checkpoint.sqlite` e recibos de verificação como conjunto consistente; ledger inclui decisões, HTTP e feedback | Criar nova sessão vinculada à API/Registry do ambiente | Hashes preservados nos ensaios de ciclo/reboot; backup/restore destes bancos não foi aceito |
| `rounds/`, `model-updates/`, `serving-maintenance/` sob `data/storage/kind-platform/` | Preservar linhagem, aprovações, transações, identidade ativa e recibos; MLflow sozinho não substitui esses registros | Gerar recibos pelos controladores da nova rodada e entrega | Fora da captura limitada do tracking; não sintetizar aprovações nem reescrever pins |
| `data/storage/kind-platform/` — ownership, configuração privada, kubeconfig, credenciais e baselines | Manter acesso protegido e contexto da instalação; estes arquivos não bastam para recuperar volumes perdidos | Inicializar ownership e segredos próprios; gerar baseline após instalar a topologia | Privados, fora do Git; não copiar para fazer outro cluster se passar pelo original |
| `data/reports/case-explanations/`, `data/reports/fraud-portal/` e recibos em `kind-platform/portal/` | Preservar manifests/bytes se for necessário mostrar exatamente o relatório aceito; regeneração depende de modelo, fontes e verificadores compatíveis | Publicar novos snapshots, SHAP e desempenho após verificar a sessão | Exibição/readback aceitos; estes relatórios não fazem parte da captura do tracking |
| Prometheus e Grafana em PVCs próprios | Histórico de séries e alterações locais só sobrevivem se o armazenamento correspondente for preservado; provisionamento versionado não restaura histórico | Reinstalar dashboards/regras versionados; iniciar nova coleta | Mesmos volumes sobreviveram aos ciclos ensaiados; nenhum backup/restore desses PVCs foi aceito |
| Agregados voláteis do exporter, heartbeat, PIDs, port-forwards e logs de acesso | Projeções/processos podem ser reiniciados a partir da sessão e configuração; conservar logs quando forem evidência útil | Iniciar acessos e observador com vínculo local | Recuperação dos serviços do host aceita; PIDs antigos não são estado recuperável |
| `.venv`, `.portal-venv`, imagens auxiliares e unidades systemd | Reinstaláveis com fontes/dependências disponíveis; conservar imagens exatas quando exigidas pelo recibo | Construir ambientes/imagens e renderizar unidades para o caminho local | Reinstalar não garante imagem bit a bit idêntica; comparar identidades antes de reutilizar evidência |
| `data/tracking/`, `data/storage/tracking-lab/` e `mlruns/` do Alienware | Tratar como acervos separados até inventário; não apagar nem incorporar ao tracking da VM implicitamente | Não são pré-requisitos a copiar do autor | Fora do backup `mlflow_kind`; ausência de arquivos rastreados não significa ausência de valor |

Os quatro PVCs locais observados são `hostPath`, com política de reclaim `Delete`.
Persistência através de recriação de pod/reboot não protege contra exclusão de
PVC/cluster, perda de disco ou perda física do host. Não copiar diretórios de
PostgreSQL ou SQLite ativos como se fossem backups consistentes. Qualquer novo
procedimento precisa coordenar writers e verificar a restauração; este documento
não autoriza parar serviços nem copiar os bancos agora.

## Aceite já encerrado e limite do fechamento

O [tracking](VM_TRACKING_BACKUP.md#restauração-da-cópia-fora-da-vm--2026-10-30)
tem captura fechada por manifesto, restauração em alvo temporário e paridade do
champion. A origem e a captura foram preservadas e os alvos temporários removidos.
Uma linha pontuada no smoke valida recarga/paridade; não é nova avaliação global.

A cópia no Alienware fica fora do guest, mas pode compartilhar o SSD físico da VM.
`platform_backup_restore_validated` e `physical_host_loss_validated` continuam
false. Não repetir esse restore para encerrar a classificação documental.

O fechamento de reprodução do portfólio exige um percurso limpo verificado e
limitações explícitas. Recuperação integral da mesma instalação permanece um
backlog separado: inclui sessão, governança, datasets e eventual retenção de
métricas. Não exige implantar outro banco, broker ou orquestrador para esta etapa.

## Gate de instalação limpa — ainda não executado integralmente

O [guia KinD](KIND_PLATFORM.md) consolida dependências, mas combina aceites de
incrementos. O [guia da rodada](OPERATIONAL_ROUND.md) parte de cluster e Bronze
já preparados. Não tratá-los como evidência de uma instalação única do zero.

Há um ponto concreto a resolver: `infra/make/session.mk` define
`SESSION_SOURCE_STATE=data/replay/paced-native-v1`. `session-prepare` recebe esse
estado, e a preparação ampliada em `scripts/product_session/configuration.py`
abre seu `checkpoint.sqlite`. O checkout não traz esse banco. O roteiro precisa
identificar e testar seu produtor/entradas, sem copiar o checkpoint do autor nem
gerar decisões históricas apenas para satisfazer um caminho padrão.

| Ordem | Entrega da instalação nova | Evidência necessária |
| --- | --- | --- |
| 1 | Checkout identificado, pré-requisitos e orçamento do host | Commit, versões, ferramentas, espaço e dependências conferidos; sem acervos privados pré-carregados |
| 2 | Fundação, ownership e tracking próprios | PostgreSQL/RustFS/MLflow saudáveis; credenciais privadas; controle de leitura/escrita/recarga sem dados do autor |
| 3 | Bronze identificado e cadeia Silver/Gold | Origem, hashes, completude, produtores e verificações registrados; sem depender de bundle privado pré-existente |
| 4 | Rodada e champion próprios | Dados/Inputs, candidatos, política, alias, API e gates de entrega conferidos; não reutilizar o SHA do champion do autor como resultado esperado |
| 5 | Fonte de eventos e sessão limitadas | Comando produtor da fonte explícito, janela e ID próprios, chegada/feedback e reconciliação independente; nenhum recibo histórico fabricado |
| 6 | Portal, SHAP e monitoramento vinculados à sessão | Manifests, verificação HTTP, navegação e escopo visual; agregados ligados à sessão correta, sem inventar métricas sem suporte |
| 7 | Acessos, supervisão e operação | Caminhos locais, unidades e vínculo próprios; verificações de continuidade planejadas e recibos preservados |
| 8 | Revisão final de reprodução | Sequência realmente executada, interrupções/lacunas registradas, custos/tempos observados e limitações; concluir só após saída nativa |

O caminho para novo usuário precisa indicar IDs em todas as chamadas correlatas
(rodada, fonte, sessão, portal, explicações e desempenho). IDs novos são para
uma instalação nova; não são um recurso para contornar falha de retomada.
Credenciais/ownership antigos não entram como entrada. Referências históricas no
Git podem explicar o projeto, mas não satisfazem gates operacionais do novo ambiente.

Executar esse gate em ambiente novo isolado, sem recriar/excluir o cluster aceito
da VM nem adotar seus PVCs. Se não houver recursos para outro ambiente, documentar
a limitação e planejar o ensaio; não marcar a instalação integral como concluída.

## Base da revisão

- [Catálogo de destinos e produtores](../../references/persistence_catalogue_v1.json): classificação de caminhos; campos históricos permanecem intactos.
- [Sessão](PRODUCT_SESSION.md), [rodada](OPERATIONAL_ROUND.md) e [bootstrap do portal](VM_PORTAL_BOOTSTRAP.md): dependências e recibos de cada etapa.
- [Progresso da VM](VM_REPRODUCTION_PROGRESS.md), [supervisão](VM_USER_SUPERVISION.md) e [tracking](VM_TRACKING_BACKUP.md): aceites nativos e limites já registrados.

Esta revisão não coletou inventário adicional na VM, não verificou payloads de
datasets e não testou perda do host. Não modifica contratos científicos,
credenciais, bancos, imagens ou configurações dos serviços.
