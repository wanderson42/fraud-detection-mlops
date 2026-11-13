# Criar uma VM local: automação mínima

## Aceite do autor — 2026-11-12

A prévia e a criação desligada foram executadas no Alienware com VirtualBox
7.2.22r175245. A VM `fraud-bootstrap-v1`, UUID
`04471b53-42c7-4792-a7b2-fb6d085581a2`, recebeu 6 vCPUs, 8192 MiB e disco de
80 GiB; SSH em `127.0.0.1:45871`. O checksum da ISO foi comparado à publicação
Ubuntu. Não foi fornecida validação da assinatura do arquivo de checksums.

O perfil inicial `Ubuntu24_64` descrevia Ubuntu 24.10. O autor corrigiu a VM
existente para `Ubuntu24_LTS_64` e verificou o readback; o script foi corrigido
em `39d97e3`, com 13 testes aprovados. A criação inicial veio de `21bbe96`.
Não foi criada outra VM para repetir o ensaio com o script corrigido.

Ubuntu 24.04.5/OpenSSH foram instalados manualmente. A raiz ext4 foi expandida
manualmente de 39 para 77 GiB após conferir o espaço no LVM. Autenticação por
chave pública passou em BatchMode; nenhuma chave ou senha integra esta evidência.

O guest usou `e26ee204053e053421b2cc5eb36ac2f63c5341e8`, HEAD destacado. As fases
`packages`, `docker`, `python`, `tools`, `inotify` e `check` foram executadas.
Python 3.14.4, Poetry 2.4.3, uv 0.13.8, Engine 29.1.3, Compose 5.7.1,
Buildx 0.39.0, KinD 0.33.0, kubectl 1.35.9 e Helm 4.3.1 foram reportados.
Inotify ficou em 512/524288. O acesso ao Docker passou após reconectar o SSH.

O shell inicialmente encontrou Poetry 1.8.2 do sistema. O autor resolveu o
PATH com `pipx ensurepath` e reconexão, confirmando Poetry 2.4.3 e uv numa nova
sessão. A correção posterior do setup fixa o uso do Poetry do usuário e acrescenta
um bloco idempotente a `~/.profile`, sem remover o Poetry do sistema. Essa
persistência automática tem testes offline, não nova execução nativa do setup.
O script suporta o login padrão Ubuntu que lê `.profile`; shells customizados
ou `.bash_profile` que ignore esse arquivo precisam de configuração própria.

O incremento está aceito no escopo de criação, instalação manual e setup.
A publicação/CI/merge da revisão final ainda precisam ser confirmados. Não
repetir treino, sessão ou bootstrap completo apenas para registrar o aceite.
Novo cluster, produto nesta VM, reboot, HA, backup/restore e AWS não foram
validados neste ensaio. Consulte a
[evidência](../../references/evidence/vm_provision_bootstrap_author_validation_2026-11-12.json).


