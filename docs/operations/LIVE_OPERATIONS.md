# Jornada diária: chegada, investigação e operação simultâneas

Este recorte verifica uma sessão nova com um dia completo do Handbook enquanto
produtor, fila do portal único e métricas da mesma sessão avançam. Reutiliza os
CLIs e os serviços já aceitos; não cria broker, serviço, imagem ou controlador.
A implementação está preparada; o aceite nativo desta jornada permanece pendente.

O operador inicia o produtor. O sistema transacional é representado pelo replay
temporal; o analista recebe a fila sem pedir novos dados. A sessão registra a
janela, features causais, pesos, release, política e feedback atrasado. A avaliação
completa e as explicações já verificadas permanecem separadas no portal.

## Preparar o checkout

Parta da main integrada pelo PR #11, commit `e53d3e3`. Crie a branch
`feat/live-fraud-operations-rehearsal`, aplique o pacote, execute os testes locais
e faça o commit seletivo antes de preparar dados. A preparação exige fontes
commitadas. As alterações deste recorte não mudam os pins do produtor/receiver
aceitos nem exigem reconstruir as imagens.

Execute primeiro `tox -e portal-ui`; o ensaio usa esse runtime isolado para uma
conferência automatizada do Streamlit. O portal aberto no navegador continua
sendo o mesmo serviço em 18501. Não se instala Streamlit no runtime científico.

## Preparar uma sessão diária nova

Encerre `session-monitor-watch` com Ctrl+C. O ensaio terá um observador próprio;
não execute outro produtor, watcher ou mudança de modelo/rota durante o ensaio.
Use os acessos normais para conferir a infraestrutura já ativa:

```bash
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-session-counts-v1.json &&
make session-daily-prepare \
  LIVE_SESSION_ID=arrivals-day-v1 SESSION_NO_OTHER_WRITERS=1
```

O default é `[2018-09-16T00:00:00, 2018-09-17T00:00:00)`. O alvo nominal 50.000
é um teto para capturar todas as transações disponíveis desse dia, não uma meta
de carga. Confira o total efetivo no relatório: o gate exige que ele coincida
com `eligible_transactions`, entre 1.000 e 50.000 decisões. O contexto de 14 dias
é reconstruído antes da pontuação; o feedback conserva atraso de sete dias.

A janela já foi observada no desenvolvimento do projeto: esta execução demonstra
operação, não cria novo holdout nem confirma qualidade independente.
`arrival-v1` e `arrival-2k-v1` são preservadas. A preparação não inicia a chegada.

## Vincular a operação explicitamente

No estado aceito em 2026-10-27, portal e monitor estão em `arrival-2k-v1`, com SHA
`f82d8fb8c2e25f533c31d03f7dc430b4a15ef2761758840506a7136de94edf3e`.
Os comandos abaixo arquivam esse vínculo e apontam ambos à sessão nova:

```bash
make session-monitor-prepare \
  SESSION_MONITOR_ID=arrivals-day-v1 \
  SESSION_MONITOR_PREVIOUS_SHA256=f82d8fb8c2e25f533c31d03f7dc430b4a15ef2761758840506a7136de94edf3e &&
make session-monitor-up &&
make portal-session-link \
  PORTAL_SESSION_ID=arrivals-day-v1 \
  PORTAL_PREVIOUS_SESSION_SHA256=f82d8fb8c2e25f533c31d03f7dc430b4a15ef2761758840506a7136de94edf3e &&
make access-stop ACCESS_SERVICE=portal &&
make access-stop ACCESS_SERVICE=grafana &&
make access-up &&
make portal-session-verify
```

A API e o champion não são reimplantados. O Helm reconcilia o vínculo do monitor;
seus pods de observabilidade podem reiniciar. Os acessos portal/Grafana são
reabertos para obter o vínculo e os pods atuais. O portal mostrará a sessão
preparada, ainda sem decisões. Os snapshots aceitos continuam consultáveis.

Se a mesma sessão já estiver vinculada, os comandos são idempotentes. Se houver
outro vínculo atual, inspecione seu SHA: não use o valor do exemplo para substituir
uma origem diferente. Não apague os ponteiros para contornar essa verificação.

Salve o baseline desta configuração, sem sobrescrever o anterior:

```bash
make platform-baseline \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-arrivals-day-v1.json \
  PLATFORM_TRACKING_REPORT=data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json &&
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-arrivals-day-v1.json
```

## Executar o ensaio

Não execute `session-run` manualmente nessa sessão antes do gate. O teste será
o produtor e fará a pausa/retomada. Deixe o portal em 18501 e o dashboard
`fraud-product-session` do Grafana abertos:

```bash
make session-live-test \
  LIVE_SESSION_ID=arrivals-day-v1 \
  LIVE_BASELINE=data/storage/kind-platform/operations/baseline-arrivals-day-v1.json \
  SESSION_NO_OTHER_WRITERS=1
```

O teste:

1. Confere baseline, vínculos, modelo/release, código commitado e fontes aceitas.
2. Publica agregados em paralelo ao produtor; exige crescimento real no Prometheus
   enquanto a sessão está em andamento, com o mesmo SHA.
