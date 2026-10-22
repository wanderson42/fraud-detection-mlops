# fraud-observability

Chart independente para o namespace fraud-platform. Três Deployments de réplica
única, Services ClusterIP, três PVCs mantidos e ConfigMaps provisionados.
`stateReady=false` instala sem ativar leitores; o controlador importa uma cópia
verificada antes de ativar. Não modificar valores à mão para contornar essa ordem.
Imagens locais exclusivas, imagePullPolicy Never, usuário não root, raiz somente
leitura, capabilities removidas e token ServiceAccount desativado.
O exporter não monta credenciais nem recebe acesso de escrita ao estado.

[Entrada, comandos, gate e limites](../../../docs/operations/KIND_OBSERVABILITY.md).
