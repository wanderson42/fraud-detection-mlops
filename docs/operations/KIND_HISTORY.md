# Histórico dos experimentos e referência HGB no KinD

Este procedimento leva o histórico verificado ao MLflow já instalado no KinD e
registra a referência HGB aprovada para o laboratório. Dummy, Regressão Logística,
HGB, ablações e busca temporal permanecem consultáveis no mesmo servidor.
**Implementação preparada; aceite nativo da transferência pendente.**

## Origem, destino e identidades

A origem é o snapshot restaurado `c9c4e6e0…4d924`: dois stores, 71 runs e
67 logged models. O controle externo vem da release restaurada `a33bb2fb…6b154`.
Os dois bundles passam novamente por verificação completa antes da transferência.
O processo não inicializa MLflow sobre os SQLite históricos nem consulta as
pastas originais de treino. Também não executa fit ou nova avaliação do teste.

O destino é o serviço `fraud-tracking` no cluster e namespace `fraud-platform`,
com PostgreSQL e RustFS existentes. O cliente usa somente a API HTTP por um
port-forward loopback de duração limitada, sem credenciais S3 ou PostgreSQL.
Os experimentos sintéticos existentes permanecem preservados.

A API cria novos IDs nativos. Os recibos guardam o mapa origem → destino e as
tags `fraud.history.*` guardam procedência e metadados originais. Datas de criação
de experimentos/logged models não podem ser retroativas pela API; ficam nesses
metadados. Datas de início/fim, status e ciclo de vida das runs são conferidos.
Os artefatos mantêm seus bytes, inclusive referências originais embutidas em
`MLmodel`; a navegação do novo servidor usa os IDs mapeados.

Os nomes dos experimentos principais são preservados, incluindo
`fraud-temporal-baseline-v1`. `Default` usa `historical-main::Default`; os nomes do
store auxiliar recebem `interface-smoke::`. Os bancos e arquivos restantes do
snapshot ficam num run adicional de procedência, em
`fraud-history-transfer-control`. Assim, o gate conta **71 runs importadas mais
uma run de procedência**, sem confundir essa run com resultado de modelagem.

## Executar o recorte

Use a branch de trabalho atual. O cluster deve estar operando e os bundles
restaurados devem continuar em `data/storage/restored/`. Suspenda produtores de
tracking durante importação/registro; `--no-other-writers` declara essa condição,
não é um mecanismo de bloqueio de clientes externos. O lock local coordena apenas
os controladores deste checkout. Feche port-forwards antigos se estiverem usando
a mesma sessão, sem apagar serviços, volumes, owner ou recibos.

```bash
.venv/bin/python -m tox -e py314 -- tests/infra/test_kind_history.py

poetry run python -m scripts.platform.kind_history plan \
  --bundle-id c9c4e6e0f684ba21484e7491c026a24a76002570c251c97c98b244f043a4d924 \
  --release-bundle-id a33bb2fbb3e9503b4bb96c92dd6950677127b77fbf5bedb4322007587176b154 \
  --plan data/storage/kind-platform/history/plan-v1.json

poetry run python -m scripts.platform.kind_history import \
  --plan data/storage/kind-platform/history/plan-v1.json --no-other-writers

poetry run python -m scripts.platform.kind_history verify \
  --plan data/storage/kind-platform/history/plan-v1.json

poetry run python -m scripts.platform.kind_history champion \
  --plan data/storage/kind-platform/history/plan-v1.json --no-other-writers
```

`plan` verifica fontes, gate e ownership e grava um arquivo privado exclusivo;
não escreve no servidor. Não o recrie se ele já existir: retome o mesmo plano.
`import` cria/reutiliza entidades pela identidade original e publica os artefatos.
`verify` faz leitura independente, inclusive download e SHA-256 de todos os
artefatos. `champion` repete essa conferência antes de recarregar os três modelos.
Recibos novos ficam em `data/storage/kind-platform/history/`.

Se houver falha, preserve plano, recibos e entidades parciais e repita a mesma
ação após resolver a causa. Não há limpeza destrutiva ou rollback automático das
escritas de API. A retomada confere identidade antes de reutilizar entidades;
nomes existentes sem a procedência esperada causam recusa. Ela não cria outra
cópia para contornar conflitos. Fonte alterada, mudança de ownership ou revisão
do código exigem revisão explícita antes de continuar.

