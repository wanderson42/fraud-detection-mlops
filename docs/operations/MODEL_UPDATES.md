# Substituição recorrente do modelo ativo

A rodada `portfolio-v1` foi concluída pelo autor: três modelos com Inputs nativos,
HGB versão 1 em `fraud-detector@champion`, pesos conferidos na API e rollback na
mesma URL. Este documento acrescenta o protocolo de sucessores. **Implementação
preparada e testada localmente; aceite nativo da substituição no KinD pendente.**

## Contrato da atualização

| Etapa | Regra executável |
| --- | --- |
| Reservar | Política/código commitados, Gold identificado e champion efetivamente servido conferidos |
| Treinar | Dummy fixo, LR e HGB; Inputs, partições, hashes, parâmetros e recarga de toda a validação |
| Comparar | Reavaliar o incumbent e todos os candidatos na mesma janela, com feedback maduro |
| Qualificar | AP ≥ 0,5; precisão diária por cliente @100 ≥ 0,45; superar Dummy; ganho absoluto de AP ≥ 0,005 sobre o incumbent e queda de P@100 ≤ 0,01 |
| Registrar | Criar versão e `challenger`; conservar `champion` e tráfego |
| Preparar | Conferir pesos no slot inativo, HTTP 422, readiness e p95 ≤ 500 ms em 20 chamadas sequenciais |
| Aprovar | Operador informa o SHA-256 exato da preparação e declara ausência de outros escritores |
| Liberar | Trocar o Service, conferir HTTP, atualizar champion e conferir alias/HTTP; retirar réplicas do slot anterior |
| Compensar | Se uma etapa falhar, iniciar o slot anterior, restaurar tráfego e alias e registrar o resultado |

Os limites são escolhas explícitas deste protocolo de laboratório, não regras
universais do MLflow. P95 aqui é um gate de smoke incluindo o port-forward;
não é benchmark de carga, SLA nem promessa de zero downtime.

`champion` significa a versão ativa. `challenger` identifica uma versão aprovada
para preparação, sem autorizar tráfego. Ao concluir a ativação, seu alias
`challenger` é removido; a versão anterior continua no Registry para rollback.

Há uma API ativa e um gateway fora dos ensaios. Os dois slots são mantidos durante
a preparação/liberação; o inativo fica com zero réplicas após liberação ou rollback.
Os nomes físicos blue/green alternam nas atualizações. Uma nova imagem portadora
é associada somente ao slot inativo: a preparação exige que o pod ativo conserve
sua identidade. O runtime da API é o mesmo runtime imutável já qualificado; este
protocolo altera pesos, não o código da API.

## Busca opcional por modelo

A política padrão é `references/model_update_policy_v1.json`:

- Dummy: fixo e sem overrides;
- LR/HGB: `mode: fixed`, com parâmetros do catálogo ou overrides explícitos;
- `mode: grid`: combinações categóricas, em ordem determinística, dentro do orçamento;
- `mode: optuna`: TPE com seed 42, escolhas categóricas declaradas, limite de trials e tempo.

A política de exemplo `model_update_policy_search_v1.json` usa grid de três valores
de C para LR e seis trials Optuna para HGB. Ela não garante melhoria. Não se deve
mudar os gates ou repetir buscas indefinidamente porque um candidato foi rejeitado.
Os limites de tempo são checados entre trials; o deadline do Job limita a execução
total, inclusive um fit individual. O Job usa até 2 CPUs e 4 GiB.

A busca só recebe o treino. Por padrão, ajusta em 01–14/04 e seleciona em
22–28/04; verifica que os rótulos do ajuste estavam disponíveis antes da seleção
interna. O pipeline escolhido é reajustado em todo o treino 01–28/04 e comparado
na validação 06–12/05. A janela externa não é argumento da busca.
Cada trial registra parâmetros, métricas e Inputs próprios em um experimento de
tuning, ligado ao run final. Os três candidatos finais recebem o mesmo gate,
independentemente de terem usado busca.

Este recorte conserva o protocolo Gold temporal v1 já examinado. Demonstra
reprodução, governança e substituição; não representa nova confirmação cega nem
melhoria científica independente. O teste original não é usado para seleção.
Novas janelas reais exigem versionar o protocolo e seus produtores, respeitando
as restrições do contrato Gold, antes de usar outro bundle. `--gold-bundle-id`
aceita somente um bundle Gold compatível publicado no RustFS do cluster.

O `comparison_as_of` é o relógio da fonte, não a data atual do computador. Todos
os rótulos de comparação devem estar disponíveis nesse instante. O cutoff de
treino do incumbent também deve preceder a comparação. Drift sozinho não chama
este fluxo nem autoriza promoção. Não foi acrescentado scheduler ou CT automático.

