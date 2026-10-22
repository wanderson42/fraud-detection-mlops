# Operação do produto: responsabilidades e próxima consolidação

## Onde estamos

A infraestrutura KinD/Helm e a recuperação operacional foram aceitas. A rodada
canônica recompôs dados, registrou candidatos com Inputs e colocou o champion
identificado na API. O fluxo HTTP causal aceitou 200 decisões e seu feedback.
O portal consulta esses casos e cinco explicações. A avaliação completa das
67.255 transações de validação foi conciliada com MLflow e aceita funcional e
visualmente pelo autor em 2026-10-22. Esses recortes estão encerrados.

Falta consolidar uma **sessão operacional contínua e reproduzível**: produtor
liberando eventos em ritmo controlado, processamento pelo champion, fila que
acompanha o avanço, feedback posterior e observabilidade da mesma sessão.
O portal atual é um snapshot e o monitoramento KinD aceito usa uma cópia de
replay concluído. Mantê-los abertos simultaneamente não comprova atualização
contínua nem que estejam mostrando a mesma execução.

## Serviços conectados, responsabilidades distintas

| Usuário | Entrega no projeto |
| --- | --- |
| Sistema transacional | API de pontuação com contrato e modelo identificado |
| Analista de fraude | Portal: fila, contexto, triagem, explicação e feedback disponível |
| Equipe de operação | Grafana: disponibilidade, erros, atrasos e indicadores de monitoramento |
| Equipe de ML | MLflow e fluxo governado: linhagem, comparação, qualificação e implantação |
| Gestor de risco | Visão resumida da operação e da capacidade de investigação |

A tabela organiza tarefas, não cinco sistemas isolados. Pesos/release, datasets,
IDs de transação, decisões e recibos ligam as entregas. MLflow fornece linhagem e
Registry ao fluxo de implantação; não recebe transações para investigá-las. A API
pontua features identificadas; a política e o ledger persistem a triagem. Grafana
consulta métricas; o portal consulta casos e evidência. O modelo não cria o fluxo
de dados nem assume a decisão sobre pagamentos.

## Centralização operacional

Make é o ponto de entrada para o operador; os CLIs mantêm checagens, ownership,
locks e recibos. Helm descreve os workloads no cluster. Grafana observa a saúde,
sem virar um controlador de deploy. Streamlit serve o analista, sem precisar
iniciar dados ou administrar a infraestrutura.

| Família atual | Responsabilidade |
| --- | --- |
| `platform-*` | Fundação, inspeção, serviços, acessos e ensaios da plataforma |
| `round-*` / atualização governada | Dados, treinamento, qualificação e serving canônico |
| `product-*` | Eventos causais pela API, decisões persistidas e verificação |
| `portal-*` | Preparação e consulta de casos e avaliação pinados |

`make platform-api` lê o deployment ativo em `model-updates/current.json` e usa
os gates compartilhados para conferir Registry, slot, imagens e identidade HTTP.
Não escolhe o modelo por nome de algoritmo nem pelo ID da primeira rodada.
`round-api` permanece como alias de compatibilidade para esse mesmo acesso;
`ROUND_ID` não seleciona um deployment antigo nesse comando. O acesso ao serving
sintético original exige `make platform-synthetic-api` explicitamente. A ausência
ou divergência do deployment ativo interrompe o acesso, sem fallback sintético.

A identidade é conferida ao abrir e fechar o acesso. Uma promoção ou manutenção
posterior exige reabrir o comando para obter uma verificação atualizada. O acesso
não bloqueia deploys nem modifica tráfego, replicas ou aliases.

`make help` mostra os acessos e comandos cotidianos. `make help-all` mantém o
catálogo completo de instalação, ciência e ensaios. Os requisitos de baseline e
writers continuam obrigatórios para parada/subida. A [primeira simplificação](CODEBASE_SIMPLIFICATION.md)
registra as remoções e as dependências que impedem apagar diretórios inteiros.

## Quem faz os dados chegarem

Na jornada desejada, transações surgem do sistema transacional. Um produtor ou
integração as entrega ao executor causal, que monta features disponíveis naquele
instante, chama a API e persiste decisão/alerta. O analista recebe a fila resultante.
Atualizar a consulta pode buscar casos já processados; não deve fabricar ou
solicitar que novas transações aconteçam. Iniciar/pausar uma demonstração pertence
ao operador, com controles e identidade próprios.

Bronze/Silver/Gold são a cadeia de preparação e rastreabilidade dos dados. Não
significa refazer Silver/Gold completos para cada transação durante a operação.
As features online vêm do estado causal já existente; o treino e a avaliação
continuam separados do caminho de pontuação.

## O que o Handbook permite demonstrar

O acervo disponível é histórico e finito: 183 dias de transações simuladas.
Podemos reproduzi-lo em ordem temporal para simular chegada gradual, com ritmo
configurável e checkpoint, sem depender de novas coletas ou dados pagos. O atual
lote HTTP de 200 casos não é o limite do acervo. O replay anterior e esse lote
não são novos dados independentes para confirmar qualidade.

Uma sessão deve declarar origem, janela, modelo, contrato, política e ponto de
retomada. Reutilizar períodos já observados é demonstração operacional, não novo
holdout. A disponibilidade de uma janela ainda não consumida precisa ser conferida
no protocolo antes de qualquer alegação de avaliação inédita. Rótulos devem
continuar chegando no relógio definido pelo projeto, sete dias após a transação;
a ordem causal não pode ser trocada para deixar o dashboard mais convincente.

## Próximo recorte e critério de encerramento

O próximo recorte é **consolidação da demonstração operacional**, dentro do marco
de simplificação e fechamento funcional. Ele reaproveita produtor/replay causal,
API, ledger, Make, portal e observabilidade. Não exige adicionar broker, feature
store, CT, outro modelo ou novo serviço de orquestração para começar.

Critérios de aceite propostos para esse recorte:

1. Um operador inicia uma sessão declarada e vê o modelo/release efetivo.
2. Eventos chegam gradualmente e as decisões passam pela API, sem fallback local.
3. Fila e métricas acompanham a mesma sessão/checkpoint; fontes e atualização ficam visíveis.
4. Pausa/retomada preservam estado, contagens e identidades, sem duplicar alertas.
5. Feedback fica disponível no relógio correto; explicações são capacidades opcionais verificadas.
6. Um guia único distingue instalar, iniciar, acessar, pausar, retomar e parar.

A avaliação temporal aceita permanece como evidência fixa. Consulta corrente,
sessão operacional e reprocessamento demonstrativo devem ter identidades próprias.
A ativação/rollback de sucessor com pesos diferentes continua condicionada a um
candidato qualificado; não se altera o gate para produzir um evento de promoção.
Terraform permanece posterior à consolidação funcional.

## Organização do trabalho

O marco KinD/Helm e o aceite do portal foram integrados à main. A consolidação
ocorre em `feat/fraud-operations-demo`, começando pela revisão dos comandos e
pela retirada de uma transição de uso único já aplicada. A sessão de chegada
gradual e a atualização coordenada de fila/métricas seguem como próximo passo;
a reorganização dos comandos não as implementa implicitamente.
