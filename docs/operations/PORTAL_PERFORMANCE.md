# Desempenho e transparência do modelo no portal

A página **Desempenho do modelo** responde por que vale priorizar investigações
com o ranking e qual carga de trabalho decorre da política escolhida. As telas
de casos permanecem orientadas ao analista. A avaliação serve também à equipe
de ML e ao gestor de risco, sem concentrar toda a informação na visão geral.

## Contrato independente do algoritmo

A interface lê JSON versionado. Não importa estimadores, não verifica tipos de
HGB e não tem métricas ou nomes de modelos embutidos. Cada avaliação identifica
Registry/versão/run, pesos, release, dataset, período, relógio dos rótulos e commit.
Os candidatos são os presentes no relatório MLflow do modelo do snapshot.
O nome do algoritmo é informação do registro, não uma chave para escolher a UI.

O contrato de score atual é binário de ranking em [0,1]; não pressupõe calibração.
O adaptador de avaliação aceita um vetor de scores ou a saída de duas colunas
[classe 0, classe 1] dos produtores existentes. Algoritmos compatíveis com esse
contrato reutilizam a página. Outro formato de saída exige um adaptador explícito,
sem alterar a interpretação do resultado silenciosamente. O carregamento seguro
continua usando o contrato de persistência já adotado pelo projeto.

**Alvo binário não é sentença sobre a transação.** O modelo ordena sinais; a
política aplica capacidade e limiar; a investigação combina contexto e evidência.
A decisão sobre pagamentos pertence ao sistema integrador e às suas regras de
risco. O feedback tem disponibilidade posterior própria. Não há comparação medida
com desempenho humano nem estimativa de economia financeira nesta avaliação.
Esses benefícios exigem uma avaliação operacional com custos, tempo e resultados.

## O que cada seção demonstra

| Seção | Evidência | Unidade/interpretação |
| --- | --- | --- |
| Indicadores | AP, precisão diária @100, ROC-AUC, prevalência | Validação temporal completa; não os 200 casos da consulta |
| Comparação | Candidatos no mesmo dataset e métricas conferidas | Modelo do snapshot explicitamente marcado |
| Curvas | Precision–Recall e ROC | Até 250 pontos para desenho; métricas calculadas com todas as previsões |
| Política | Precisão, recall e falsos positivos com primeira passagem, limiar e orçamento | Cliente/dia; simulação offline na validação com o redutor original |
| Matriz no limiar isolado | TP, FP, FN, TN, precisão e recall | Transação; sem deduplicação ou orçamento, distinta da fila |
| Rastreabilidade | Dataset, janela, rótulos, run, commit, pesos e release | Vínculo ao mesmo snapshot da investigação |

AP não é acurácia nem proporção de fraudes capturadas. A precisão @100 é a média
por dia entre os até 100 clientes de maior score; não equivale à fila online por
primeiro cruzamento de limiar. ROC-AUC complementa a visão de precisão/carga.
Prever tudo como legítimo ilustra por que acurácia isolada pode enganar sob
forte desbalanceamento. Recall e precisão são apresentados com suas unidades.
A janela foi usada para selecionar candidatos; exibi-la não cria confirmação
independente em holdout, nem significância estatística ou validade econômica.

## Preparação verificável

O produtor confere a versão MLflow do snapshot e o alias ativo durante a captura,
os Inputs nativos, a identidade do conteúdo da validação e os hashes dos pesos.
Recalcula AP, ROC-AUC e precisão @100 para todos os candidatos e exige paridade
com o relatório aceito. Só então gera curvas e diagnósticos de limiar/política.
A política reutiliza `decide_customer_day_alert`; os rótulos entram depois da
seleção da fila. Não há segundo algoritmo de triagem na interface.

O leitor abre somente o manifest Gold autenticado e as partições de validação.
Não pontua treino ou test, não recompõe features e não executa fit. O instante
histórico de maturidade é o último LABEL_AVAILABLE_AT declarado e conferido na
validação. O treino deve ter feedback disponível antes de a validação começar.
A política de avaliação e os candidatos existentes são preservados.

A avaliação é um artefato separado do snapshot de casos. O recibo guarda um digest
externo; a leitura recusa outro modelo, release, versão Registry ou snapshot.
Repetir a mesma preparação conserva bytes e instante de publicação. Depois de
publicada, a página e a verificação offline não acessam modelos, MLflow ou cluster.

## Comandos

Revise e commite este recorte antes de preparar; o comando confere os módulos
recursivamente contra HEAD. Mantenha backend/Registry saudáveis e sem escritores.
Não execute portal-prepare de novo para o snapshot atual.

```bash
make portal-performance-prepare PRODUCT_NO_OTHER_WRITERS=1 &&
make portal-performance-verify &&
make portal-open
```

Encerre o portal anterior com Ctrl+C antes de reabrir. A nova opção aparece no
menu. A origem padrão é a restauração Gold da rodada `portfolio-v1`; essa é uma
seleção de dataset, não de algoritmo. Para outra origem, use
`PORTAL_EVALUATION_ROUND=<rodada>` ou passe o diretório de bundle Gold restaurado
explicitamente ao controlador com `--gold-directory`. Ele precisa coincidir com
o dataset declarado nos Inputs da versão avaliada; caminhos externos à área
gerenciada são recusados. Use `PORTAL_PERFORMANCE_ID=<novo-id>` para outro relatório.

Em outro terminal:

```bash
make portal-smoke
make portal-performance-verify
```

O primeiro confere HTTP e casos; o segundo confere o vínculo da avaliação offline.
O aceite visual abrange os quatro indicadores, comparação, curvas, política e
rastreabilidade do mesmo modelo mostrado na consulta de casos.

## Uma nova promoção

A UI não acompanha o alias e substitui casos históricos silenciosamente. Depois
de uma promoção governada, aceite um novo fluxo HTTP do modelo ativo, prepare
outro PORTAL_ID e gere sua avaliação. Um relatório antigo não pode enriquecer o
novo snapshot. O anterior continua como evidência histórica identificada.
Nenhuma dessas operações altera o alias ou o tráfego.

SHAP é uma capacidade opcional, com método e unidade próprios. O produtor atual
usa TreeExplainer para o HGB não transformado. Isso não torna o portal dependente
do HGB: uma fila sem explicações pode ser preparada explicitamente com
`PORTAL_WITHOUT_EXPLANATIONS=1`; o caso informa que não há explicação preparada.
Uma saída explicativa em unidade ainda não suportada não é desenhada como se
fosse log-odds. Novos explicadores devem cumprir um contrato verificado de pesos,
request, unidade e reconstrução antes de fornecer dados à UI.

## Persistência e aceite

- `data/reports/fraud-portal/performance/<id>/performance.json`: agregados, curvas e proveniência.
- `data/reports/fraud-portal/performance/<id>/manifest.json`: hash e publicação.
- `data/storage/kind-platform/portal/<portal-id>-performance.json`: vínculo pinado ao snapshot.

Arquivos originais de casos/SHAP são preservados. O recibo é privado e contém
caminho absoluto; não o publique. A evidência desta entrega é de preparação:
testes locais e navegador com fixture. A avaliação dos dados nativos e a revisão
dessa página no ambiente do autor ainda precisam do aceite correspondente.