3. Pausa nas primeiras 500 decisões, navega no portal, mantém as contagens e
   interrompe a coleta para conferir expiração. Verifica `/ready` da API nesse
   intervalo e recupera a publicação.
4. Retoma as chegadas e executa AppTest no runtime isolado enquanto o produtor
   ainda está ativo. Confere paginação e acesso às evidências, sem atribuir as
   métricas de validação à janela corrente.
5. Confere que o feedback das decisões do dia ainda está oculto. Avança depois
   o relógio para os feedbacks maduros e reconcilia todas as decisões/features
   com o verificador causal independente.
6. Reinvoca a sessão concluída, exigindo zero novas tentativas de scoring do
   produtor, fontes históricas preservadas, mesmo champion e check final do baseline.

O intervalo entre decisões é 0,05 s. Só o pacing representa ao menos 0,05 × N
segundos, além de contexto, HTTP, feedback e reconciliação. O tempo total não é
latência da API. p50/p95/máximo vêm dos recibos HTTP; a medida de RSS é o pico do
processo produtor, não a memória incremental da API ou do cluster.

A fonte expirada é diferente de um receiver indisponível ou uma API fora do ar.
O ensaio verifica `source_valid` e a consulta pelo Grafana; não afirma disparo de
notificação ou firing de regra sem um aceite próprio.

## Retomada e resultados

O progresso privado fica em `live-progress.json` na pasta da sessão. Um erro
solicita pausa do produtor antes de fechar os forwards e preserva o checkpoint.
Depois de resolver a causa, repita o mesmo gate/ID para retomar as fases salvas.
Se não houver prova de UI/métricas durante as chegadas, a conclusão é recusada;
uma sessão já consumida manualmente não pode receber esse aceite retroativamente.

O resultado completo fica em
`data/storage/kind-platform/product-flow/session-arrivals-day-v1/native-live-rehearsal.json`.
Ele registra janela, identidades, observações concorrentes, UI automatizada,
expiração/retomada, tempos, reconciliação e hashes das fontes preservadas.
Reexecutar um gate concluído apenas verifica o recibo e o ledger; não cria outra
jornada ao vivo. O relatório original não é sobrescrito.

Ao terminar, a coleta temporária do teste é encerrada. Retome a observação diária
em outro terminal para manter a fonte atualizada:

```bash
make session-monitor-watch SESSION_MONITOR_ID=arrivals-day-v1
```

Conferência humana: observe a fila crescer, navegue por páginas e casos, confira
a identidade no portal/Grafana, a pausa, a fonte expirada e sua recuperação. O
AppTest não equivale a uma inspeção visual no navegador; esse aceite será registrado
separadamente após a confirmação do autor.

O recorte encerra quando o gate nativo e a conferência visual forem aprovados.
Instalação limpa e promoção/rollback de sucessor com pesos diferentes continuam
como entregas separadas; o gate de qualidade não é afrouxado para produzir promoção.


## Leitura concorrente e bloqueio temporário do SQLite

O primeiro ensaio diário do autor parou no aquecimento, ainda com zero decisões
HTTP, porque uma leitura recebeu `SQLITE_BUSY` após o timeout inicial de 2 s.
O checkpoint e o diário de fases foram preservados; esse resultado não fecha o
aceite nativo. O erro não indica, por si só, corrupção nem falha do modelo.

Portal e coletor agora repetem a operação de leitura inteira apenas para os
códigos SQLite BUSY/LOCKED, com orçamento de tentativas de 15 s (uma chamada
SQLite já em curso pode consumir até mais 2 s do timeout existente). Cada
falha fecha a conexão e descarta o resultado parcial antes da próxima tentativa.
Não há leitura suja, uso de cache como evidência, alteração do journal ou escrita
no checkpoint. Erros de identidade, integridade e schema continuam sendo erros;
o tratamento curto da criação inicial do schema no ensaio permanece separado.

As transições de fontes são exatas e devem ser commitadas. Conservam recibos
legados, sessões expandidas, baseline e vínculo do monitor; o código do receiver
na imagem não mudou. Não é necessário reconstruir ou reimplantar imagens.

Após aplicar, testar e commitar a correção, reabra somente o portal e confira a
baseline já registrada. Não prepare outra sessão, não apague o checkpoint e não
recrie os vínculos:

```bash
make access-stop ACCESS_SERVICE=portal &&
make access-up &&
make platform-check \
  PLATFORM_BASELINE=data/storage/kind-platform/operations/baseline-arrivals-day-v1.json &&
make session-live-test \
  LIVE_SESSION_ID=arrivals-day-v1 \
  LIVE_BASELINE=data/storage/kind-platform/operations/baseline-arrivals-day-v1.json \
  SESSION_NO_OTHER_WRITERS=1
```

Mantenha o watcher externo encerrado. O ensaio retoma o mesmo checkpoint e o
mesmo `live-progress.json`; as fases só são aceitas após a verificação nativa.
Se o bloqueio persistir além do prazo, a operação falha e conserva os recursos
para diagnóstico, em vez de atribuir sucesso a uma leitura antiga.
