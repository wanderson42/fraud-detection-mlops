# Portal único de investigação

O acesso canônico é **http://127.0.0.1:18501**, aberto por `portal-open` ou
`access-start ACCESS_SERVICE=portal`. A navegação reúne a sessão corrente e as
evidências verificadas no mesmo Streamlit, com o tema escuro e gráficos Plotly.

| Área | Fonte e finalidade |
| --- | --- |
| Sessão atual | Checkpoint causal da sessão vinculada; atualização automática, totais globais, fila paginada, contexto e feedback disponível |
| Visão geral | Snapshot de evidências verificado, com período e modelo na captura |
| Fila de investigação / Investigar caso | Decisões e explicações verificadas do snapshot |
| Desempenho do modelo | Avaliação completa com dataset, período, pesos, métricas e política explícitos |

O analista consulta o que chegou. Não há botão de ingestão, treino ou promoção.
O produtor continua enviando transações à API e o observador da sessão continua
publicando agregados para o Grafana. Este recorte consolida a interface sem
acrescentar serviço, porta, banco ou dependência ao runtime de apresentação.

## Vincular a sessão já aceita

Depois de aplicar, testar e commitar a implementação:

```bash
make portal-session-link PORTAL_SESSION_ID=arrival-2k-v1 &&
make portal-session-verify &&
make access-stop ACCESS_SERVICE=portal &&
make access-start ACCESS_SERVICE=portal
```

Reinicie somente o acesso do portal para carregar o vínculo e a implementação
novos. Os demais serviços permanecem na operação habitual. Se estiver usando
`make portal-open` em foreground, encerre essa interface com Ctrl+C e reabra-a.
Não mantenha simultaneamente outro processo usando a porta 18501.

O vínculo privado fica em
`data/storage/kind-platform/portal/investigation-v1-session.json`, modo 0600.
Ele fixa ID/recibo da sessão, digest do input, pesos, release e diretório gerenciado.
O launcher passa seu digest ao runtime isolado e não encaminha credenciais AWS,
PostgreSQL ou Kubernetes. A interface confere o vínculo e reutiliza o leitor
SQLite aceito, em transação somente leitura, sem carregar modelos ou fazer scoring.

O snapshot e a avaliação não são recriados. O vínculo é idempotente: repetir o
mesmo ID preserva os bytes. Trocar de sessão exige o digest anterior explícito:

```bash
make portal-session-link PORTAL_SESSION_ID=NOVO_ID \
  PORTAL_PREVIOUS_SESSION_SHA256=SHA_DO_RECIBO_ANTERIOR
```

“Sessão atual” significa a sessão explicitamente vinculada pelo operador. Uma
promoção posterior não transforma decisões anteriores em decisões dos novos
pesos; para acompanhar outro modelo, vincule a sessão correspondente.

O vínculo anterior é arquivado. Reabra a interface após trocar o vínculo: um
processo com digest antigo mostra erro em vez de mudar silenciosamente de fonte.
Alterações deste portal não mudam a configuração do cluster. O baseline
`baseline-session-monitoring-20261024.json` é histórico; a referência operacional
vigente está no [guia principal](PRODUCT_OPERATIONS.md).

## Investigação e explicabilidade

A sessão usa a mesma apresentação de contexto, feedback, features e identidade
que o snapshot, com leitura paginada de até 100 linhas. Os gráficos e contadores
cobrem toda a sessão, mesmo quando a fila mostra só os alertas de uma página.
Relações entre casos na investigação da sessão têm escopo explicitamente restrito
à página filtrada; não são apresentadas como todo o histórico do cliente.

Uma explicação verificada pode aparecer na sessão somente quando a requisição
completa e a resposta são idênticas às do caso verificado, incluindo features,
score, pesos e release. A origem conserva o digest do manifest de evidências.
A interface não recalcula SHAP e não usa feedback posterior na explicação.
Sem correspondência exata, o caso mantém contexto e decisão sem contribuições
fabricadas. A escolha do algoritmo permanece nos dados do modelo: não há HGB
fixado na interface. Outros pesos ou release também produzem um aviso de que a
avaliação das evidências não pode ser atribuída ao modelo da sessão.

## Fontes distinguíveis no mesmo portal

No cenário aceito atual, a sessão tem 2.001 decisões, 81 alertas e 1.561 clientes.
O conjunto de evidências mantém 200 decisões e 5 explicações; a avaliação mantém
67.255 transações de validação. Esses números não são somados nem renomeados
como se fossem uma janela única. A página de desempenho mostra a identidade do
modelo avaliado e as métricas de sua política, com unidade e período próprios.

