# Portal de investigação de fraude

O portal transforma as decisões identificadas da API em uma jornada de trabalho:
localizar um alerta, entender o cliente e a transação, examinar as features e
consultar os fatores que contribuíram para o score. Streamlit organiza a interface;
Plotly apresenta as distribuições e a decomposição SHAP.

## Jornada e responsabilidades

| Usuário | Entrega e finalidade |
| --- | --- |
| Sistema transacional | API de pontuação identificada, integrada ao fluxo causal |
| Analista de fraude | Fila priorizada, contexto, feedback posterior e explicação por caso |
| Gestor de risco | Visão da janela: decisões, alertas, cobertura de feedback e explicações |
| Operação | Grafana: disponibilidade, erros, atrasos, qualidade de dados/features |
| Equipe de ML | MLflow: datasets, candidatos, qualidade, Registry e implantação governada |

O gestor vê indicadores da janela preparada. Eles não são estimativas de fraude
financeira evitada, produtividade dos analistas ou qualidade global do modelo.

## Três telas conectadas

1. **Visão geral**: volume de decisões, alertas da política, feedback e cobertura
   SHAP; distribuição de scores e resultados da triagem; acesso aos alertas prioritários.
2. **Fila de investigação**: cliente, transação, dia, status e score mínimo,
   ordenação com alertas primeiro e score decrescente, paginação de 25 linhas.
   O filtro de score altera a visualização; não altera o limiar da política.
3. **Investigar caso**: valor sem moeda inventada, cliente/terminal, horário,
   score e motivo da triagem, limiar e orçamento, feedback disponível,
   waterfall SHAP, contexto na janela, 19 features e identidade do modelo/release.
   O download JSON permite levar o caso para uma revisão técnica.

SHAP laranja aumenta o score; azul o reduz; essas cores descrevem direção, não
fraude confirmada. O gráfico conserva a soma: base + maiores contribuições +
restantes = margem em log-odds; a sigmoide reconstrói o score armazenado.
A explicação descreve o modelo, não causalidade. O score é ranking não calibrado.
O feedback posterior é exibido separadamente e não participa da explicação.

## Dados e identidade

A preparação usa **todas as decisões pontuadas do fluxo HTTP aceito**, incluindo
as que não geraram alerta. A origem atual é `champion-http-v1`; não é toda a base
Bronze nem todas as 143.945 decisões do replay anterior. O limite de segurança
é 10.000 decisões: acima dele a exportação para para exigir revisão, sem truncar.
As cinco explicações aceitas em `champion-shap-v1` enriquecem apenas seus cinco
casos. Os demais apresentam um estado explícito de explicação indisponível.

O exportador confere o recibo de aceitação, os quatro arquivos da origem,
requisição/resposta por hash, features, modelo/release e o digest externo do
relatório SHAP. Confere também API/Registry ativos durante a captura. A consulta
SQLite é somente leitura e a publicação usa diretório temporário e rename.
Repetir a preparação com o mesmo conteúdo preserva bytes e data de captura.
Fonte divergente exige outro ID e revisão; não sobrescreve um snapshot aceito.

O portal lê um **snapshot verificado**, com período e instante da captura visíveis.
Atualizar a página não busca transações novas. O modelo é o ativo na captura;
uma mudança posterior do champion exige uma nova preparação. Consultar o bundle
não carrega pesos, acessa credenciais, recompõe features ou recalcula SHAP.

## Instalação e abertura

Revise, teste e commite a implementação antes da preparação: o exportador exige
que seus módulos e locks correspondam ao HEAD. Permaneça na branch de trabalho.

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/fraud_portal tests/case_explanations tests/product_flow \
  tests/serving_recovery tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py tests/infra/test_platform_entrypoints.py &&
.venv/bin/python -m tox -e portal-ui
```

Com o backend operacional no KinD e sem escritores no fluxo aceito:

```bash
make portal-install &&
make portal-prepare PRODUCT_NO_OTHER_WRITERS=1 \
  PRODUCT_EXPLANATION_SHA256=1f780b2d7eb9de7a553435f3ccd69ede37eca735cbdddae6dadbb01981e0d91c &&
make portal-verify &&
make portal-open
```

Abra **http://127.0.0.1:18501**. A preparação usa o relatório SHAP já aceito e
preserva a fonte; não executa replay, treino ou deploy. `portal-install` cria
`.portal-venv` com Streamlit/Plotly e dependências fixadas por hashes, separada do
ambiente científico. O frontend é um cliente local dos dados produzidos pelo
backend no KinD; neste recorte ele não é instalado por Helm.

Em outro terminal, mantendo a interface aberta:

```bash
make portal-smoke
```

O smoke confere a saúde HTTP do Streamlit e o snapshot local pinado. A conferência
visual das três telas complementa esse teste; a resposta HTTP sozinha não testa
uma sessão de navegador. `Ctrl+C` encerra somente a interface. Para trocar a
porta, use `PORTAL_PORT=18502` tanto em `portal-open` quanto em `portal-smoke`.
O snapshot já preparado pode ser aberto e verificado sem o cluster disponível.

Arquivos gerados:

| Local | Papel |
| --- | --- |
| `data/reports/fraud-portal/<portal-id>/snapshot.json` | Casos e explicações reconciliados |
| `data/reports/fraud-portal/<portal-id>/manifest.json` | Hash, origem, modelo e instante da captura |
| `data/storage/kind-platform/portal/<portal-id>.json` | Caminho gerenciado e digest externo para acesso |

Preserve os três arquivos juntos sob os controles locais de acesso. O recibo
contém caminho absoluto: outro checkout exige preparação própria. A verificação
por hash detecta alteração de conteúdo; não substitui assinatura nem gestão de identidade.

## Operação, segurança e evolução

A interface oferece consulta e exportação; decisões de aprovar, bloquear, atribuir
ou encerrar investigação exigem um serviço de casos com persistência, autorização
e trilha de auditoria. Não há botões que simulem essas operações.
O processo atende somente em loopback e não herda variáveis AWS/DB do controlador.
Os casos e downloads devem ser tratados como dados restritos; não publique o
bundle nem encaminhe a porta externamente como se fosse um serviço autenticado.
Uma entrega compartilhada acrescentará autenticação, autorização por função,
proteção dos dados e auditoria antes de disponibilizar ações ao cliente.

A evolução do produto deve ser orientada à tarefa: seleção/atribuição persistente,
notas e resolução, feedback auditável e atualização incremental são próximos
contratos possíveis. A explicação por caso permanece fora do caminho síncrono de
pontuação. Grafana e MLflow mantêm suas responsabilidades especializadas.

## Aceite deste recorte

Implementação e testes locais preparados; falta executar a exportação com os
recibos nativos e conferir a interface na máquina do autor. O aceite deve mostrar
contagens completas do fluxo, cobertura SHAP correta, navegação/filtros,
reconstrução de um caso explicado e estado de caso sem explicação. Pesos,
release, champion e arquivos de origem devem permanecer iguais.
A promoção recorrente de sucessor com pesos diferentes continua aguardando
candidato qualificado, independentemente do aceite do portal.
