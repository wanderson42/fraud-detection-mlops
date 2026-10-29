# Explicações dos casos do champion

## Aceite da sessão reconciliada na VM — 2026-10-29

O autor publicou/verificou cinco explicações em `repro-session-shap-v1` e preparou
`investigation-shap-v1`, preservando o snapshot anterior. Smoke e navegação nativa
passaram: portal-unified teve 1 teste aprovado em 1,09 s (tox 15,94 s), com hashes
preservados. A captura revisada mostra o caso 1610254, score 0,999225, gráfico SHAP,
reconstrução do score e feedback posterior separado.

[Identidades, proveniência e limites do aceite](VM_REPRODUCTION_PROGRESS.md#aceite-nativo-de-shap-e-portal-da-sessão--2026-10-29) e
[recibo documental](../../references/evidence/session_shap_author_validation_2026-10-29.json).
CI/merge da branch são um gate separado. Os flags visuais originais permanecem
false; a captura complementa a evidência. O procedimento abaixo preserva a
sequência de preparação; a pendência nativa descrita naquela nota foi encerrada
pelo aceite acima no escopo do portal da sessão concluída.



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

O autor publicou e verificou os cinco casos no ensaio nativo em 2026-10-22.
O relatório `champion-shap-v1` está vinculado ao modelo
`6dad9a78a5da0bda34cae6957b0ddbe391ecd848fbbc6604ae930bea3501370b`
e à release `e8350e0a841f4136983e0d905068a743`, com digest de manifest
`1f780b2d7eb9de7a553435f3ccd69ede37eca735cbdddae6dadbb01981e0d91c`.
A verificação offline passou sem carregar o modelo; o champion foi preservado.
[Evidência](../../references/evidence/case_explanations_author_validation_2026-10-22.json).

No caso 1610254, a taxa conhecida de fraude do terminal em 7 dias contribui
`+4,79087707` log-odds para o score `0,99922529`. O feedback posterior dessa
transação é 1. No caso 1610229, score `0,93497685`, a contribuição da taxa de
1 dia é `+2,20451993`; o feedback posterior é 0. Esses exemplos permitem
inspecionar os fatores de alertas com resultados posteriores distintos; não
medem a qualidade global nem estabelecem causalidade.

O anexo fornecido contém o HTML com as tabelas. Os cinco SVG são referenciados
por caminhos relativos; para compartilhar o relatório completo, preserve todo
o diretório de saída. A manutenção do instalador também está aceita. Promoção
e rollback de sucessor com pesos diferentes permanecem como aceite independente,
condicionado a candidato qualificado.

## Sessão reconciliada na VM — preparação em 2026-10-29

O gerador aceita duas fontes: o fluxo com ensaio histórico e a sessão v1 com
verificação reconciliada. Um rehearsal existente, mesmo inválido, não é contornado
pelo caminho da sessão. Para sessão, a verificação completa reconstrói o ledger
em memória e compara o recibo ao checkpoint atual antes de selecionar os casos.
As explicações usam as features registradas; não alteram o checkpoint nem fazem
novas chamadas HTTP de pontuação. O código da reconciliação também entra nos pins
do relatório. Relatórios de revisões anteriores permanecem preservados; seu
verificador pode recusar pins antigos após esta alteração.

A seleção continua limitada a 1–20 casos, alertas primeiro e score decrescente,
sem escolher pelo rótulo. SHAP usa os pesos da release identificada e verifica
aditividade e score. Feedback posterior continua apenas como contexto observado.
O snapshot `investigation-v1` sem SHAP já foi aceito visualmente pelo autor;
esta preparação não equivale a aceite nativo das novas explicações.

Depois de aplicar, testar e commitar a implementação, sem produtor, alteração de
Registry ou rollout concorrente, gere cinco casos (observador e Grafana podem
continuar ativos):

```bash
make product-explain \
  PRODUCT_FLOW_ID=session-repro-arrival-200-v1 \
  PRODUCT_EXPLANATION_ID=repro-session-shap-v1 \
  PRODUCT_CASES_LIMIT=5 \
  PRODUCT_NO_OTHER_WRITERS=1
```

Conserve o manifest_sha256 impresso. Use-o nos comandos abaixo como SHA_DO_RELATORIO;
não repita geração sobre relatório existente. Verifique o relatório publicado:

```bash
make product-explanations-verify \
  PRODUCT_EXPLANATION_ID=repro-session-shap-v1 \
  PRODUCT_EXPLANATION_SHA256=SHA_DO_RELATORIO
```

Preserve o portal anterior: criar outro snapshot é intencional, pois seus bytes e
sua avaliação já estão fixados por hashes. Os IDs abaixo não criam outro serviço:

```bash
make portal-prepare \
  PORTAL_ID=investigation-shap-v1 \
  PRODUCT_FLOW_ID=session-repro-arrival-200-v1 \
  PRODUCT_EXPLANATION_ID=repro-session-shap-v1 \
  PRODUCT_EXPLANATION_SHA256=SHA_DO_RELATORIO \
  PRODUCT_NO_OTHER_WRITERS=1 &&
make portal-session-link \
  PORTAL_ID=investigation-shap-v1 \
  PORTAL_SESSION_ID=repro-arrival-200-v1 &&
make portal-performance-prepare \
  PORTAL_ID=investigation-shap-v1 \
  PORTAL_PERFORMANCE_ID=repro-shap-validation-v1 \
  PORTAL_EVALUATION_ROUND=repro-v1 \
  PRODUCT_NO_OTHER_WRITERS=1 &&
make portal-verify PORTAL_ID=investigation-shap-v1 &&
make portal-session-verify PORTAL_ID=investigation-shap-v1 &&
make portal-performance-verify PORTAL_ID=investigation-shap-v1
```

A avaliação precisa de outro ID porque se vincula ao manifest do novo snapshot;
reutiliza a validação da rodada, sem fit nem novo holdout. Depois dos checks,
troque somente o acesso à interface, na mesma porta 18501:

```bash
make access-stop ACCESS_SERVICE=portal &&
make access-start ACCESS_SERVICE=portal PORTAL_ID=investigation-shap-v1 &&
make portal-session-smoke PORTAL_ID=investigation-shap-v1 &&
FRAUD_PORTAL_ID=investigation-shap-v1 .venv/bin/python -m tox -e portal-unified
```

Confira visualmente um dos casos explicados, contribuições das 19 features,
score/identidade e mensagem sobre feedback posterior. Casos fora dos cinco
selecionados continuam sem SHAP: a interface não estima contribuições ao abrir.
O gate funcional e a revisão visual são distintos. Para voltar ao portal anterior,
pare somente o acesso e inicie-o com PORTAL_ID=investigation-v1; não apague snapshots,
relatórios, avaliações ou recibos.