Uma fonte de sessão inválida exibe erro; a interface não substitui a sessão por
um snapshot antigo. As evidências continuam acessíveis. Da mesma forma, evidências
inválidas não ocultam uma sessão válida durante a navegação. A associação a uma
sessão é opcional: sem vínculo, o portal mantém sua navegação de evidências.

O acesso independente `session-open` permanece como compatibilidade com recibos
e ensaios anteriores. A operação diária usa o portal único em 18501. Os arquivos
do produtor/leitor aceitos não foram alterados neste recorte, preservando os
hashes das sessões de 200 e 2.001 decisões. A retirada dessa entrada de
compatibilidade pode ser avaliada após o aceite da interface unificada.

## Testes e aceite

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/fraud_portal tests/product_session tests/session_monitoring \
  tests/product_flow tests/infra/test_platform_entrypoints.py \
  tests/infra/test_source_compatibility.py tests/infra/test_local_services.py \
  tests/storage/test_managed_paths.py tests/storage/test_persistence_inventory.py &&
.venv/bin/python -m tox -e portal-ui
```

Os testes isolados de UI verificam navegação entre fontes, paginação com totais
globais, falha de fonte sem fallback indevido e reutilização de explicação somente
por identidade exata. Os testes do vínculo verificam hashes, paths, credenciais,
idempotência, troca explícita e preservação dos arquivos de origem.

Depois de vincular a sessão concluída e abrir o portal:

```bash
make portal-session-smoke &&
.venv/bin/python -m tox -e portal-unified &&
make portal-verify &&
make portal-performance-verify
```

`portal-session-smoke` verifica saúde HTTP e leitura do vínculo; não afirma que
o navegador renderizou a interface. `portal-unified` executa o Streamlit AppTest
no runtime isolado com os arquivos reais aceitos: navega pela sessão, página
seguinte, evidências e desempenho, comparando os hashes antes/depois. Esse gate
exige uma sessão concluída para não disputar o teste de integridade com um
produtor. A revisão visual no navegador continua sendo um aceite separado.
Nenhum dos gates cria decisões, treina ou altera o champion.

Conferência visual: confira os totais da sessão, navegue por páginas e por
“Visão geral”/“Desempenho do modelo”, confirme as origens apresentadas e teste a
investigação com e sem explicação. O aceite funcional nativo deste recorte foi concluído pelo autor. O refinamento
do cabeçalho foi aprovado visualmente pelo autor; os relatórios originais são preservados.

## Aceite funcional e refinamento visual — 2026-10-24

O autor conferiu o portal com suas fontes reais: o ambiente `portal-unified`
passou (1 teste em 0,96 s; tox 12,69 s), com navegação entre sessão e evidências
e hashes preservados. Os testes direcionados tiveram 103 aprovados e 12 pulados;
o ambiente isolado de UI teve 44 aprovados. A captura enviada mostra a
investigação com contexto, score e explicação verificada.

O refinamento solicitado concentra-se na apresentação: cabeçalho maior e
responsivo, título com altura automática e quebra por palavras, menor recuo
superior e identificação visual da origem. Tema escuro, cores semânticas,
fontes de dados e navegação permanecem na aplicação existente.
[Registro do aceite funcional](../../references/evidence/unified_portal_author_validation_2026-10-24.json).
O autor aprovou o cabeçalho refinado em 2026-10-24 ("Ficou muito bom!");
o campo visual automatizado do ensaio anterior continua preservado.

## Fechamento do recorte

Portal único aceito funcional e visualmente, com acesso canônico em 18501,
sessão vinculada, investigação paginada e evidências com origens identificadas.
A sessão conserva 2.001 decisões/81 alertas; o snapshot conserva 200 decisões/5
explicações e a avaliação conserva 67.255 transações. O aceite não transforma
esses conjuntos em uma avaliação única nem altera o champion.
O baseline da época, `baseline-session-monitoring-20261024.json`, permanece histórico.
Consulte o [guia principal](PRODUCT_OPERATIONS.md) para a configuração atual.

O inventário e as otimizações aceitas da API/MLflow estão em
[IMAGE_FOOTPRINT.md](IMAGE_FOOTPRINT.md). O próximo aceite é a
[jornada diária integrada](LIVE_OPERATIONS.md), com chegadas e métricas simultâneas.
Este guia conserva o aceite do portal; o ensaio novo não recria seus snapshots.
