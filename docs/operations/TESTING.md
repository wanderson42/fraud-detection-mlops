# Validação com pytest, Poetry e tox

Os checks protegem contratos e falhas usando dados controlados. Resultados locais,
CI da revisão publicada e avaliações com os dados reais são evidências distintas.

## Executar e selecionar testes

Na raiz do checkout, com Python 3.14.4 e Poetry:

```bash
poetry install
make validate
```

`make validate` executa `poetry run tox -e py314`, a mesma referência da CI.
Para desenvolvimento rápido, use `make test`. Para selecionar uma área mantendo
os checks de lint e formatação:

```bash
poetry run -- tox -e py314 -- tests/data tests/features
```

O primeiro `--` encerra opções do Poetry; o segundo encaminha argumentos ao pytest.
Só declaramos suporte ao Python 3.14; sua ausência causa falha.

## Organização e responsabilidade

| Implementação | Testes |
| --- | --- |
| `data/ingestion/handbook_bronze.py` | `tests/data/ingestion/test_handbook_bronze.py` |
| `data/datasets/silver_dataset.py`, `gold_dataset.py` | `tests/data/datasets/` |
| `data/quality/` | `tests/data/quality/` |
| `features/causal_history.py` | `tests/features/test_causal_history.py` |
| `modeling/contracts/`, `modeling/experiments/` | `tests/modeling/contracts/`, `tests/modeling/experiments/` |
| `evaluation/`, `integrations/` | `tests/evaluation/`, `tests/integrations/` |
| `serving/` | `tests/serving/` e integração da exportação |
| Comandos atuais e paths padrão | `tests/data/test_entry_points.py`, `tests/modeling/test_entry_points.py` |
| Escrita atômica e identidade dos arquivos | `tests/test_artifacts.py` |

Os caminhos de implementação são relativos a `fraud_detection_mlops/`.
`tests/conftest.py` compartilha fontes sintéticas; `tests/experiment_fixtures.py`
mantém auxiliares de comparação. Arquivos de teste não importam outros arquivos
de teste. Integrações podem cobrir vários módulos; não exigimos um teste por arquivo.
O pytest usa `--import-mode=importlib`.

## Riscos protegidos

| Área | Comportamento conferido |
| --- | --- |
| Bronze e artefatos | Integridade, retomada, concorrência, corrupção, falhas de download/publicação e escrita atômica |
| Silver e perfil | Tipos, aceitação semântica, zeros, reconciliação e preservação da origem |
| Features e Gold | Oracle independente por máscaras temporais, nanossegundos, peers, atraso de rótulos, invariância ao futuro e splits |
| EDA | Restrição ao treino, exclusão dos holdouts, agregações e corrupção de inputs/outputs |
| Interface e treino | Factories novas, alinhamento, pré-processamento aprendido só no treino, classe positiva e exclusão do teste |
| Persistência e MLflow | Tipos revisados antes de desserializar, assinaturas, runs independentes e paridade após recarga |
| Ranking e comparação | Agregação por cliente/dia, score máximo, desempate por ID, orçamento de cem e população pareada |
| Diagnósticos e ablações | Validação exclusiva, modelo congelado, SHAP, retomada sem refit/duplicação e dias elegíveis |
| Congelamento e avaliação final | Identidades commitadas, modelo fixo, inputs autorizados, acesso registrado, retries e recomputação offline |
| Optuna | SQLite real, orçamento global, lock, falhas, trial abandonado, retomada determinística do TPE e mudança de identidade |
| Serving | Validação antes da predição, paridade HTTP, readiness, hashes/tipos antes da carga e exportação sem fit ou novas runs |

A preparação de desenvolvimento usa bytes reservados inválidos para demonstrar
que setembro não é aberto. A comparação TPE usa o runner público numa chamada e
em lotes, além da inicialização do sampler. Integrações usam HGB, MLflow e skops
reais em dados pequenos; nos cenários históricos relevantes, fits são proibidos
e partições não autorizadas são removidas. Isso não produz resultados reais de fraude.

## Contrato de integração de modelos

`tests/modeling/contracts/test_model_interface.py` confere os três modelos
registrados e uma contribuição sintética externa: factories independentes,
seed/parâmetros, pré-processamento aprendido só no treino, alinhamento de entradas
e paridade após recarga. Os cenários negativos rejeitam estimadores já ajustados,
features inválidas, classe positiva incompatível e probabilidades fora do contrato
antes da publicação. O exemplo externo passa pelo mesmo treino e tracking.
O [guia de contribuição](../modeling/CONTRIBUTING_MODELS.md) define a interface.

## Sequência e isolamento

1. tox cria ou reutiliza `.tox/py314`, separado da `.venv` de desenvolvimento.
2. [check_test_environment.py](../../scripts/check_test_environment.py) confirma que Poetry aponta para o Python do tox.
3. `poetry check --lock` exige coerência entre projeto e lockfile.
4. `poetry sync --only main,pipeline,dev` instala as versões fixadas e o projeto editável.
5. O mesmo Python executa Ruff e pytest; falhas interrompem a validação.

