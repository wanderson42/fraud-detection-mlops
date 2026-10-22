# Parar e retomar o laboratório KinD

**Aceite nativo em 2026-10-21:** parada/subida e reboot manual planejado aprovados,
com o mesmo node/PVCs e persistência dos valores inotify. Não há recuperação
por perda do host, restauração de backup ou nova avaliação do modelo neste gate.

Para o champion canônico `fraud-detector`, a [manutenção da imagem instaladora](SERVING_RECOVERY.md)
trata startup repetido no mesmo volume, com comandos Make e sem alterar pesos/alias.
Os baselines deste documento pertencem ao ensaio que os gerou; não representam
automaticamente o estado de uma release posterior.

O controlador usa o kubeconfig privado e o namespace `fraud-platform`. Confere o
recibo da fundação, o ID exato do container do node, o UID Kubernetes e a identidade
do Docker Engine. Não usa o contexto global para escolher um cluster. Não cria
cluster, executa Helm upgrade, reconstrói imagens, apaga PVCs ou usa prune.
Os outros clusters e a stack Compose não são alvos deste procedimento.

## O que será interrompido

A API e os dashboards deste laboratório ficam indisponíveis durante o ciclo.
Feche sessões de port-forward (`open`) e finalize Jobs, replay, publicações,
ensaios e outros writers antes de declarar `--no-other-writers`. O lock local
coordena estes controladores; não bloqueia clientes externos. A declaração do
operador é necessária e não equivale a um mecanismo de exclusão no servidor.

| Parada, nesta ordem | Retomada, nesta ordem |
| --- | --- |
| Gateway → serving blue | RustFS → PostgreSQL |
| Exporter → Grafana → Prometheus | MLflow → serving blue → gateway |
| MLflow → PostgreSQL → RustFS | Exporter → Prometheus → Grafana |

Cada controller é escalado para zero, com precondições de UID, spec,
resourceVersion e número atual de réplicas. A rotina aguarda os pods terminarem
antes de avançar. Só então para o container exato do node. Na retomada, inicia o
mesmo container, aguarda API/node e restaura uma réplica de cada controller.
O chart continua sendo a origem declarativa das instalações; a escala temporária
é uma ação operacional e o plano original é restaurado ao final.

## 1. Pré-requisito do host

```bash
poetry run python -m scripts.platform.kind_operations host-check
```

O bootstrap deste host exigiu pelo menos `512` instâncias e `524288` watches de
inotify. Esses valores vêm do diagnóstico observado, não de uma recomendação
universal de dimensionamento. O controlador apenas lê os valores; não altera
sysctl nem reinicia Docker ou a máquina.

Para persistir o ajuste, caso ainda não esteja configurado, revise o arquivo
versionado e aplique manualmente:

```bash
sudo cp --no-clobber infra/kind/operations/90-fraud-platform.conf /etc/sysctl.d/90-fraud-platform.conf &&
cmp -s infra/kind/operations/90-fraud-platform.conf /etc/sysctl.d/90-fraud-platform.conf &&
sudo sysctl --load /etc/sysctl.d/90-fraud-platform.conf
```

