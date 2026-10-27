# Histórico importado — registro arquivístico

A transferência dos experimentos antigos para o MLflow do KinD foi concluída na
etapa de construção. A rodada canônica passou a registrar seus próprios candidatos,
Inputs, métricas e pesos; sua operação usa `fraud-detector`, não o Registry
arquivístico `fraud-hgb-reference`.

O importador `scripts/platform/kind_history.py`, o pacote `scripts/platform/history`
e seus testes exclusivos foram retirados após a revisão de dependências de todo
o checkout. Não são consumidores do fluxo operacional atual nem da CI de entrega.
Os dados importados, runs, modelos, aliases, planos e recibos existentes continuam
preservados. Nenhum comando deste fechamento exclui objetos do MLflow ou dados.

Para consultar o produto atual, use `make platform-mlflow` e abra a rodada
`fraud-operational-portfolio-v1`, seus Inputs e o Registry `fraud-detector`.
[Operação do produto](PRODUCT_OPERATIONS.md) · [Atualização governada](MODEL_UPDATES.md).

A implementação e o procedimento de transferência antigos continuam disponíveis
no [commit anterior à retirada](https://github.com/wanderson42/fraud-detection-mlops/tree/740ca24bcbd17c57465ae9f654f9dbbfefd47d30).
Qualquer reprodução arquivística deve usar um checkout isolado dessa revisão e
seus próprios recibos. A documentação corrente deixa de recomendar uma nova
importação histórica como requisito para instalar ou demonstrar o produto.

O [balanço da simplificação](CODEBASE_SIMPLIFICATION.md) identifica as remoções,
as dependências mantidas e as consolidações ainda necessárias.