Preparação em 2026-11-12, sobre a main integrada `e26ee20` (PR #26).
Executar **no cliente Linux amd64**, com Python 3.11+ e VirtualBox 7.2.x.
Nenhum novo provider Terraform, serviço ou dependência Python é necessário.

## Decisão e limites

O Terraform já aceito administra namespace/Secret/release da fundação isolada;
este auxiliar usa a CLI do VirtualBox para criar uma VM nova e **desligada**.
Não instala Ubuntu, não inicia VM e não cria cluster. Não é gerenciamento de
ciclo de vida por Terraform, nem oferece update/destroy/reconciliação contínua.
VMs existentes, dados e state Terraform permanecem sob seus responsáveis atuais.

O provider terra-farm/virtualbox se declara experimental e procura mantenedores;
não foi adotado neste incremento. A ISO do autor foi reconhecida como Ubuntu
24.04.5, mas `unattended detect` retornou `NS_ERROR_NOT_IMPLEMENTED`, apesar de
`IsInstallSupported="on"`. Não inferir instalação automática bem-sucedida.
A instalação do SO permanece manual; não alteramos o hipervisor para contornar
essa limitação. Referências: [provider](https://github.com/terra-farm/terraform-provider-virtualbox)
e [CLI oficial](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html).

## Prévia, sem criar arquivos ou VM

Obtenha o SHA256 esperado na publicação oficial da ISO Ubuntu (e confira a
assinatura do arquivo de checksums se necessário). Calcular o hash do próprio
arquivo e declará-lo esperado, sem uma referência confiável, não comprova origem.
A publicação atual está em https://releases.ubuntu.com/24.04/; versões anteriores
podem estar em https://old-releases.ubuntu.com/releases/.

```bash
cd ~/Documentos/fraud-detection-mlops
read -rp 'SHA256 oficial da ISO Ubuntu: ' VM_ISO_SHA256
export VM_ISO_SHA256
make vm-create-plan \
  VM_NEW_NAME=fraud-bootstrap-v1 \
  VM_NEW_BASE="$HOME/VirtualBox VMs" \
  VM_ISO="$HOME/Downloads/ubuntu-24.04.5-live-server-amd64.iso"
```

A pasta base deve existir. A prévia verifica o checksum completo, nomes
registrados, diretório de destino, CPU, espaço para o disco totalmente expandido
mais 2 GiB e porta SSH. Reserva conservadoramente portas TCP configuradas em
outras VMs, mesmo desligadas, e recusa uma porta explicitamente ocupada.
`VM_NEW_SSH_PORT=0` escolhe uma porta livre e informa a escolhida. A porta não
fica reservada após a consulta; confira novamente antes de ligar a VM.

Os defaults são 6 vCPUs, 8192 MiB e disco VDI dinâmico de 80 GiB. Ajuste com
`VM_NEW_CPUS`, `VM_NEW_MEMORY_MIB`, `VM_NEW_DISK_GIB` e `VM_NEW_SSH_PORT`.
Valores menores não herdam o aceite funcional obtido no ambiente de referência.
`boot_budget_ready` é apenas a comparação da memória disponível com a RAM da VM
mais 2 GiB para o host; não é benchmark nem autorização automática para iniciar.
A prévia imprime os argumentos exatos planejados. Não contém senhas ou chaves.

## Criar desligada e instalar manualmente

Após revisar, execute `make vm-create` com **os mesmos parâmetros**. O comando
revalida os conflitos; se usar porta automática, pode selecionar outra porta.
Para fixar a porta revisada, passe `VM_NEW_SSH_PORT=<porta>` em ambas as chamadas.
A saída informa nome, UUID, caminho, porta e `vm_created_powered_off`.

Somente quando houver RAM disponível, abra essa VM no VirtualBox e faça a
instalação manual do Ubuntu com OpenSSH. A ISO já estará conectada ao DVD.
Depois da instalação, remova a ISO pela interface e confira o boot pelo disco.
A regra SSH liga somente `127.0.0.1:<porta>` do cliente à porta 22 do guest;
ela não publica os serviços MLOps. Continue no
[CLEAN_INSTALL_RUNBOOK.md](CLEAN_INSTALL_RUNBOOK.md), usando o bootstrap existente
`vm_setup.sh` e as entradas existentes da plataforma. Senhas, chaves e identidade
Git são configuradas pelo usuário; este auxiliar não as gera ou copia.

## Falha parcial e concorrência

A criação utiliza UUID próprio para modificar somente a máquina recém-criada.
Uma trava na pasta base serializa chamadas deste auxiliar; não bloqueia a GUI
ou outros clientes VBoxManage. Não execute criação concorrente por outro meio.
Em falha parcial, preserva VM/disco e informa UUID/diretório. Inspecione-os no
VirtualBox antes de qualquer decisão de limpeza. Nova chamada recusa nomes e
pastas existentes; não apaga, adota ou reinicializa recursos para tentar novamente.
Se alguma VM estiver inacessível ou o inventário não puder ser interpretado,
a verificação recusa avançar em vez de assumir que não há conflitos.

## Aceite

A preparação teve testes offline de conflitos, checksum, limites, UUID e
preservação após falha parcial. A execução nativa posterior está registrada
no início deste documento. O produto completo nesta VM permanece fora do
aceite; não repetir treino, sessão ou backup para fechar criação e setup.
