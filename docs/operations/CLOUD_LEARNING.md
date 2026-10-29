# Aprendizado de nuvem e POC temporária

Decisão de escopo em **2026-10-28**: o produto local continua sendo a entrega
principal. Conta de aprendizagem e ensaio em nuvem são opcionais. Nenhum serviço,
conta, upgrade ou recurso foi criado por esta atualização documental.

## Objetivo

Aprender console, identidades, rede, containers, observabilidade, cobrança e
remoção de recursos. Posteriormente verificar uma implantação mínima do projeto
com prazo e orçamento definidos. Não assumir hospedagem permanente nem copiar
a infraestrutura completa local para vários serviços gerenciados de uma vez.

## Condições AWS verificadas nesta data

As condições dependem da elegibilidade e do fluxo de cadastro. A FAQ atual
informa plano Free para novos clientes, com US$ 100 iniciais e possibilidade de
até US$ 100 adicionais. O plano dura até seis meses ou até esgotar os créditos,
o que vier primeiro; há restrições de serviços. Não é uma conta experimental
ilimitada e permanente.

Segundo a FAQ, muitos novos clientes podem cadastrar-se sem meio de pagamento;
a AWS pode solicitar dados adicionais ou meio de pagamento para verificar
identidade. A documentação de Billing ainda descreve outros fluxos de cadastro.
Portanto, não prometer cadastro sem cartão em todos os casos: conferir o fluxo
e o plano efetivamente apresentados ao usuário.

A FAQ informa que clientes com conta atual ou anterior não são elegíveis ao
Free plan/créditos de novo cliente. Não criar outra identidade para contornar
esse requisito. Confirmar elegibilidade antes de planejar o ensaio.

No Free plan, os créditos cobrem o uso elegível e a continuidade após seu fim
exige mudança de plano. O encerramento do período suspende/fecha a conta e
interrompe o acesso aos recursos; a retenção adicional de dados por 90 dias não
substitui exportar os resultados antes do término. No Paid plan, uso não coberto
por créditos pode gerar cobrança. Outros fluxos, como ativação de recursos
avançados/Organizations/Control Tower, podem alterar a condição do plano;
não ativá-los durante o primeiro estudo sem revisão.

Criar a conta inicia o relógio do plano gratuito. Preferir fazê-lo perto da
etapa de aprendizado efetivo, para aproveitar a janela. Manter uma conta vazia
não testa o projeto; eventual custo depende de plano, recursos e contratos,
não de estar ou não conectado ao console.

Fontes oficiais consultadas:

- [AWS Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/).
- [Choosing a plan](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html).
- [AWS Free Tier](https://aws.amazon.com/free/).

Rever condições no momento do cadastro e de cada ensaio; não congelar preços ou
ofertas como garantias do projeto.

## Etapas propostas

| Etapa | Resultado esperado |
| --- | --- |
| Explorar a conta | Confirmar plano/elegibilidade, proteger acesso, identificar região e verificar cobrança; sem provisionar a arquitetura |
| Desenhar o piloto | Escolher o menor recorte útil, serviços disponíveis no plano, duração, estimativa e rotina de remoção |
| Executar o piloto | Provisionamento deliberado, imagem/dados identificados, API verificada, evidências exportadas |
| Encerrar o piloto | Conferir recursos remanescentes e custos; preservar artefatos localmente; nenhuma dependência permanente da nuvem |

Não atribuir custo zero à arquitetura inteira por existir Free Tier. Para uma
conta paga, alertas de orçamento isolados não equivalem a um bloqueio absoluto
de gastos. Não há orçamento pago aprovado nesta etapa.

## Alternativas a avaliar, sem adoção prévia

- EC2 com implantação compacta para uma primeira portabilidade; isso não prova
  operação distribuída ou alta disponibilidade.
- ECS com serviços contínuos e tarefas finitas para gestão de containers.
- EKS somente se operar Kubernetes na AWS justificar seu custo e complexidade.
- MLflow próprio ou gerenciado, conforme compatibilidade, disponibilidade e custo.

Terraform poderá declarar a infraestrutura escolhida após a reprodução local
estável. Um executor de pipeline continua sendo decisão independente. Nenhuma
alternativa é pré-requisito para concluir o portfólio local.
