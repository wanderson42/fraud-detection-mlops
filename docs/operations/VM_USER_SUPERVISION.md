# Supervisão do observador e acesso Grafana na VM

## Aceite nativo informado pelo autor — 2026-10-29

Instalação e check da sessão aprovados; ambos os serviços ficaram ativos com
Grafana HTTP saudável. O ensaio `vm_user_service_recovery_verified` confirmou
mudança de PID e aumento de NRestarts para cada unidade, seguida de readback da
mesma sessão, sem novas inferências. O check posterior manteve os PIDs e hashes.
O [acompanhamento](VM_REPRODUCTION_PROGRESS.md) registra a origem e os limites.

O primeiro preflight encontrou apenas TIME-WAIT na porta 13001, sem listener.
O probe foi corrigido para reutilização de endereço compatível com port-forward,
preservando recusa de listener ativo. A revisão teve 18 testes na preparação.
As unidades não precisaram ser reinstaladas por essa mudança.

`logout_persistence_validated=false` e `reboot_validated=false` foram mantidos nas
saídas. Não há comprovação de fechamento de todos os logins/túneis na saída
recebida. CI e merge da supervisão continuam pendentes nesta revisão.

Escopo: dois serviços systemd de usuário no host da VM; nenhum Deployment,
PVC, modelo, política, imagem, inferência ou recibo aceito é modificado.
O receiver, Prometheus e Grafana continuam no KinD. O túnel SSH é outro acesso,
executado no Alienware. Não são introduzidos outro microsserviço ou orquestrador.

## Serviços

| Unidade | Processo e recuperação |
| --- | --- |
| fraud-session-observer.service | Observador existente, vinculado ao ID instalado; lock original impede dois observadores. Reinício após saída inesperada; systemd encerra os descendentes no mesmo cgroup. |
| fraud-grafana-access.service | Comando foreground de port-forward existente, com probe HTTP no loopback 13001. Três falhas após readiness ou timeout de início encerram o processo para recuperação pelo systemd. |

Reinício é configurado em 10 segundos. As unidades usam a .venv do checkout,
PATH capturado da instalação, umask 0077 e journal. Não contêm credenciais.
Exigem checkout com caminho absoluto sem espaços/backslashes, sessão previamente
instalada e ferramentas presentes no PATH. O instalador recusa unidades com
conteúdo divergente. Não ativa unidades nem altera o Linger.

## Preparar na VM

Aplicar o pacote numa branch, executar os testes e commitar. Depois:

```bash
poetry run python -m scripts.platform.host_supervision install \
  --session-id repro-arrival-200-v1
```

A instalação cria apenas as duas unidades em ~/.config/systemd/user e faz
user daemon-reload. Não use sudo nesse comando nem nos systemctl --user abaixo.

Para manter o user manager após logout e iniciá-lo no boot:

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

Linger=yes é configuração; não comprova sobrevivência após logout ou reboot.
O cluster/Docker precisa estar disponível para as unidades obterem saúde. Elas
não criam cluster, não fazem deploy e não iniciam Jobs de treino ou produtor.

## Trocar os processos manuais

1. Encerrar o watch antigo com Ctrl+C no terminal de origem. Se ele não estiver
   acessível, identificar sua identidade antes de enviar qualquer sinal; não usar
   pkill genérico, não apagar watch.lock.
2. Encerrar o acesso antigo com o controlador que possui seu recibo:

```bash
make access-stop ACCESS_SERVICE=grafana
poetry run python -m scripts.platform.host_supervision preflight \
  --session-id repro-arrival-200-v1
```

O preflight não mata processos: exige lock livre, port 13001 livre e ausência do
encaminhamento manual identificado. Depois:

```bash
systemctl --user enable --now fraud-session-observer.service fraud-grafana-access.service
poetry run python -m scripts.platform.host_supervision check \
  --session-id repro-arrival-200-v1
```

O check aguarda a saúde dos serviços, lê a projeção, Prometheus e proxy Grafana,
e compara métricas da mesma sessão e hashes de fontes antes/depois. Não republica
projeções, não executa inferências e não reescreve recibos. O observador é quem
publica os agregados durante a leitura. Uma sessão concluída produz contagens
constantes; a idade da coleta deve continuar atualizada.

## Operação

```bash
poetry run python -m scripts.platform.host_supervision status
systemctl --user status fraud-session-observer.service fraud-grafana-access.service --no-pager
journalctl --user -u fraud-session-observer.service -u fraud-grafana-access.service -n 60 --no-pager
systemctl --user stop fraud-session-observer.service fraud-grafana-access.service
systemctl --user start fraud-session-observer.service fraud-grafana-access.service
```

Enquanto essas unidades estiverem ativas, usar systemctl para operar os dois
processos. Não iniciar outro make session-monitor-watch ou make access-start
ACCESS_SERVICE=grafana. O status do controlador antigo mostra seu recibo antigo,
não o estado das novas unidades. Parar o observador faz a fonte expirar em até
30 segundos; os pods e a sessão permanecem.

Antes de manutenção Helm ou rehearsals que exigem watch.lock livre, parar o
observador supervisionado. Antes de mudar para branch que não contém o novo
módulo ou substituir a .venv, parar as duas unidades. Depois conferir e reiniciar.

## Ensaio de recuperação automática

Somente após o check inicial:

```bash
poetry run python -m scripts.platform.host_supervision rehearse \
  --session-id repro-arrival-200-v1
```

O ensaio termina o MainPID de cada unidade via systemctl kill, uma por vez.
Exige outro MainPID, incremento de NRestarts e readback da mesma sessão após a
recuperação. Confere contagens e hashes de fontes antes/depois. Pode haver uma
breve interrupção da coleta/acesso. Não encerra pods nem SSH e não repete scoring.
Aceite de logout, reboot e recuperação do cluster precisa de ensaio separado.

## Acesso pelo Alienware

O Grafana fica na VM em 127.0.0.1:13001, sem exposição externa. O SSH continua
necessário. Se o túnel atual está funcionando, preservá-lo. Para abrir outro,
com a porta 13005 livre, no Alienware:

```bash
ssh -f -N -p 2222 -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:13005:127.0.0.1:13001 wanderson@127.0.0.1
```

-f deixa o túnel em segundo plano depois da autenticação. Keepalive detecta
conexão morta; não é reconexão automática. O túnel não é supervisionado por esta
entrega. No navegador: http://127.0.0.1:13005. A senha do Grafana permanece própria
da VM; nunca colocá-la em unidades, argumentos ou logs.

## Retornar ao modo manual

```bash
systemctl --user disable --now fraud-session-observer.service fraud-grafana-access.service
make access-start ACCESS_SERVICE=grafana
make session-monitor-watch SESSION_MONITOR_ID=repro-arrival-200-v1
```

As unidades desabilitadas podem permanecer para inspeção. Não desativar Linger
indiscriminadamente se outros serviços de usuário passaram a depender dele.
A implantação da sessão e os recibos não precisam de rollback nesta entrega.

## Verificação local e limites

Testes stdlib em Python 3.14 cobrem child exit, probes HTTP reais de loopback,
falhas transitórias/permanentes, cleanup, recusa de unidade divergente, instalação
sem ativação, lock ocupado e critérios de recuperação. systemd-analyze verify
aprova a sintaxe das unidades geradas. Não houve teste de user manager real
neste ambiente: aceite de instalação, reinício automático e logout ficam na VM.
