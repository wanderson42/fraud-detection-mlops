# Comandos do projeto

O Makefile da raiz reúne as famílias de comandos; a execução continua nos
controladores Python, sem duplicar lógica de Helm, ownership, locks ou recibos.
`make help` apresenta a operação cotidiana. `make help-all` lista todos os alvos,
inclusive ciência, primeira instalação e ensaios de falha. Ambos funcionam antes
de instalar Poetry e não iniciam serviços.

| Arquivo | Responsabilidade |
| --- | --- |
| `platform.mk` | Fundação, serviços e operação do cluster |
| `round.mk` / `updates.mk` | Primeira rodada e atualização governada |
| `product.mk` | Eventos pela API e triagem persistida |
| `serving-recovery.mk` | Manutenção do instalador da release |
| `case-explanations.mk` / `portal.mk` | Explicações e consulta do analista |
| `development.mk` | Ambiente, qualidade e verificações de empacotamento |
| `science.mk` | Preparação local, experimentos e protocolos científicos |

`platform-api` abre o deployment ativo, validando Registry/rota/imagens/HTTP.
`round-api` é um alias para esse acesso. `platform-synthetic-api` abre somente o
serving sintético original, quando esse ensaio é solicitado explicitamente.
Nenhum deles treina, muda aliases ou instala workloads. Se o modelo ativo mudar,
reabra o acesso. Ctrl+C encerra somente o forward daquele terminal.

Os alvos operacionais continuam serializados mesmo com `make -j`. Parâmetros de
operação são expandidos como argumentos entre aspas. Parada/subida exigem o
baseline salvo e a declaração de ausência de writers; `help` não substitui esses
requisitos. Não há alvo `destroy`, prune ou reboot automático.

[Operação do produto](../../docs/operations/PRODUCT_OPERATIONS.md) e
[roteiro de apresentação](../../docs/operations/PORTFOLIO_WALKTHROUGH.md).
