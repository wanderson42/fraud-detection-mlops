# Sessões ampliadas: janela, volume e fila paginada

O operador escolhe um intervalo `[início, fim)` no relógio original do Handbook e
um volume nominal. As transações são selecionadas por tempo e ID, nunca por score
ou rótulo. O contexto causal de 14 dias é reconstruído antes da pontuação; os
feedbacks mantêm atraso de sete dias. A API, os pesos e a release permanecem os
fixados na preparação. Treino, alias e roteamento não são alterados.

A sessão `arrival-v1` já foi aceita no KinD: 269.334 eventos, 134.667 transações de
contexto/janela, 200 decisões HTTP e 13 alertas; pausa/retomada, feedback e
reconciliação aprovados. A reexecução fez zero chamadas de pontuação. Seu checkpoint,
input, recibo e relatório nativo são preservados. A evolução do leitor/controlador
tem uma transição exata de hashes revisada, que precisa estar commitada; não
reescreve recibos antigos nem aceita alterações arbitrárias de código ou fonte.

## Preparar 2.000 decisões em nova sessão

Após aplicar, testar e commitar este recorte, com a plataforma disponível e sem
writers concorrentes na fonte, Registry ou rota:

```bash
make session-prepare-expanded \
  SESSION_ID=arrival-2k-v1 SESSION_NO_OTHER_WRITERS=1 \
  SESSION_START=2018-09-16T00:00:00 \
  SESSION_END=2018-09-17T00:00:00 \
  SESSION_TARGET_DECISIONS=2000
```

Os três parâmetros de janela/volume são obrigatórios como conjunto na CLI;
o Make fornece esses defaults explícitos. O ID precisa ser novo. Uma preparação
incompleta não é apagada automaticamente. Repetir a mesma configuração preparada
é permitido; tentar mudar sua janela ou volume é recusado.

O volume nominal pode variar de 2 a 50.000 decisões, com janela de até sete dias.
Se houver menos eventos no intervalo, usa todos os disponíveis, exigindo ao menos
dois. Transações no mesmo timestamp que o último selecionado ficam juntas:
podem acrescentar até 1.024 decisões ao alvo nominal, sem ultrapassar o fim da
janela. O relatório de preparação informa o total efetivo e a disponibilidade.
Esses são limites operacionais revisados do ensaio, não capacidade medida da API.

A preparação usa os eventos originais persistidos na fonte concluída e reconstrói
features; não reaproveita suas previsões ou seus modelos. Reutilizar uma janela já
observada demonstra operação, sem criar um novo holdout de qualidade.

## Ensaio nativo e acompanhamento

Se o `session-open` anterior ainda estiver usando a porta 18502, encerre apenas
esse acesso com Ctrl+C no terminal correspondente; isso não para a API nem o cluster.
Em seguida, abra a mesma tela para a nova sessão:

```bash
make session-open SESSION_ID=arrival-2k-v1
```

Em outro, **antes de executar manualmente o produtor nessa nova sessão**:

```bash
FRAUD_SESSION_ID=arrival-2k-v1 FRAUD_SESSION_NO_OTHER_WRITERS=1 \
  .venv/bin/python -m tox -e product-session-scale
```

O teste pausa após cinco decisões, retoma a chegada, verifica páginas separadas
da fila, avança o relógio para feedback e reconcilia todas as decisões pelo
verificador causal independente. Confere que `arrival-v1` manteve os bytes de
`input.json`, `session.json`, `checkpoint.sqlite` e `native-rehearsal.json`. A
reexecução concluída não pode pontuar novamente. O resultado fica em
`data/storage/kind-platform/product-flow/session-arrival-2k-v1/native-scale-rehearsal.json`.

O ensaio usa espera mínima de 0,05 s entre transações pontuadas: só essa espera
representa pelo menos 100 s para 2.000 decisões. Contexto e verificação acrescentam
tempo próprio; o total do teste não é uma medida de latência da API.

