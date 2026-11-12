# Criar uma VM local: automação mínima

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

Testes offline cobrem prévia sem mutações, conflitos, checksum, limites,
UUID próprio e preservação após falha parcial. Não comprovam execução real da
CLI 7.2.22, instalação do guest ou nova reprodução do produto. O próximo gate
nativo é a prévia no cliente e criação desligada, seguida de inspeção da
configuração e instalação manual quando houver capacidade. Não repetir treino,
sessão, backup ou ensaios aceitos para validar apenas a criação da máquina.