## Aplicar, testar e commitar

Use a branch atual. O ZIP inclui `LEIA-ME.md` com os comandos completos de commit,
sem incluir `.vscode/settings.json` ou `mlruns/`.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/model_updates tests/operational_round \
  tests/storage/test_managed_paths.py tests/storage/test_persistence_inventory.py \
  tests/serving tests/infra/test_platform_entrypoints.py
make update-validate
```

O novo catálogo inventaria os consumidores e recibos de atualização. A ponte
`model_update_compatibility_v1.json` autoriza somente essa extensão do catálogo e a atualização documental do README
na leitura do aceite inicial: contratos, código científico, node e ownership
originais continuam conferidos. Nenhum recibo de `portfolio-v1` é reescrito.
Após adotar o novo controlador, use `update-api` e `update-verify`; os comandos
congelados da primeira rodada mantêm seus pins originais e não administram sucessores.

## Primeiro ensaio: recusar uma troca sem ganho

Com os serviços do cluster prontos e os arquivos commitados:

```bash
make update-init &&
make update-build &&
make update-train &&
make update-qualify UPDATE_NO_OTHER_WRITERS=1 &&
make update-verify
```

O ID padrão é `successor-v1`. Com os mesmos dados e parâmetros da rodada anterior,
o esperado é `retain_champion`: isso comprova que terminar um treino não basta
para substituir o modelo. Nenhuma versão challenger é criada quando os gates
falham. O relatório e os três runs são preservados.

## Busca opcional e preparação

Se o próximo experimento acordado incluir otimização, reserve outra identidade
com a política de busca já commitada:

```bash
make update-init UPDATE_ID=optimized-v1 \
  UPDATE_POLICY=references/model_update_policy_search_v1.json &&
make update-build UPDATE_ID=optimized-v1 &&
make update-train UPDATE_ID=optimized-v1 &&
make update-qualify UPDATE_ID=optimized-v1 UPDATE_NO_OTHER_WRITERS=1
```

Só continue se o resultado for `challenger_registered`:

```bash
make update-stage UPDATE_ID=optimized-v1 UPDATE_NO_OTHER_WRITERS=1
```

Confira `gates`, versões, hashes e a identidade do novo modelo. O comando imprime
`approval_sha256`. Ele não libera tráfego nem altera `champion`.

## Aprovação explícita e verificação

Após revisar a preparação, informe seu digest literalmente:

```bash
make update-release UPDATE_ID=optimized-v1 \
  UPDATE_NO_OTHER_WRITERS=1 \
  UPDATE_APPROVAL_SHA256=COLE_O_APPROVAL_SHA256_DA_PREPARACAO &&
make update-verify UPDATE_ID=optimized-v1
```

O controlador reconfere os bytes, o runtime carregado e o slot inativo antes da
transição. A aprovação só vale para essa preparação. Registry e Service não
oferecem uma transação distribuída: uma curta divergência é possível entre os
passos; ela é tratada por journal e compensação, sem alegação de atomicidade.
O lock local não bloqueia outro cliente do cluster/Registry; a declaração de
escritor exclusivo é necessária. Mudanças inesperadas levam à recusa/revisão.

```bash
make update-api UPDATE_ID=optimized-v1
# Em outro terminal:
make platform-mlflow
```

## Recuperação e rollback

O diretório é `data/storage/kind-platform/model-updates/<update-id>/`.
`update.json`, `training.json`, `assessment.json`, `challenger.json` e `stage.json`
são recibos de identidade. `release.json` é o journal mutável; `release-events/`
conserva snapshots imutáveis das transições. `current.json`, no diretório pai,
identifica o modelo ativo após atualizações.

Se o processo terminar abruptamente entre Service e Registry:

```bash
make update-recover UPDATE_ID=optimized-v1 UPDATE_NO_OTHER_WRITERS=1
```

A recuperação devolve o estado anterior da liberação autorizada. Não inicia um
novo treino. Se não conseguir restaurar, conserva `recovery_required`; não anuncia
sucesso nem sobrescreve um alias de terceiros. Uma release ativa exige rollback
explícito, em vez de `recover`.

A saída de `update-release` informa `rollback_approval_sha256`. Para voltar após
uma liberação concluída:

```bash
make update-rollback UPDATE_ID=optimized-v1 UPDATE_NO_OTHER_WRITERS=1 \
  UPDATE_APPROVAL_SHA256=COLE_O_ROLLBACK_APPROVAL_SHA256 &&
make update-verify UPDATE_ID=optimized-v1
```

Models/datasets anteriores não são apagados. Runs parciais e Jobs com falha são
preservados; não são silenciosamente duplicados. Não crie IDs diferentes para
contornar um gate ou uma falha sem antes revisar a causa.