O tox define `VIRTUAL_ENV` e impede outro ambiente Poetry. `sync` pode remover
pacotes extras dentro de `.tox/py314`; a `.venv` principal não é o alvo. As versões
vêm do lockfile, sem uma lista independente de dependências do tox.
[Configuração](../../tox.toml) e [workflow de CI](../../.github/workflows/ci.yml).

## Validação da reorganização

A primeira etapa conservou dependências, protocolos e parâmetros. No checkout
`b595985`, 224 casos passaram antes da migração. As três factories foram comparadas
antes/depois com 600 linhas de treino e 300 de avaliação sintéticas: parâmetros e
scores idênticos, incluindo um HGB com predições não constantes. Um estudo sintético
anterior foi verificado com fit bloqueado. A suíte da primeira etapa aprovou 245 casos.

A segunda etapa compara Silver (63 linhas), Gold (12 linhas) e features sobre todo
o contexto (63 linhas) usando a mesma fonte sintética offline: igualdade exata dos
valores, ordem e tipos. SQL causal, resumo e tabelas da EDA também são comparados.
Silver e Gold produzidas antes da mudança são verificadas depois sem build.
Os testes de compatibilidade dessa etapa conferiram imports, cinco CLIs de dados e paths padrão.
A identidade de código deve mudar quando uma regra compartilhada ou seu path muda,
mantendo estabilidade entre checkouts e ordens de inventário.