Um arquivo existente diferente faz a comparação falhar: revise-o antes de alterar.
Outros arquivos sysctl podem definir os mesmos parâmetros. A verificação depois
do reboot comprova os valores efetivamente carregados, sem presumir qual arquivo
venceu a precedência. [sysctl.d](https://www.freedesktop.org/software/systemd/man/latest/sysctl.d.html).

## 2. Primeiro gate: parada/subida sem reboot

Use o recibo de tracking já aceito, sem treinar ou publicar outro modelo:

```bash
FRAUD_KIND_TRACKING_REPORT="data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json" \
FRAUD_KIND_NO_OTHER_WRITERS=1 \
  .venv/bin/python -m tox -e kind-operations
```

Esse comando interrompe temporariamente apenas o cluster deste projeto. O gate
registra baseline, parada, subida e verificação em
`data/storage/kind-platform/operations/`. Não reinicia a máquina. Sucesso nesta
fase mantém `host_reboot_validated=false`.

São comparados UIDs/specs de controllers, PVCs, Services, ConfigMaps e hashes de
Secrets; réplicas voltam ao plano inicial. Pods podem receber novos UIDs.
Credenciais não entram em texto nos relatórios. O baseline é privado e imutável,
com hashes dos controladores e identidade do host/engine/node.

A leitura funcional verifica o run MLflow sintético existente e seus dois pontos
de histórico, bytes/hash do modelo S3, identidade/score/HTTP 422 do serving e a
cópia de monitoramento, scrape, oito regras, proxy Grafana e cores dos dashboards.
O modelo de tracking não é carregado pelo cliente; o serving executa inferência
com a fixture sintética já qualificada. Não há fit, replay ou nova publicação.
O worker temporário de leitura S3 e os port-forwards são removidos pelos helpers.

## 3. Segundo gate: reboot manual planejado

Execute esta fase somente depois do primeiro gate aprovado. Prepare **outro**
baseline, com o ambiente pronto e o nome ainda inexistente:

```bash
poetry run python -m scripts.platform.kind_operations prepare \
  --tracking-report data/storage/kind-platform/tracking/rehearsal-e835e0ee8ce34106a203594a23c4bb3a.json \
  --baseline data/storage/kind-platform/operations/baseline-before-reboot.json &&
poetry run python -m scripts.platform.kind_operations stop \
  --baseline data/storage/kind-platform/operations/baseline-before-reboot.json \
  --no-other-writers
```

Confira o relatório `status=stopped`. **Reinicie a máquina manualmente.** Não
remova o container do node, o cluster, diretórios ou volumes. Ao voltar, abra o
terminal no repositório e confira se Docker está ativo:

```bash
systemctl is-active docker
```

Se estiver parado, inicie-o manualmente com `sudo systemctl start docker`. Em
seguida, retome e compare o estado:

```bash
poetry run python -m scripts.platform.kind_operations host-check &&
poetry run python -m scripts.platform.kind_operations start \
  --baseline data/storage/kind-platform/operations/baseline-before-reboot.json \
  --no-other-writers &&
poetry run python -m scripts.platform.kind_operations verify \
  --baseline data/storage/kind-platform/operations/baseline-before-reboot.json \
  --expect-reboot
```

O gate exige mudança real do `boot_id`, mesmo host/engine/node, parada controlada
registrada antes do reboot, valores inotify suficientes, mesmos recursos/PVCs e
leituras funcionais iguais. `docker restart` ou recriar um pod não satisfaz essa
prova. Se o nome do baseline já existir, preserve-o e escolha outro nome.

Para voltar a ver os dashboards, abra novos port-forwards pelos comandos
suportados em [KIND_OBSERVABILITY.md](KIND_OBSERVABILITY.md). As URLs locais são
temporárias; a porta anterior não é uma identidade permanente do serviço.

## Falhas e limites

Em caso de falha, preserve recursos e relatórios. `rehearse` tenta restaurar as
réplicas salvas, mas continua falhando se qualquer verificação falhar. Para uma
parada parcial ou retomada manual, use `start` com o mesmo baseline e a declaração
de ausência de writers. Não substitua a recuperação por `create`, `up`, `helm
uninstall`, `docker rm`, prune ou exclusão de PVCs. Se uma identidade/spec mudou,
o controlador recusa prosseguir: investigue a divergência antes de adaptar o plano.

`status` consegue reconhecer o node parado sem consultar a API Kubernetes.
Depois de um reboot não planejado, use diagnóstico; não marque retroativamente
uma parada como planejada. Este procedimento cobre **parada ordenada antes do
reboot**, não recuperação de interrupção brusca de energia.

A persistência dos PVCs locais depende do mesmo node/container e disco. O gate
não relê todos os bundles Bronze/Silver/Gold, não restaura backups, não reconstrói
o cluster e não prova recuperação por perda do host. Os aceites anteriores dos
datasets e dos Jobs continuam separados. Os testes com mocks permanecem separados do aceite nativo. Os dois gates foram
aprovados pelo autor e o fechamento integrado está no [guia da plataforma](KIND_PLATFORM.md).

Referências: [kubectl scale e precondições](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_scale/),
[Docker stop](https://docs.docker.com/reference/cli/docker/container/stop/).


## Aceite nativo — 2026-10-21

| Verificação | Resultado observado |
| --- | --- |
| Ciclo sem reboot | 1 teste aprovado em 252,23 s; tox 264,31 s |
| Reboot real | boot_id mudou de `dfa7e97e-495e-49f6-bc49-f235f91db516` para `f04f4e68-1363-42be-9ce4-50a1339e0b56` |
| Identidade e réplicas | Mesmo container/node/engine, controllers, configurações e PVCs; oito workloads com uma réplica novamente |
| Inotify depois do reboot | 512 instâncias, 524288 watches; valores suficientes sem reaplicação no comando de verificação |
| Tracking | Run existente e dois pontos históricos iguais; modelo S3 com SHA-256 preservado |
| Serving | Release sintética `c298299b7d3540d3968b5e715937b923`; score `0.03463335444035967`; entrada inválida HTTP 422 |
| Monitoramento | Arquivos da cópia conferidos, 556.824 eventos/143.945 decisões/1.453 alertas, oito regras saudáveis, proxy Grafana e quatro dashboards com política de cores verificados |

Relatório do ciclo: `rehearsal-dd2b5c6bd7fb4ee392c121ef79441b38.json`.
Reboot: `stop-34144fbd33444ae0bf74745d050a4c49.json`,
`start-65bae4bbcd954dba9d432c5459697305.json` e
`verify-dd4464dcc9c146138a0650cd6ca6618c.json`.
Todos ficam sob `data/storage/kind-platform/operations/` no ambiente do autor.

[Evidência versionada](../../references/evidence/kind_operations_author_validation_2026-10-21.json)
transcreve os JSONs completos apresentados pelo autor e identifica os anexos por
hash. Não houve acesso independente ao daemon nem aos bytes dos arquivos nativos;
o hash de um anexo de terminal não é o hash do recibo original. As durações
pertencem a esse ensaio e não são um SLA. O reboot foi um gate CLI separado,
sem nova contagem pytest.

O aceite não valida nova conferência visual, firing de alertas, recuperação de
energia interrompida, perda do disco/host, backup/restore ou novo resultado de
qualidade do modelo. Os aceites anteriores desses outros escopos permanecem
separados. Não repita um reboot só para atualizar a documentação.
