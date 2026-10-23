# Sessão operacional de investigação

O sistema transacional entrega eventos; o produtor reconstrói o contexto causal,
chama a API do champion e persiste resposta e triagem numa transação SQLite.
O analista acompanha a fila, consulta features e investiga alertas. Ele não precisa
solicitar novas transações para que o fluxo avance. O score prioriza investigação;
não é uma decisão definitiva de fraude nem uma probabilidade calibrada.

Este recorte conecta chegada gradual, checkpoint e consulta corrente. Reutiliza
`HTTPReplay`, o construtor de features, a política de triagem e o verificador
independente existentes. Não instala outro serviço no KinD. O modelo e a release
são fixados na preparação; respostas de outros pesos são recusadas. Não treina,
não atribui aliases e não altera a rota blue/green.

## Responsabilidades e fontes

| Papel | Operação |
| --- | --- |
| Sistema transacional | Origina transações; nesta demonstração, o produtor reproduz eventos históricos do Handbook |
| Operador | Prepara, inicia, pausa e retoma a sessão; controla o avanço para feedback posterior |
| Analista | Acompanha a fila atualizada automaticamente, inspeciona features, resposta HTTP e motivo da triagem |
| Equipe de ML | Consulta avaliação completa e linhagem no portal de evidências e MLflow; qualifica sucessores pelo gate existente |
| Equipe de operação | Usa o ciclo de vida canônico e os acessos registrados; identifica separadamente a fonte dos dashboards Grafana |

A sessão usa uma nova cópia dos eventos originais, não as decisões do replay.
Aplica o contrato já revisado de 200 transações, incluindo pares no mesmo instante
(até 256). O acervo é maior; ampliar ou trocar a janela exige uma revisão explícita
da política de entrada, não mudar um limite escondido no dashboard. O replay dessa
janela demonstra o produto operando, sem constituir nova avaliação independente.

## Preparar e iniciar

Pré-requisitos: plataforma corrente pronta, champion consistente no Registry/API,
runtime do portal instalado (`make portal-install`) e implementação deste recorte
revisada e commitada. Suspenda writers do estado fonte, Registry e rota durante
a preparação; mantenha Registry/rota estáveis enquanto a sessão pontua.

```bash
make session-prepare SESSION_NO_OTHER_WRITERS=1
```

A preparação imprime o diretório privado e o SHA-256 de `session.json`. O ID padrão
é `arrival-v1`; altere `SESSION_ID` em **todos** os comandos se escolher outro ID.
O ID cria `data/storage/kind-platform/product-flow/session-<ID>`, sem alterar
`champion-http-v1`, `investigation-v1`, suas explicações ou avaliação.

Em um terminal, abra a consulta corrente:

```bash
make session-open
```

Em outro, inicie o produtor:

```bash
make session-run SESSION_NO_OTHER_WRITERS=1 SESSION_INTERVAL=1
```

`SESSION_INTERVAL` é a espera entre transações pontuadas (0,05–60 segundos), não
uma taxa garantida nem o intervalo original entre transações. O contexto de 14 dias
é reconstruído primeiro sem esperas artificiais. Depois chegam as transações da
janela e os feedbacks já disponíveis até aquele instante, na ordem causal original.
A interface se atualiza a cada dois segundos e identifica sessão, pesos, release,
relógio dos eventos, contagem de decisões e idade da atualização do produtor.
O tempo histórico comprimido fica explícito; não é apresentado como horário atual.

A consulta é somente leitura e usa uma única transação SQLite para contagens,
watermark, decisões, recibos HTTP e feedback. Não precisa disputar o lock do
produtor. Um produtor sem heartbeat recente aparece como desatualizado; isso não
apaga decisões já persistidas. Falhas de leitura não são substituídas por dados
inventados ou por outra sessão.

## Pausar, retomar e completar feedback

```bash
make session-pause
make session-status
# Após status paused, retome o mesmo checkpoint:
make session-run SESSION_NO_OTHER_WRITERS=1 SESSION_INTERVAL=1
```