Na preparação, **271 testes passaram**, com 77 avisos de dependências; Ruff,
formatação e lockfile também passaram no tox com Python 3.14.4.
O [recibo desta etapa](../../references/evidence/data_features_organization_preparation_2026-10-09.json)
registra ambiente, identidade do código e escopo das conferências. Os dados reais do
autor não são reconstruídos nesta preparação; CI remoto e validação no Alienware
permanecem verificações distintas. Procedimentos ficam no
[runbook de migração](OPERATIONS.md#migração-de-dados-e-features).

## Revisão da suíte após a organização

O smoke test `test_transaction_round_trip.py` foi removido: chamava apenas pandas,
PyArrow e DuckDB, enquanto a integração da Silver já confere leitura/escrita pelo
builder, tipos, valores e consultas. Os seis casos de comparação pareada ficam em
`tests/evaluation/test_paired_comparison.py`; a proteção de JSON sem sobrescrita fica
em `tests/test_artifacts.py`. As assertions foram preservadas nesses movimentos.

`test_baseline_history.py` identifica explicitamente a leitura de recibos v1 sem
desserializar seus modelos. Os testes de compatibilidade acompanharam o período
de suporte aos imports e comandos anteriores. A refatoração moveu capacidades
vigentes; MLflow, persistência, causalidade, integridade e orçamento Optuna continuam
exigindo seus testes. Não removemos cenários independentes só para reduzir a contagem.

A preparação desta limpeza aprovou **270 testes**, com os mesmos 77 avisos; Ruff,
formatação e lockfile passaram no tox. O [recibo da revisão](../../references/evidence/test_suite_maintenance_preparation_2026-10-09.json)
registra a remoção, os movimentos e a igualdade das assertions e parametrizações.

## Retirada das entradas de transição

Após a migração dos consumidores do projeto, os vinte adaptadores planos foram
retirados, assim como a CLI genérica `modeling.experiments` e seus reexports de
ablação. Os testes de identidade dos aliases aposentados encerraram seu propósito.
As doze CLIs atuais e os caminhos padrão dos datasets continuam cobertos em
`test_entry_points.py`. Os três checks da API pública do pacote `features` foram
preservados em `tests/features/test_causal_history.py`.

Os testes de contrato, algoritmos, causalidade, integridade, MLflow, persistência,
histórico e orçamento Optuna permanecem. As regras de `model_interface_v1`, os
protocolos, os formatos dos artefatos e os dados existentes continuam iguais.

A preparação da retirada aprovou **242 testes**, com os mesmos 77 avisos de
dependências. `make validate` passou no Python 3.14.4, incluindo Ruff, formatação,
lockfile e tox. A diferença de 28 casos corresponde somente aos aliases aposentados.

## Serving e wheel de inferência

A preparação de serving aprovou **302 testes**, com 89 avisos de dependências;
Ruff, formatação e lockfile passaram no tox com Python 3.14.4. Os 60 casos novos
cobrem contrato HTTP, ordem de features, paridade, readiness e falhas de carga.
O teste de tipos skops existente foi adaptado à leitura dos mesmos bytes usados
na inspeção e desserialização; sua proteção permanece.

O autor também informou **302 testes aprovados, 89 avisos e tox `py314` aprovado**:
pytest em 65,81 s e tox em 70,64 s. O
[recibo do relato](../../references/evidence/laboratory_serving_author_validation_2026-10-09.json)
registra o ambiente e as limitações de identificação: o SHA do checkout não foi
fornecido. Esse resultado local não substitui a CI da revisão que será publicada
nem comprova exportação/HTTP da referência real.

A exportação usa MLflow/skops reais e 160 linhas sintéticas de validação. Antes
de exportar, o fit é bloqueado e as partições de treino/teste são removidas;
as runs e os bytes nativos permanecem iguais. Os resultados da avaliação final
usados nessa fixture também são sintéticos e já salvos.

`make validate-serving-wheel` é um check separado: constrói e instala o wheel
num ambiente temporário apenas com `main`, confirma ausência das ferramentas
offline e pontua por HTTP em diretório vazio fora do Git. Esse check passou com
o exemplo `synthetic_smoke`; não substitui a exportação da referência no Alienware
nem comprova Docker. [Procedimento](SERVING_CONTRACT.md#wheel-e-runtime-de-inferência)
e [recibo](../../references/evidence/laboratory_serving_preparation_2026-10-09.json).

## Preparação do protocolo estatístico

O plano lê somente quatro JSONs versionados, mesmo com bytes inválidos em uma
partição reservada. Os checks rejeitam políticas/recibos alterados e código não
commitado, modificado ou excluído. A referência de fila aleatória é comparada com
enumeração independente de todas as filas possíveis em populações pequenas,
incluindo clientes recorrentes; probabilidades extremas conservam o resultado
em log10 quando a representação linear sofre underflow.

Esses checks verificam a implementação e os limites declarados de interpretação;
não avaliam o HGB em setembro. A preparação aprovou **324 testes**, com 89 avisos
de dependências; lockfile, Ruff e formatação passaram no tox com Python 3.14.4. O
[recibo da preparação](../../references/evidence/reference_assessment_preparation_2026-10-09.json)
registra o ambiente e a suíte completa. Validação no Alienware e CI da nova revisão
permanecem evidências distintas. [Protocolo e comandos](../modeling/EVALUATION_PROTOCOL.md#protocolo-estatístico-da-referência--v1).

## Executor da referência em setembro

Os checks percorrem 28 partições Silver sintéticas, geram somente os 14 dias
de avaliação e conferem o histórico de rótulos por máscara temporal independente.
Arquivos inválidos em maio e no replay asseguram que essas janelas não entram
no cálculo. A carga de MLflow/skops é conferida num store temporário, sem fit
durante a carga ou nova run. Os cenários exercitam dias de uma classe, cobertura
ausente, dados inválidos, symlinks, corrupção rehasheada, concorrência e interrupção
após publicação; retomadas completas não carregam modelo nem consultam a Silver.

[Escopo e comandos](../modeling/EVALUATION_PROTOCOL.md#executor-auditável-da-janela-fixada)
e [recibo da preparação](../../references/evidence/reference_assessment_execution_preparation_2026-10-09.json).
A preparação aprovou **348 testes**, com 94 avisos de dependências; lockfile, Ruff
e formatação passaram no tox com Python 3.14.4. A execução com os artefatos do
autor continua separada destes checks. O autor também informou **348 testes/94
avisos em 77,27 s**, tox aprovado em 82,25 s, seguido de `run` e `verify` nativos
com sucesso. O [recibo do autor](../../references/evidence/reference_assessment_author_validation_2026-10-09.json)
registra essa evidência; a CI da revisão publicada ainda precisa ser conferida.

## Evidências anteriores

As evidências abaixo identificam revisões anteriores, com paths e hashes históricos
preservados. Não são aprovação da revisão atual.

| Marco | Evidência de preparação | Evidência com os dados do autor |
| --- | --- | --- |
| Gold | [Checks controlados](../../references/evidence/gold_preparation_2026-10-07.json) | [Build/verify informado](../../references/evidence/gold_build_2026-10-07.json) |
| Interface de modelos | [Contrato e integração](../../references/evidence/model_interface_preparation_2026-10-08.json) | — |
| Congelamento | [164 testes](../../references/evidence/freeze_preparation_2026-10-08.json) | [Execução informada](../../references/evidence/freeze_execution_2026-10-08.json) |
| Avaliação final | [180 testes](../../references/evidence/final_evaluation_preparation_2026-10-08.json) | [Model Card](../modeling/MODEL_CARD.md) |
| Optuna | [224 testes](../../references/evidence/optuna_preparation_2026-10-08.json) | [Relatório fornecido](../../references/evidence/hgb_optuna_author_report_2026-10-09.json) |

## Recuperação e limites

- Ferramenta ou Python ausente: confira a instalação e o grupo `dev` do Poetry.
- Alvo Poetry incorreto: revise a configuração; preserve o check anterior ao `sync`.
- Ambiente inconsistente: use `poetry run tox -r -e py314`.
- Lockfile divergente: revise as dependências e gere um lockfile consistente.

Os testes usam diretórios temporários e rede simulada; não baixam o Handbook.
Instalar dependências pode exigir rede. `verify` em fixtures não substitui verificar
os artefatos reais. Avisos de terceiros permanecem visíveis, sem supressão global.

Contagem inclui parametrização e não é uma meta isolada. Acrescentamos casos que
protegem um risco concreto; dados pequenos limitam o custo. O checkout editável e
`package = "skip"` no tox não comprovam um wheel independente nem recursos de
`references/` empacotados. Latência, disponibilidade, streaming e eficácia antifraude
real terão verificações próprias, conforme o [mural](../project/ROADMAP.md).
