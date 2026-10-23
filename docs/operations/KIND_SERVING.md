# Serving sintético no KinD — instalação aposentada

A release `fraud-serving` foi desinstalada em 2026-10-23 após a adoção do
champion em `fraud-detector`. Saíram os deployments blue/gateway, seus dois
Services e o ConfigMap do gateway. Recursos externos, pesos, PVCs e histórico
Helm foram preservados; API, tracking, armazenamento, monitoramento e portal
passaram nos readbacks antes/depois. O serviço blue sintético não estava pronto;
não foi necessário repará-lo para retirar uma instalação que já tinha substituta.
[Evidência de retirada](../../references/evidence/synthetic_serving_retirement_author_validation_2026-10-23.json).

Os três módulos `scripts/platform/kind_serving*.py`, seu teste exclusivo, o live
gate `kind-serving` e seus quatro alvos Make foram retirados. Os artefatos e
recibos privados permanecem; código histórico está no Git anterior à retirada.
Não use este documento para instalar ou operar o champion atual.

O chart `infra/helm/fraud-serving` e o Dockerfile do carrier continuam necessários
para a primeira implantação canônica. A qualificação Docker e o consumo da imagem
na CI continuam ativos. Os gates HTTP compartilhados agora estão em
`scripts/operational_round/serving.py`; a consulta CRI da observabilidade usa o
helper compartilhado, sem importar o controlador aposentado.

[Primeira rodada](OPERATIONAL_ROUND.md), [atualizações governadas](MODEL_UPDATES.md),
[operação diária](KIND_OPERATIONS.md) e [produto](PRODUCT_OPERATIONS.md).

## Aceite nativo — 2026-10-21

O autor aprovou os seis eventos do gate em 2026-10-21: baseline, bloqueio de
pacote inválido, candidata direta, troca na mesma URL, falha HTTP 502 e retorno
por `helm rollback`. A imagem qualificada da API foi reutilizada, sem rebuild
do runtime ou fit; somente o carrier foi construído. Os mesmos pesos sintéticos
produziram score `0.03463335444035967` antes/depois da troca e do rollback.
Baseline/gateway mantiveram seus pods e a fundação permaneceu igual; candidata
removida, baseline instalada no laboratório e port-forward temporário encerrado.

| Verificação | Resultado observado |
| --- | --- |
| Entrada confiável | Commit `0b95136a462583441044d50de0884a558777b260`; descritor `aa0e79886800765c516d3ed776295dc46d657a7ea50eb7c88900241d8c0ff0d7` |
| API qualificada | Docker ID `sha256:bc2ba70a90de114613bd8679e79ccfbb7a17de048f72f16323ca26713e1dae8d`, sem rebuild |
| Importação | Três arquivos `linux/amd64`, identidades CRI conferidas e arquivos temporários removidos; digests de importação locais não representam publicação em registry |
| Identidade blue → green | `c298299b7d3540d3968b5e715937b923` → `4953dc6c2a1047cd8fc16c575d81b49b` |
| Pesos preservados | Modelo SHA-256 `33c6fbab88b0876fef76eb754ebe7c130294e6267d736452a38851e7c4e31106` |
| Gates HTTP | Identidade e score iguais ao esperado na baseline, candidata direta, gateway e rollback; entradas inválidas retornaram 422 |
| Troca e falha | EndpointSlice pronto da candidata; zero endpoints prontos após interrompê-la; HTTP 502 observado |
| Rollback Helm | Revisão baseline 1 restaurada na revisão 5; blue ativa, candidata removida, mesmos pods e Services da baseline/gateway |
| Duração | Rollback observado em 1,99 s numa execução controlada; não é SLA ou demonstração de zero downtime |
| Teste | 1 passou em 19,48 s; tox OK em 34,53 s |

A URL `http://127.0.0.1:34297` existiu somente durante esse gate e não é um
endpoint permanente. Para novas inspeções, usar os comandos de operação acima.
Os Services privados e a baseline sintética ficaram instalados; fundação e
entrega original foram preservadas. Não houve promoção histórica, migração
primária, publicação em registry ou validação de recuperação por perda do host.

[Evidência completa](../../references/evidence/kind_serving_author_validation_2026-10-21.json),
com origem no terminal enviado pelo autor. O arquivo original do recibo não foi
fornecido em bytes; seu hash não foi inferido da formatação no terminal.
Próximo recorte: observabilidade no KinD; a pendência operacional de reboot
abaixo continua aberta.
