# Comandos do projeto

O Makefile da raiz reúne as famílias de comandos; a execução continua nos
controladores Python, sem duplicar lógica de Helm, ownership, locks ou recibos.
`make help` apresenta a operação cotidiana. `make help-all` lista todos os alvos,
inclusive ciência, primeira instalação e ensaios de falha. Ambos funcionam antes
de instalar Poetry e não iniciam serviços.

| Arquivo | Responsabilidade |
| --- | --- |
| `access.mk` | Acessos locais com nohup, logs e parada por serviço |
| `platform.mk` | Fundação, serviços e operação do cluster |
| `round.mk` / `updates.mk` | Primeira rodada e atualização governada |
| `product.mk` | Eventos pela API e triagem persistida |
| `serving-recovery.mk` | Manutenção do instalador da release |
| `case-explanations.mk` / `portal.mk` | Explicações e consulta do analista |
| `development.mk` | Ambiente, qualidade e verificações de empacotamento |
| `science.mk` | Preparação local, experimentos e protocolos científicos |

`platform-api` abre o deployment ativo, validando Registry/rota/imagens/HTTP.
`round-api` é um alias para esse acesso. O acesso à antiga instalação sintética
foi retirado após sua desinstalação.
Nenhum deles treina, muda aliases ou instala workloads. Se o modelo ativo mudar,
reabra o acesso. Ctrl+C encerra somente o forward daquele terminal.

Os alvos operacionais continuam serializados mesmo com `make -j`. Parâmetros de
operação são expandidos como argumentos entre aspas. Parada/subida exigem o
baseline salvo e a declaração de ausência de writers; `help` não substitui esses
requisitos. Não há alvo `destroy`, prune ou reboot automático.

[Operação do produto](../../docs/operations/PRODUCT_OPERATIONS.md) e
[roteiro de apresentação](../../docs/operations/PORTFOLIO_WALKTHROUGH.md).

`make access-up` abre os quatro acessos principais sem prender o terminal.
`make access-start ACCESS_SERVICE=prometheus` abre um acesso adicional.
`make access-stop-all` fecha somente os acessos registrados neste checkout.
[Comandos e arquivos privados](../../docs/operations/LOCAL_ACCESS.md).

`make platform-check PLATFORM_BASELINE=CAMINHO` confere a topologia e os readbacks
atuais, sem exigir ciclo de parada/subida neste boot. `platform-verify` conserva
seu significado específico: verificar um ciclo controlado registrado. O baseline
atual após a retirada sintética está identificado no guia operacional.

`session.mk` separa a sessão de chegada gradual dos snapshots aceitos: preparar, executar/retomar, pausar, consultar, avançar feedback, verificar e abrir a fila corrente. Guia: `docs/operations/PRODUCT_SESSION.md`.
