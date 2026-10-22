# Explicações dos casos do champion

O analista recebe a transação, seu score, a decisão de triagem e os fatores que
contribuíram para a saída do modelo. O relatório liga cada explicação ao hash dos
pesos, à release e à requisição HTTP persistida. A explicação ajuda a investigar;
a decisão operacional continua seguindo a política de triagem e seu orçamento.

## Método e vínculo com a decisão

O adaptador usa o mesmo método do diagnóstico existente
`evaluation/validation_diagnostics.py:explain_sample`: Tree SHAP do HGB, saída
`raw`, `tree_path_dependent` e checagem de aditividade. O runner antigo seleciona
amostras e casos a partir de rótulos; o adaptador recebe explicitamente os casos
persistidos, sem exigir rótulos ou selecionar por feedback. A regressão compara
as contribuições dos dois caminhos nos mesmos exemplos.

Os contadores de treinamento nos caminhos das árvores compõem a referência do
método, conforme a [documentação do TreeExplainer](https://shap.readthedocs.io/en/latest/generated/shap.TreeExplainer.html).
Não há nova coleta de background, leitura da Gold ou mudança de features.
Este recorte atende a pipeline HGB binária sem transformação; outra arquitetura
exige um adaptador próprio, sem explicar silenciosamente outra saída.

Antes de publicar, o controlador confere:

1. Recibo aceito do fluxo HTTP, hash do checkpoint completo e identidade dos eventos.
2. Casos ordenados por alerta, score decrescente e ID, sem usar o feedback na seleção.
3. Hashes da requisição e resposta iguais aos seus recibos HTTP.
4. Champion/Registry/rota/HTTP antes e depois; release local com manifest e pesos exatos.
5. Score local igual ao score HTTP gravado, com `rtol=atol=1e-12`.
6. Base + soma das 19 contribuições igual à margem raw, com `rtol=atol=1e-7`;
   sigmoide da margem igual ao score gravado, com `rtol=atol=1e-12`.
7. Bytes do input, eventos, checkpoint e aceite inalterados ao final.

A geração não refaz o replay nem a engenharia de features; usa os valores da
requisição que efetivamente alimentou a API. O HGB é carregado apenas para a
análise explicativa, fora do caminho de latência da API. Não altera modelos,
política, fila, feedback ou alias. As verificações existentes de compatibilidade
permanecem; não é necessário reescrever os recibos já concluídos.

## Executar no ambiente do autor

Primeiro aplique, teste e commite a implementação revisada. Deixe o champion/API,
tracking e fundação do KinD saudáveis e não execute escritores do fluxo/Registry
ou rollout durante a análise. O input aceito `champion-http-v1` já existe.

Para produzir e verificar os cinco casos no ensaio nativo:

```bash
FRAUD_CASE_EXPLANATIONS_NO_OTHER_WRITERS=1 \
  .venv/bin/python -m tox -e case-explanations
```

O teste chama o mesmo controlador disponibilizado pelo Make, imprime o caminho
do HTML e o `manifest_sha256`, e verifica o relatório publicado. A alternativa
para gerar diretamente é:

```bash
make product-explain PRODUCT_NO_OTHER_WRITERS=1
```

**Escolha uma das duas formas de geração.** O ID padrão é `champion-shap-v1`;
com um relatório concluído, use os comandos de leitura/verificação. O gerador
recusa sobrescrita. Falhas anteriores à publicação removem apenas o staging
privado e permitem retomar o mesmo ID após resolver a causa. Não crie outro ID
para contornar uma divergência de fonte, score ou identidade.

```bash
make product-explanations-verify PRODUCT_EXPLANATION_SHA256=COLE_O_DIGEST_IMPRESSO &&
make product-explanations PRODUCT_EXPLANATION_SHA256=COLE_O_DIGEST_IMPRESSO

xdg-open data/reports/case-explanations/champion-shap-v1/index.html
```

A verificação usa o digest externo informado pelo operador e os arquivos fontes
aceitos. Não carrega modelo, chama API ou recalcula SHAP. O HTML usa somente
arquivos locais, sem serviço adicional; pode ser aberto diretamente no navegador.
`make product-cases` conserva a consulta original do ledger. A consulta enriquecida
é `make product-explanations`, com a mesma identidade de transação e os vínculos
matemáticos e de proveniência da explicação.

## Entrega ao analista

| Artefato | Uso |
| --- | --- |
| `index.html` | Contexto, score, triagem, feedback posterior e gráficos por caso |
| `case-<transaction_id>.svg` | Contribuições das 19 features, ordenadas por magnitude |
| `cases.json` | Requisição/resposta/triagem originais e explicação vinculada por hashes |
| `manifest.json` | Pesos/release, fontes, versões, método, tolerâncias e hashes dos arquivos |

Laranja indica contribuição que aumenta a margem; azul indica contribuição que
a diminui; cinza indica zero. Essas cores não representam fraude confirmada,
saúde do serviço ou aprovação de um modelo. As contribuições são em **log-odds**,
não pontos percentuais de probabilidade. O score permanece um ranking não
calibrado. A importância de features correlacionadas depende do método e da
referência; contribuição não é causa da fraude.

O feedback aparece como informação posterior, com seu instante de disponibilidade,
e não entra no cálculo da explicação. Casos escolhidos para investigar não
estimam importância global, desempenho ou incidência de fraude. O relatório é
identificado com o modelo que era champion durante os gates de geração; uma
promoção posterior não transforma explicações antigas em explicações dos novos pesos.

## Estado do aceite

Implementação e regressão local preparadas. O aceite dos casos do champion no
KinD será fechado a partir da saída do ensaio nativo do autor. A manutenção do
instalador já está aceita. Promoção e rollback de sucessor com pesos diferentes
continuam como aceite independente, condicionado a candidato qualificado.
