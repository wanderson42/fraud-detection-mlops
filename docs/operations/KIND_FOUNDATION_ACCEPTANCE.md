# Fundação KinD/Helm — aceite nativo de 2026-10-20

PostgreSQL e RustFS estão prontos no cluster dedicado `fraud-platform`.
O ensaio do autor confirmou leitura dos marcadores SQL e S3 após recriar os
dois pods, mantendo os mesmos PVCs e PVs. Este é o estado atual do primeiro
incremento; as notas anteriores de preparação e investigação são históricas.

## O que foi demonstrado

| Verificação | Resultado observado |
| --- | --- |
| Regressão de fundação e diagnóstico | 45 testes aprovados em 1,36 s; Ruff check/format aprovados |
| Instalação Helm | `foundation_ready`; dois pods Running/Ready e dois PVCs Bound |
| Recriação dos pods | Novos UIDs para PostgreSQL e RustFS |
| Identidade dos volumes | Mesmos UIDs de PVC e nomes de PV antes/depois |
| PostgreSQL | Marcador SQL relido corretamente |
| RustFS | Marcador S3 relido corretamente |
| Identidade de software | Imagens por digest preservadas no ensaio |

Relatório nativo:
`data/storage/kind-platform/foundation-fea8f802c01346d1862c82881c312243.json`.
O [registro versionado](../../references/evidence/kind_foundation_author_validation_2026-10-20.json)
contém as identidades antes/depois e delimita a origem da evidência: saídas
fornecidas pelo autor, revisadas na conversa. O recibo nativo não foi copiado
nem seu hash inventado; credenciais e kubeconfig ficam fora da evidência.

## Bootstrap: diagnóstico e correção no host

O bootstrap inicial falhou antes de implantar aplicações. Os logs retidos
mostraram que containerd não conseguiu criar o monitor CNI por esgotamento de
watchers inotify. O plugin CRI ficou indisponível, o kubelet falhou e a API
recusou conexões. Os limites eram 128 instâncias e 65.536 watches.

O autor aplicou 512 instâncias e 524.288 watches no host. Após remover somente
o node da tentativa falha, conferindo sua identidade no diagnóstico, o cluster
foi criado com sucesso. Os clusters de energia não integraram essa operação.
Não foi necessário alterar imagem, API kubeadm ou permissões RBAC para obter
o aceite. A documentação oficial do KinD descreve esse esgotamento e os mesmos
[valores de referência](https://kind.sigs.k8s.io/docs/user/known-issues/#pod-errors-due-to-too-many-open-files).

Antes de criar novos clusters no ambiente Linux deste laboratório, conferir:

```bash
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
```

O ajuste feito com `sysctl -w` é temporário. Para mantê-lo após reboot, registrar
uma vez as linhas abaixo em `/etc/sysctl.conf`, editando o arquivo com
`sudoedit /etc/sysctl.conf` e conciliando eventuais definições já existentes:

```ini
fs.inotify.max_user_instances = 512
fs.inotify.max_user_watches = 524288
```

Preservar valores maiores já necessários a outros projetos. Esses limites são
compartilhados no host; não são limites privados do namespace Kubernetes.
Depois da edição, aplicar os valores acordados com `sudo sysctl -w` e conferir
novamente. A configuração persistente e um reboot posterior ainda não foram
confirmados pelo autor. O pacote documental não escreve em `/etc` nem executa sudo.

## Reproduzir e interpretar

Em uma instalação nova, usar a sequência documentada em
[KIND_FOUNDATION.md](KIND_FOUNDATION.md): validar → inicializar → criar → subir.
Com a fundação já pronta:

```bash
poetry run python -m scripts.platform.kind_foundation status
poetry run python -m scripts.platform.kind_foundation rehearse
```

`rehearse` grava novos marcadores e recria os dois pods dedicados; não é uma
consulta somente leitura. O ensaio já aprovado não precisa ser repetido para
fechar a documentação. PVC local do KinD sobrevive à recriação do pod, mas pode
ser perdido com a exclusão do cluster/node. O resultado não comprova backup,
restore, recuperação após perda do host ou alta disponibilidade.

## Próximo incremento

Integrar MLflow aos serviços PostgreSQL/RustFS deste cluster, com identidade
e roteamento explícitos, e validar publicação e recarga independente pelo proxy.
Preservar as fontes e evidências Compose/históricas até aceitar o destino.

O marco amplo de fluxo completo em KinD/Helm continua em andamento: objetos
Bronze/Silver/Gold, jobs, estado/replay, serving e observabilidade ainda exigem
seus incrementos e aceites. Não houve migração primária, refit ou promoção de
modelo neste marco. A simplificação vem depois; Terraform permanece o desfecho.