A pausa é uma solicitação: aguarde o reconhecimento do produtor. Um evento em
andamento pode terminar antes dela. Ctrl+C no produtor também pausa; Ctrl+C na
interface fecha somente a interface. Reabra `session-open` para continuar consultando.
Não use outro ID para contornar uma divergência de modelo, código ou fonte.

Quando as transações acabam, o estado é `awaiting_feedback`. Os feedbacks das
últimas decisões ainda estão sete dias à frente no relógio dos dados. Para
**avançar explicitamente esse relógio comprimido** e concluir o ensaio:

```bash
make session-feedback SESSION_NO_OTHER_WRITERS=1
make session-verify
```

`session-feedback` só é permitido após todas as decisões da janela. Não gera
novas transações, não pontua de novo e não antecipa rótulos na etapa de chegada.
O verificador final reconstrói causalmente o fluxo inteiro e confere os recibos,
features, respostas, triagem e feedback; apenas contar linhas não substitui esse
aceite. Reexecutar uma sessão concluída não duplica decisões ou alertas.
Uma chamada HTTP pode ser repetida se houver falha antes do commit; a garantia
é de unicidade no ledger persistido, não de entrega de rede exatamente uma vez.

## Segundo plano e logs privados

O produtor pode continuar sem prender o terminal. Use uma sessão por ID e não
inicie outro produtor antes de confirmar a pausa/saída do anterior:

```bash
umask 077
mkdir -p data/storage/kind-platform/product-flow/session-arrival-v1/logs
nohup make session-run SESSION_NO_OTHER_WRITERS=1 SESSION_INTERVAL=1 \
  </dev/null \
  >data/storage/kind-platform/product-flow/session-arrival-v1/logs/producer.log 2>&1 &
make session-status
```

Use `session-pause` e acompanhe `session-status` para encerrar a execução ativa.
O arquivo de log pertence ao operador; redirecionar com `>` inicia um log novo.
Toda a árvore está coberta por `/data/` no Git e pelos contextos de build revisados.
Não versione logs, checkpoints, transações ou recibos privados. Os acessos de API,
MLflow e Grafana continuam em `make access-up`/`access-status`; esses acessos não
iniciam o produtor nem alteram a sessão.

## Explicações, desempenho e observabilidade

A fila corrente é agnóstica ao algoritmo: usa o contrato HTTP e a identidade dos
pesos. Não importa estimadores. SHAP é uma capacidade opcional e aparece como
não preparado nas novas decisões; o dashboard não associa automaticamente uma
explicação antiga a uma nova inferência. O portal de evidências continua mostrando
suas explicações verificadas e a avaliação temporal completa de 67.255 transações.
Contagens desta sessão não estimam AP, recall global, incidência ou ganho financeiro.

**Os dashboards Grafana atuais continuam vinculados ao replay concluído aceito.**
Este recorte não muda o PVC/fonte do exporter nem declara esses gráficos como
monitoramento da sessão corrente. A integração dos indicadores desta sessão no
Prometheus/Grafana, com identidade, freshness e aceite próprios, permanece como
próximo recorte da consolidação. A fila atual e seus contadores vêm do mesmo
checkpoint; a observabilidade compartilhada ainda precisa dessa ligação.

## Validação e estado do aceite

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/product_session tests/product_flow tests/infra/test_platform_entrypoints.py
.venv/bin/python -m tox -e portal-ui
```

Após preparar uma sessão **nova, ainda não executada**, o ensaio nativo opcional
realiza uma pausa após a primeira decisão, retoma chegadas, avança o feedback e
executa a reconciliação independente:

```bash
FRAUD_PRODUCT_SESSION_LIVE=1 FRAUD_SESSION_NO_OTHER_WRITERS=1 \
  .venv/bin/python -m tox -e product-session
make session-open
```

Para outro ID no ensaio tox, informe também `FRAUD_SESSION_ID=<ID>`.
O relatório é `native-rehearsal.json` no diretório da sessão. A conferência visual
fica a cargo do autor. Implementação e testes locais são preparação; o aceite no
KinD e a revisão visual permanecem pendentes até receber suas saídas. Promoção e
rollback de sucessor com pesos diferentes continuam condicionados a candidato
qualificado; este recorte não os executa ou conclui.
