# Acesso por SSH com portas locais livres

No **computador cliente Linux**, o novo `make vm-access-up` precisa de Python
3.10+ e OpenSSH, sem Poetry ou ambiente científico. A VM continua atendendo em
loopback. Os endpoints selecionáveis são portal, Grafana, MLflow, API e
Prometheus; banco e armazenamento não são expostos por esse comando.

Prepare autenticação SSH por chave/agent ou um alias em `~/.ssh/config` e aceite
a chave do host numa conexão interativa anterior. O comando usa `BatchMode=yes`;
não pede, armazena ou injeta senha. Acesso SSH e senha do Grafana são diferentes.
Confira primeiro com `ssh -o BatchMode=yes -p PORTA usuario@host true`.
Se você só usa senha, configure uma chave no cliente e publique sua chave pública
com `ssh-copy-id -p PORTA usuario@host`; confirme a identidade do host no terminal.
Não sobrescreva chaves existentes. Depois repita a verificação BatchMode.

Os arquivos desta entrega precisam existir **também na VM**, na revisão commitada.
Não é preciso reiniciar observador nem refazer treinamento/sessão.

```bash
make vm-access-up \
  VM_HOST=127.0.0.1 VM_USER=wanderson VM_SSH_PORT=2223 \
  VM_ROOT=/home/wanderson/projetos/fraud-detection-mlops \
  PORTAL_ID=clean-install-portal-v1
```

Esse é um exemplo do ensaio VirtualBox; substitua host, usuário, porta e caminho.
Com alias SSH, omita usuário/porta para respeitar o arquivo de configuração:

```bash
make vm-access-up VM_HOST=minha-vm VM_ROOT=/caminho/absoluto/projeto PORTAL_ID=meu-portal
make vm-access-status
make vm-access-stop
```

Por padrão abre portal/Grafana/MLflow em **portas livres escolhidas no cliente**
e informa as URLs reais em JSON. Se outro processo ocupar a porta no intervalo
entre seleção e bind do SSH, tenta novamente até cinco vezes. A prontidão exige
socket pertencente ao processo SSH criado e HTTP 200 pelo túnel. Uma porta já
ocupada por outra aplicação saudável não satisfaz o gate.

Para selecionar serviços ou uma porta local específica:

```bash
make vm-access-up VM_HOST=minha-vm VM_ROOT=/caminho/absoluto/projeto \
  PORTAL_ID=meu-portal VM_SERVICES=portal,grafana \
  VM_LOCAL_PORTS=portal=18511,grafana=13011
```

Porta explícita ocupada causa erro; não há troca silenciosa nem encerramento do
seu ocupante. Pare primeiro o perfil se alterar parâmetros. `VM_PROFILE=demo`
separa outro conjunto de túneis do mesmo checkout. Todas as URLs são ligadas
somente a `127.0.0.1`, sem abrir interfaces públicas ou portas no firewall.

A preparação remota reaproveita os controladores existentes e o Grafana ativo
pelo systemd. Se necessário, inicia acessos locais da VM para os serviços pedidos;
não faz provisioning, treinamento, replay ou alteração de deployments. Pode
executar os controles de identidade/HTTP já próprios do acesso à API. A opção
`VM_REMOTE_PORTS=portal=18521,grafana=13021` configura forwards manuais na VM;
API/MLflow escolhem porta pelo controlador e não aceitam override; Grafana
supervisionado usa 13001 e recusa outra porta. O portal deve ter snapshot preparado.

`vm-access-stop` encerra **somente os processos SSH identificados** no cliente,
conferindo PID, início, boot, argumentos e diretório. Preserva os serviços remotos,
Kubernetes e acessos do systemd. A falha parcial fecha os túneis recém-abertos;
acessos remotos preparados ficam disponíveis e identificados pelo controlador.
Não há encerramento por nome ou busca de ocupantes de portas.

Recibos/logs privados ficam em
`${XDG_STATE_HOME:-$HOME/.local/state}/fraud-vm-access/<checkout>/perfil/`.
Reboot do cliente ou perda de conexão exige reabrir túneis; não muda os dados da
VM. Um perfil ativo saudável é reutilizado; perfil parcial exige stop/up explícito.
A implementação cliente é Linux por usar identidade de processo/socket em `/proc`.
macOS/Windows sem Linux não estão qualificados.

Os testes locais exercitam sockets/processos reais com um substituto de SSH;
a autenticação e os novos comandos na VM aguardam aceite nativo. No percurso
já aceito, o autor usou túneis OpenSSH manuais e confirmou a revisão visual.

Referência de opções e encaminhamento: [manual oficial OpenSSH](https://man.openbsd.org/ssh.1).