A tela oferece 25, 50 ou 100 casos por página e filtro de alertas. O banco aplica
filtro, prioridade, limite e offset antes de carregar os JSONs dos casos. A
ordenação é alerta, score decrescente e ID. A fila é corrente: novas decisões
podem mudar as posições entre consultas; a navegação não promete um cursor
imutável de um banco em atualização. Cada consulta usa uma única transação de
leitura, com watermark consistente. Contadores e histogramas SQL cobrem **toda a
sessão**, não apenas a página exibida. A investigação detalhada opera sobre um
caso daquela página. A paginação não constitui verificação independente dos
recibos que ainda não foram exibidos; o gate final cobre todo o conjunto.

## Executar, pausar e verificar manualmente

Para outra demonstração, escolha outro ID e repita a preparação explícita:

```bash
make session-run SESSION_ID=arrival-2k-demo SESSION_NO_OTHER_WRITERS=1 SESSION_INTERVAL=0.05
make session-pause SESSION_ID=arrival-2k-demo
make session-status SESSION_ID=arrival-2k-demo
# Repetir session-run retoma o mesmo checkpoint após reconhecer a pausa.
make session-feedback SESSION_ID=arrival-2k-demo SESSION_NO_OTHER_WRITERS=1
make session-verify SESSION_ID=arrival-2k-demo
```

Execute `session-feedback` após concluir a chegada. Os controles são do operador;
o analista recebe os casos automaticamente. Logs privados com `nohup` seguem
[PRODUCT_SESSION.md](PRODUCT_SESSION.md), usando o diretório do novo ID. Os arquivos
continuam sob `/data/`, excluídos do Git e dos contextos de imagem por allowlists.

## O que é medido

| Relatório privado | Medida e interpretação |
| --- | --- |
| `preparation-*.json` | Tempo de preparação, checks de identidade e captura; separa decisões do histórico de contexto |
| `timing-*.json` | Wall time da invocação e fases `warmup`, `arrivals`, `feedback`; chegada inclui espera programada e feedback maduro intercalado |
| `verification-timing-*.json` | Tempo de reconciliação independente e latências HTTP extraídas dos recibos reais |
| `HTTP_latency_ms` | p50, p95 e máximo por interpolação linear dos recibos; inclui tentativas HTTP, exclui contexto, pacing e verificação |
| `process_lifetime_peak_rss_kib` | Maior RSS do processo Python no Linux, incluindo dependências/gates; não é memória incremental da sessão, do pod da API ou do cluster |

As durações são por invocação; pausas produzem relatórios distintos. Não some
percentis entre execuções nem derive throughput da soma de p95. `warmup` inclui
abertura/checagem do checkpoint e aquisição do lock. Não há meta de SLA declarada
por este ensaio. Peso, release, fonte e política precisam acompanhar a comparação.

## Próxima escala e limites do aceite

Após aceitar 2.000 decisões, uma janela diária pode ser preparada com outro ID,
mesmas datas de um dia completo e `SESSION_TARGET_DECISIONS=50000`: o total efetivo
será a disponibilidade desse dia. Primeiro confira tempo, memória, latências,
integridade e usabilidade; depois escolha janelas maiores dentro do protocolo.

O ensaio `arrival-2k-v1` foi aceito pelo autor: **2.001 decisões HTTP**, 81 alertas,
1.561 clientes e feedback das 2.001 decisões. O alvo nominal de 2.000 preservou
pares no mesmo timestamp. p50 21,51 ms, p95 110,47 ms, máximo 632,64 ms no cliente.
O warmup inicial de 369,42 s é reconstrução de contexto; não é latência de inferência.
A reexecução completa fez zero novas chamadas HTTP e a sessão de 200 permaneceu
inalterada. [Evidência](../../references/evidence/product_session_scale_author_validation_2026-10-23.json).

O [monitoramento da mesma sessão](SESSION_MONITORING.md) e o
[portal único](UNIFIED_PORTAL.md) foram aceitos separadamente. Os dashboards
históricos preservam sua fonte. A [jornada diária integrada](LIVE_OPERATIONS.md)
verificará chegadas e observação simultâneas; seu aceite nativo está pendente.
O teste de escala não valida CT ou promoção/rollback de sucessor com pesos diferentes.
