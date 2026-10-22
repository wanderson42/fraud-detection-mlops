# Diagnóstico do bootstrap dedicado

<!-- kind-foundation-acceptance-2026-10-20:start -->
**Estado em 2026-10-20 — fundação KinD/Helm aprovada.** PostgreSQL e RustFS
prontos; marcadores SQL/S3 preservados após recriar os pods, com os mesmos PVCs.
Regressão nativa: 45 testes aprovados. Bootstrap corrigido após ajuste de
inotify no host; persistência desse ajuste após reboot ainda não confirmada.
O fluxo completo no KinD segue em andamento; próximo incremento: MLflow.
[Aceite, pré-requisito e limites](KIND_FOUNDATION_ACCEPTANCE.md). As notas anteriores mantêm seu contexto histórico.
<!-- kind-foundation-acceptance-2026-10-20:end -->


Em 2026-10-20, o autor aprovou 30 testes e os templates, mas a criação do cluster
falhou em `kubeadm init`: API server em `172.18.0.4:6443` recusou conexões.
O KinD removeu o node da tentativa. Chart, PVCs, RustFS e PostgreSQL ainda não
foram implantados. Avisos sobre kubeadm `v1beta3` não comprovam a causa raiz.

Este incremento adiciona uma entrada de diagnóstico; não altera a imagem,
o chart, a versão do KinD ou parâmetros globais do Linux. A causa continua
pendente até analisar kubelet/containerd e os containers do control plane.

## Executar com o controlador existente

As credenciais já inicializadas são preservadas. Confirmou-se que não havia
cluster/node, `kubeconfig` ou `owner.json` após a primeira falha. Executar:

```bash
.venv/bin/python -m tox -e py314 -- tests/infra

poetry run python -m scripts.platform.kind_bootstrap_diagnostics create
```

O módulo chama `kind_foundation.create`, preservando seus guards de identidade,
mas acrescenta `--retain` à criação. Em caso de falha, o node fica disponível
para inspeção e o comando termina com código 1, após salvar e imprimir o
diagnóstico. Essa falha é esperada enquanto o bootstrap não for corrigido.
Se a criação funcionar, o controlador original grava o recibo normalmente;
esse sucesso ainda não valida os serviços ou a persistência.

O relatório novo fica em
`data/storage/kind-platform/bootstrap-diagnostics-<id>.json`, modo 600.
Contém erro do KinD, amostra de logs do kubelet/containerd e até duas instâncias
de cada componente (API server, etcd, controller manager e scheduler). A saída
do terminal mostra um resumo; o relatório mantém limites maiores para análise.

Só executa leituras dentro do node `fraud-platform-control-plane` após conferir
labels de cluster e papel. Não salva `Config.Env`, kubeconfig, conteúdo de
Secrets ou chaves privadas. Remove credenciais locais conhecidas, blocos PEM
de chave privada e autenticação em URLs de proxy dos trechos coletados. Essa
filtragem é defesa adicional, não prova de anonimização de qualquer log futuro.

Também registra leituras de memória, limites inotify e espaço no filesystem do
checkout. Esses números ajudam a investigar recursos; não são diagnóstico
automático de causa nem medição da capacidade do filesystem Docker separado.
Nenhum sysctl é alterado e nenhum cluster é parado ou excluído.

## Se o node já estiver retido

Não repetir `create` para contornar ausência de `owner.json`: o guard original
recusará adoção. Para reler os logs sem criar outro cluster:

```bash
poetry run python -m scripts.platform.kind_bootstrap_diagnostics inspect
```

Enviar o resumo do terminal. Com ele, decidir a correção específica. Não apagar
o node antes da análise, não fabricar recibo, não aumentar limites ou desativar
segurança apenas com base na mensagem `connection refused`.

O fluxo normal `up` e `rehearse` só continua após criação e identidade aprovadas.
Os clusters de energia, credenciais e volumes Compose não são adotados.

Referência: [KinD — troubleshooting e retenção do node em falhas](https://kind.sigs.k8s.io/docs/user/known-issues/#troubleshooting-kind).