## Gate para `champion`

O comando verifica todos os parâmetros, tags, históricos completos de métricas,
relações entre runs/modelos/datasets e arquivos dos dois stores. Tabelas
populadas sem mapeamento de API, experimentos apagados ou linhagens não suportadas
causam recusa explícita; não são silenciosamente omitidos.

A comparação histórica deve conter os três candidatos e seus pipelines. Cada um
é baixado pelo destino HTTP, tem seus bytes conferidos e é recarregado. O HGB
precisa coincidir com o hash congelado
`9728f8dddc4daa9e5478db1a8a173adecb71e41c5992965c117d2b46a27625d9`
e produzir `0.4178630794049542` no controle histórico, com erro absoluto ≤ `1e-12`.
A adaptação explícita de inteiros para doubles segue a assinatura do modelo.
O ambiente de recarga precisa corresponder ao pin da release.

A aprovação de qualidade é a já registrada: AP `0.6407032568368243` ≥ `0.5` e
precisão diária por cliente no top 100 `0.55` ≥ `0.45`. O comando confere política
congelada, hash do relatório arquivado, run de avaliação e identidade do HGB.
Não calcula métricas novas nem seleciona outro candidato usando o teste.

Só então cria/reutiliza uma versão em `fraud-hgb-reference`, verifica seus
artefatos e atribui `champion`. Um alias já apontando outra versão não é
substituído automaticamente. O escopo continua sendo referência para serving de
laboratório, sem promoção de produção.

## Conferir na interface e encerrar o aceite

```bash
make platform-mlflow
```

Em Experiments, abra `fraud-temporal-baseline-v1` e compare `dummy_prior`,
`logistic_regression` e `hist_gradient_boosting`. As tags permitem rastrear os IDs
originais; os outros experimentos importados preservam ablações e busca temporal.
Em Models, confira `fraud-hgb-reference`, a versão e o alias `champion`.
A URI resultante é `models:/fraud-hgb-reference@champion`.

O aceite exige recibos `status=success`, contagens 71/67, leitura integral de
volta, três recargas aprovadas, paridade do HGB, alias resolvendo a run esperada e
fontes/identidades do destino preservadas. Os testes locais usam MLflow HTTP com
backend SQLite; PostgreSQL/RustFS/KinD precisam passar nos comandos acima.

**O alias não troca o modelo servido.** A API instalada mantém sua release até
um gate próprio ativar a referência histórica, testar identidade/score e provar
rollback. Essa ativação é o próximo recorte operacional; este procedimento não
declara migração primária de todos os dados nem fechamento funcional integral.

## Retomada quando a listagem omite o tamanho de um arquivo vazio

O proxy pode informar `file_size=null` para `publish.lock`, cujo tamanho no
snapshot é zero. A conferência aceita tamanho ausente somente para esse caso:
o download continua obrigatório, com tamanho local igual a zero e SHA-256
idêntico. Tamanho ausente para um arquivo não vazio continua sendo recusado.

Para um plano criado antes dessa correção, preserve o original e revise apenas
o pin da implementação conhecida. O comando recusa outras mudanças de código,
fontes, gate ou ownership e não escreve no servidor:

```bash
poetry run python -m scripts.platform.kind_history_readback_fix \
  --plan data/storage/kind-platform/history/plan-v1.json \
  --output data/storage/kind-platform/history/plan-v1-readback-fix.json

poetry run python -m scripts.platform.kind_history verify \
  --plan data/storage/kind-platform/history/plan-v1-readback-fix.json

poetry run python -m scripts.platform.kind_history champion \
  --plan data/storage/kind-platform/history/plan-v1-readback-fix.json \
  --no-other-writers
```

Use esse caminho quando a importação já terminou de publicar e falhou apenas
na conferência de `snapshot-extras`. Não é necessário republicar o histórico.
Uma recusa em `verify` deve ser investigada antes do registro; não apague runs,
modelos, recibos ou planos para contornar a conferência.
