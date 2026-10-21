# Plataforma KinD/Helm: sequência integrada e operação

**Marco de laboratório concluído em 2026-10-21.** O aceite combina os recortes
nativos já registrados, encerrados pela parada/subida e pelo reboot planejado.
Este guia consolida a sequência; não afirma que todos os gates foram repetidos
em uma única execução. O próximo marco é simplificar os fluxos suportados e a
experiência de reprodução. Terraform vem depois.

## O ambiente demonstrado

Um cluster `fraud-platform`, contexto privado `kind-fraud-platform` e namespace
`fraud-platform`. PostgreSQL e RustFS mantêm estado em PVCs locais; MLflow usa
esse banco e o armazenamento de artefatos. Serving usa a imagem sintética já
qualificada e releases identificadas. Prometheus/Grafana/exporter apresentam uma
cópia somente de leitura de replay concluído. Jobs e replay são executados sob
demanda; não precisam permanecer processando para exibir o laboratório.

| Recorte aceito | O que foi demonstrado | Procedimento |
| --- | --- | --- |
| Fundação | PostgreSQL/RustFS prontos; SQL/S3 preservados ao recriar pods, mesmos PVCs | [Fundação](KIND_FOUNDATION.md) |
| Tracking | Modelo sintético publicado e recarregado por HTTP; hash S3 e nova recarga após recriar o pod | [MLflow](KIND_TRACKING.md) |
| Datasets | Bundles Bronze/Silver/Gold/desenvolvimento publicados, reenviados sem duplicação e restaurados com verificadores próprios | [Datasets](KIND_DATASETS.md) |
| Jobs | Bronze → Silver → Gold com novas saídas encadeadas e restores independentes; sem treino | [Cadeia de dados](KIND_PIPELINE.md) |
| Replay | Interrupção durante feedback, rollback e retomada no mesmo PVC; ledger igual ao contínuo e reentrega sem rescoring | [Replay](KIND_REPLAY.md) |
| Serving | Runtime qualificado preservado, blue → green → blue, pacote inválido bloqueado, falha 502 e rollback Helm | [Serving](KIND_SERVING.md) |
| Observabilidade | Cópia de replay conciliada, scrape, oito regras, proxy Grafana e quatro dashboards; exporter recriado no mesmo PVC | [Observabilidade](KIND_OBSERVABILITY.md) e [cores](DASHBOARD_COLORS.md) |
| Operação | Parada/subida e reboot planejado, mesmo node/PVCs, réplicas restauradas, inotify persistente e leituras funcionais | [Operação](KIND_OPERATIONS.md) |

Os dados históricos originais continuam preservados. Publicar bundles no RustFS
não muda automaticamente a origem dos leitores. A operação não migra o estudo
Optuna ou os stores históricos de MLflow, não promove a referência e não executa
CT. O monitoramento apresenta dados persistidos de um replay concluído, sem
nova ingestão ao vivo ou nova confirmação de qualidade do modelo.

## Instalação inicial: sequência de dependências

Siga esta ordem **somente para preparar um ambiente novo**; use os runbooks de
cada recorte para pré-requisitos, opções e caminhos dos artefatos. Os comandos
abaixo são pontos de entrada, não um script cego para um ambiente existente.
O bootstrap precisa de Docker, KinD, kubectl/Helm e inotify adequados ao host.
Credenciais e kubeconfig ficam privados; não devem ser commitados.

1. Fundação: `scripts.platform.kind_foundation validate`, `init`, `create`, `up`.
   O controlador recusa adotar outro cluster e não substitui ownership existente.
2. Tracking: `scripts.platform.kind_tracking validate`, `init`, `build`, `up`.
   O gate sintético próprio publica e recarrega a fixture; não treina a referência.
3. Datasets: `scripts.platform.kind_datasets inventory`, `build` e publicações
   dos bundles preparados, com restaurações e identidades explícitas no runbook.
4. Jobs: `scripts.platform.kind_pipeline validate`, `build` e ensaio da cadeia
   com os bundles aceitos. Execute só quando precisar produzir/verificar saídas.
5. Replay: `scripts.platform.kind_replay validate`, `build` e ensaio sintético
   separado. O teste termina o processamento e preserva seu PVC para inspeção.
6. Serving: `scripts.platform.kind_serving validate`, `prepare`, `up`, com a
   entrega qualificada e os hashes esperados explícitos. Não reconstrua o runtime
   para contornar uma divergência de identidade.
7. Observabilidade: `scripts.platform.kind_observability validate`, `init`,
   `prepare` com estado concluído e ausência de writers, `build`, `up`.
8. Operação: prepare um baseline com recibo de tracking explícito e faça os gates
   de parada/subida e reboot manual apenas quando a janela de interrupção estiver
   prevista. Preserve baseline e relatórios.

Cada módulo é executado por `poetry run python -m <módulo> <ação>`. Os runbooks
contêm os comandos completos e perfis tox opt-in. A simplificação seguinte vai
reduzir o esforço desse percurso e definir quais alternativas antigas seguem
suportadas; esta consolidação não cria outro orquestrador.

## Uso diário: inspecionar, acessar, parar e voltar

Para conferir o ambiente que já existe:

```bash
poetry run python -m scripts.platform.kind_operations status &&
poetry run python -m scripts.platform.kind_foundation status &&
poetry run python -m scripts.platform.kind_tracking status &&
poetry run python -m scripts.platform.kind_serving status &&
poetry run python -m scripts.platform.kind_observability status
```

Se o node estiver parado, comece por `kind_operations status` e retome pelo
baseline salvo; os demais status dependem da API Kubernetes. Não use `create`
para tentar recuperar um ambiente existente.

Para consultar os dashboards, mantenha o terminal do port-forward aberto:

```bash
poetry run python -m scripts.platform.kind_observability open --component grafana
```

Use a URL loopback informada pela execução e as credenciais locais privadas
conforme o runbook. Não suponha porta fixa após reiniciar a sessão.

Para parar e retomar, finalize writers/forwards e siga
[KIND_OPERATIONS.md](KIND_OPERATIONS.md). O arquivo de baseline é imutável: escolha
outro nome para uma nova janela operacional. `stop` encerra workloads na ordem de
dependência antes de parar o node; `start` restaura o mesmo plano e `verify`
compara as leituras. Reboot é sempre manual, com parada planejada antes e
`verify --expect-reboot` depois. Os recibos nativos já aceitos não devem ser
sobrescritos nem recriados apenas para atualizar documentação.

## Aceite operacional e fronteiras preservadas

O ciclo nativo teve 1 teste aprovado em 252,23 s. O reboot CLI subsequente mudou
o boot_id e preservou o container/node/engine, os PVCs e a configuração. Inotify
voltou em 512/524288. Tracking manteve o histórico e os bytes S3, a API retornou
a mesma release/score e HTTP 422 para entrada inválida; monitoramento conciliou
arquivos, métricas, regras e dashboards.
[Evidência completa](../../references/evidence/kind_operations_author_validation_2026-10-21.json).

O gate de operação relê amostras funcionais identificadas; não relê todos os
bundles dos datasets. A ausência de novo fit/replay preserva o orçamento e os
resultados históricos. O resultado não comprova backup/restore deste cluster,
recuperação de host perdido, falha abrupta de energia, disponibilidade contínua,
promoção de modelo histórico, suporte de todos os consumidores locais ou um
inventário global completo. Cada conclusão continua vinculada ao respectivo
recorte e recibo, sem ampliar os aceites anteriores.
