# Validação com pytest, Poetry e tox

A nova rodada tem testes de política, linhagem, MLflow HTTP, recarga, Registry, API real em processo e Helm. Execute `tox -e py314 -- tests/operational_round tests/storage/test_persistence_inventory.py tests/storage/test_managed_paths.py`; a execução nativa dos Jobs/serving permanece no runbook.
[Guia da rodada operacional](OPERATIONAL_ROUND.md).

## Interfaces comuns e escopo da suíte

Os 12 comandos de ajuda de dados/modelagem e a verificação dos diretórios padrão
agora estão em um único teste parametrizado:

```bash
.venv/bin/python -m tox -e py314 -- tests/interfaces/test_command_entrypoints.py
```

A matriz conserva os cenários anteriores, sem duplicar o corpo do subprocesso.
Em seleções focadas de dados ou modelagem, acrescente `tests/interfaces` quando
quiser incluir os smokes dos CLIs. A suíte completa continua coletando todos.
Os testes exclusivos do importador histórico foram retirados com essa ferramenta;
o fluxo canônico mantém seus testes de Inputs, métricas, Registry, HTTP e recovery.
[Critérios da revisão](CODEBASE_SIMPLIFICATION.md).

<!-- kind-operations-acceptance-2026-10-21:start -->
**Estado em 2026-10-21 — marco KinD/Helm concluído no escopo do laboratório.**
Parada/subida e reboot planejado aprovados no mesmo node e volumes, com inotify
persistente e leituras de tracking, serving e monitoramento conferidas.
[Sequência integrada, aceites e limites](../../docs/operations/KIND_PLATFORM.md). Próximo: simplificação
e fechamento funcional do portfólio; Terraform permanece posterior. Notas
datadas abaixo preservam o estado de cada incremento, sem reabrir gates aceitos.
<!-- kind-operations-acceptance-2026-10-21:end -->


Os checks protegem contratos e falhas usando dados controlados. Resultados locais,
CI da revisão publicada e avaliações com os dados reais são evidências distintas.

## Executar e selecionar testes

Na raiz do checkout, com Python 3.14.4 e Poetry:

```bash
poetry install
make validate
```

`make validate` executa `poetry run python -m tox -e py314`, a mesma referência da CI.
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
| `streaming/contracts/`, `streaming/features/`, `streaming/replay/` | `tests/streaming/`; fixtures geradas e release sintética compartilhadas no conftest da área |
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
| Replay causal local | Eventos sem alvo, feedback separado, paridade Gold e scores sintéticos, bordas/empates, duplicatas/conflitos, atraso rejeitado, checkpoint e rollback após falha/saída abrupta |

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

## Diagnóstico de dependência das previsões salvas

Os checks conferem autocorrelação e influência dos dias com exemplos calculáveis
à mão, recorrência de entidades e sobreposição entre dias. Séries constantes,
dias de uma classe e valores indefinidos conservam sua interpretação explícita.
A integração reutiliza o bundle sintético verificado com a Silver removida,
confere ausência de novas cargas/pontuações e preservação dos bytes da avaliação.
Corrupção das previsões impede o diagnóstico; destinos dentro da avaliação são
rejeitados antes do acesso. Esses checks não determinam o método de incerteza.
A preparação aprovou **362 testes**, com 94 avisos; os 14 checks novos passaram
também isoladamente. Ruff, formatação e lockfile passaram no tox `py314`.
O [recibo de preparação](../../references/evidence/reference_dependence_preparation_2026-10-09.json)
registra hashes e cenários. O autor executou o diagnóstico em `70a0acc`, após
aprovar 362 testes/94 avisos; o JSON nativo foi revisado nesta entrega e registrado
no [relato do autor](../../references/evidence/reference_dependence_author_validation_2026-10-09.json).

## Sensibilidade por blocos de dias completos

Os checks verificam blocos circulares, multiplicidades, truncamento da última
extração e reprodutibilidade. A AP agrupada otimizada é comparada com réplicas
que duplicam explicitamente as linhas e usam o scikit-learn, incluindo empates
de score e tamanhos diários distintos; não é a média das APs diárias. Classes
únicas e pesos inválidos não geram faixas válidas. Réplicas indefinidas suprimem
a faixa e preservam as contagens. A integração funciona com a Silver removida,
sem novas cargas/pontuações ou mudanças nos bytes da avaliação original.

A preparação passou **379 testes/134 avisos**, Ruff (147 arquivos formatados),
Poetry/lockfile e tox `py314`, em 156,00 s. O run inclui avisos de depreciação e
de conexões SQLite não fechadas em testes existentes; não houve falhas. O
[recibo](../../references/evidence/reference_uncertainty_preparation_2026-10-09.json)
registra hashes e cenários, incluindo um check sintético de capacidade. Esses
checks não substituem o relatório reamostrado das previsões nativas.

O autor agora forneceu esse relatório nativo: as quatro configurações e seus
hashes foram revisados, sem métricas indefinidas. O
[recibo](../../references/evidence/reference_uncertainty_author_validation_2026-10-09.json)
distingue resultados fornecidos, verificações de revisão e bytes não recebidos.
O fechamento altera documentação/evidência; executores e testes permanecem
idênticos à preparação de 379 testes. Nenhum novo resultado de suíte nativa foi
informado junto ao JSON.

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

## Validação Docker e TestClient

O autor aprovou a preparação Docker com 393 testes/94 avisos. Após as correções
do preflight e da captura de logs, informou 82 testes focados/1 aviso. O build/run
nativo posterior aprovou HTTP, integridade, reinício e proteções, conservando os
arquivos da release. O [resultado e os limites](SERVING_CONTRACT.md#resultado-nativo-docker--2026-10-09)
separam testes sintéticos, execução Docker e medições sequenciais.

O `TestClient` do Starlette prefere `httpx2`; a depreciação aparece quando utiliza
o cliente antigo. `httpx2==2.13.1` pertence ao grupo `dev`. O procedimento final
verifica o cliente selecionado e executa:

```bash
poetry run python -m pytest -q tests/serving \
  -W error::starlette.exceptions.StarletteDeprecationWarning
make validate
git diff --check
```

A [documentação do TestClient](https://starlette.dev/testclient/) descreve essa
seleção. Os avisos de outras dependências continuam visíveis. O fechamento
arquiva o log da validação completa por hash e registra os totais observados,
sem inferir novos totais a partir das suites anteriores. Alterações no lockfile
devem conservar as versões `main` da imagem validada; uma mudança de runtime
exige novo build/run e recibo próprio. A CI precisa aprovar o SHA final do PR.

## Replay local — validação informada pelo autor

Em 2026-10-13, o autor aprovou `make validate`/tox `py314`: 448 testes, 93 avisos,
pytest em 88,35 s e tox em 94,24 s. A implementação foi commitada em `23c863e`.
O demo passou de seis para 20 eventos únicos; a repetição reaproveitou 22 entregas,
sem nova decisão. A pontuação era desabilitada no demo. Paridade de atributos e
scores foi coberta com fixtures e release sintética pelos testes da suíte.
[Recibo do relato](../../references/evidence/causal_replay_author_validation_2026-10-13.json).
Isso é evidência local fornecida pelo autor; a CI da branch ainda precisa aprovar
o SHA final, e a avaliação operacional nativa do Handbook permanece pendente.

## Política causal de triagem — preparação

`tests/streaming/decisions/test_customer_day_alert_policy.py` usa transações e
scores gerados. Confere cruzamento inclusive no limiar, cliente repetido, teto
por dia, reset à meia-noite, esgotamento sem substituição, duplicatas idênticas e
conflitantes, identidade da release, ordem/empates, estabilidade do prefixo e
reconstrução determinística. Configuração sem limiar, não finita ou com rótulo
é rejeitada. IDs de alerta vinculam configuração, cliente e dia.

Esses checks validam o redutor em memória. Não demonstram publicação externa,
persistência de alertas no checkpoint, utilidade nos dados nativos ou latência.
O protocolo mantém acesso nativo desabilitado até congelar limiar e integrar o
adaptador/persistência. Os recibos de 448 testes e demo de features continuam
históricos; não passam a representar alertas que aquela execução não produziu.

## Seleção do limiar — desenvolvimento restrito

Os novos checks exercitam máximo por cliente/dia, mediana dos 21 cortes, empates,
cobertura e exclusão de alvos. Uma Gold gerada tem somente as 21 partições físicas
permitidas; as demais existem apenas no manifesto, para detectar leitura ampliada.
O teste de ponta a ponta usa um modelo ajustado somente em exemplos sintéticos.
Projeção, corrupção, redirecionamento, datas/domínios, release incorreta, alteração
em execução, publicação sem saída parcial e lock local são conferidos. A verificação
reproduz a seleção sem carregar o modelo ou pontuar novamente.
Esses checks não calculam o limiar nativo nem avaliam o replay reservado.

## Validação do autor e registro do limiar — 2026-10-13

O autor aprovou 502 testes/93 avisos em `d40d839`: pytest em 89,06 s e tox
`py314` em 94,14 s. A preparação/verificação nativa do limiar também retornou
sucesso nos 21 dias e 201.155 transações. O [recibo](../../references/evidence/development_alert_threshold_author_validation_2026-10-13.json)
registra o relato; mediana e hash da configuração foram conferidos separadamente.
O dataset de scores/manifesto nativo não foi recebido para inspeção independente.
Este fechamento muda documentos/JSON e narrativa; não altera código, dependências,
protocolos históricos nem saídas. A validação nativa já relatada permanece válida
para o código preservado. Conferir JSON, narrativa, hashes e diff; a CI do SHA
final continua necessária antes do merge. Não confundir o resultado do seletor
com avaliação de alertas duráveis, latência ou consumo da reserva.

## Replay de triagem durável

`tests/streaming/replay/test_durable_triage_replay.py` usa modelos/eventos gerados.
Confere paridade de requests/scores com o executor v1 e decisões/IDs com o redutor;
reinício, orçamento, duplicatas/conflitos, ordem, feedback e mudanças de identidade.
Um processo separado termina abruptamente antes e depois do commit: o primeiro
reverte todos os registros; o segundo retorna a decisão já confirmada sem rescoring.
Falhas após inserir alerta e antes do checkpoint também desfazem a transação.

Os checks cobrem aquecimento sem pontuar, limite da janela, escoamento de feedback,
escritores concorrentes da mesma identidade, inspeção sem criar banco e rejeição
preservando o schema legado. O demo rejeita a release nativa antes de carregar o
modelo. Esses testes não consomem a reserva, não usam a referência do autor para
novas avaliações e não estabelecem garantias de transporte ou falha física de disco.

## Adaptador nativo e avaliação da fila causal

`tests/streaming/replay/test_native_causal_replay.py` usa Silver e release gerados,
com datas fora do escopo deliberadamente inválidas. Confere plano sem acesso a
Parquet/modelo, vínculos antes do acesso, schema/domínios/datas/IDs, eventos sem
alvo atual, feedback com atraso exato, paridade Gold/score/decisão e retomada do
sufixo sem duplicar scores. A avaliação rejeita alterações em features, scores,
feedback, eventos, alertas e política persistidos; o verificador não reabre Silver.

Também confere precisão com denominador real, capacidade ociosa, ausência de
fraudes/alertas e captura de cliente-dia com fraude posterior ao alerta. Falha de
avaliação mantém o checkpoint; publicação final por diretório evita resultado sem
recibo. Os protocolos e fingerprints dos executores anteriores são preservados.
As fixtures não fornecem evidência de eficácia, latência 1x ou replay nativo.

## Correção numérica versionada

`tests/features/test_exact_binary_mean.py` compara a média com uma especificação
racional independente, incluindo subnormais, máximos finitos, ordem de inserção,
zero e valores inválidos. Árvores HGB geradas demonstram mudanças de score por
uma ULP nos dois cortes encontrados no diagnóstico; o modelo congelado não é
retreinado. `test_deterministic_causal_history.py` compara os 19 atributos entre
janelas DuckDB independentes e histórico SQLite, com timestamps pares, fronteiras
em nanossegundos, feedback exatamente disponível e ordem offline invertida.

`tests/streaming/replay/test_numerical_corrective_replay.py` preserva uma execução
original gerada, remove Silver antes do preparo corretivo, interrompe/retoma,
verifica igualdade exata, hashes externos, publicações atômicas e rejeição de
adulteração. Também confere que verificações e retomadas completas não recalculam
scores. O checkpoint original conserva o mesmo SHA-256. Esses checks não afirmam
que o replay corretivo passou na janela real do autor.

## Evidência nativa posterior à preparação numérica

A preparação da correção aprovou 595 testes/93 avisos em Python 3.14.4, com tox,
Ruff, formatação e lockfile. Em 13/10 o autor forneceu as saídas concordantes de
`run` e `verify` reais: 556.824 eventos, 143.945 scores, 19 features exatas, gate
de scores aprovado e 1.453 alertas preservados. A evidência nativa é separada dos
testes de fixtures e está no [recibo transcrito](../../references/evidence/native_numerical_correction_author_validation_2026-10-13.json).
A atualização documental de 15/10 não altera runtime, dependências ou protocolos
executáveis e não exige nova pontuação. Os bytes dos checkpoints, scores e custos
do autor não foram inspecionados diretamente pelo assistente.


## Monitoramento e replay programado

`tests/monitoring/` cobre fila, retomada, falha após commit, produtor, 1x curto,
qualidade por coorte, empates de AP, distribuição de scores, HTTP read-only e
credenciais locais. `tests/streaming/replay/test_paced_native_assessment.py` usa
fonte gerada, remove Silver e verifica o novo ensaio, paridade e recibo sem
alterar o checkpoint anterior. Não acessa a entrada nativa do autor.
Regras de alerta têm fixtures próprias para `promtool test rules`; dashboards
são provisionados por JSON e PromQL. [Escopo observado da preparação](../../references/evidence/local_observability_preparation_2026-10-15.json).
Docker/RustFS e execução nativa continuam com validação local separada.

## Indisponibilidade e recuperação do exporter

O runner versionado está em
[`scripts/validation/exporter_recovery.py`](../../scripts/validation/exporter_recovery.py).
Seus 17 casos em `tests/monitoring/test_exporter_recovery.py` usam arquivos gerados
e HTTP/Docker simulados: baseline inválido não para serviços; falha ao parar,
ausência de alerta e interrupção passam pela tentativa de retorno; erro no
restart, timeout e alteração de estado não são apresentados como sucesso.
Esses casos fazem parte do tox `py314` habitual, sem tocar na stack do autor.

O teste de infraestrutura fica em
[`tests/monitoring/live/test_exporter_recovery.py`](../../tests/monitoring/live/test_exporter_recovery.py).
Ele é coletado, mas ignorado por padrão. Sua execução é explícita:

```bash
poetry run tox -e exporter-recovery
```

Esse ambiente usa as mesmas dependências do lockfile, ativa o teste real e passa
o contexto Docker local. Não está em `env_list`; `make validate`/CI padrão não
param o exporter. Executar o arquivo de teste sem a ativação explícita resulta
em skip. O runner também pode ser chamado pela CLI documentada no runbook.

O autor já executou o utilitário anterior, antes dessa incorporação: alerta
observado em 56,36 s e recuperação em 12,29 s, com os arquivos de estado iguais.
[Evidência real](../../references/evidence/exporter_recovery_author_validation_2026-10-15.json).
Isso não é uma execução real do novo wrapper pytest/tox; a nova integração foi
verificada com fixtures e pela configuração/guarda de execução. Não é necessário
repetir o replay nem a falha real só para registrar esse resultado histórico.

O fechamento aprovou tox `py314`, Ruff, formatação e lockfile: **633 testes
aprovados, um teste real ignorado por padrão e 93 avisos de dependências**.
[Registro da preparação](../../references/evidence/observability_closure_preparation_2026-10-15.json).
Isso não é CI de um commit novo nem execução do teste real no host do autor.

## Inventário e piloto S3

`tests/storage/test_archive.py` usa arquivos temporários e um cliente S3 simulado,
incluído no tox padrão. Confere round-trip, manifesto publicado por último,
interrupção/retomada idempotente, corrupção do mesmo tamanho, caminhos inseguros,
symlinks, limites, espaço e distinção entre ausência e erro de acesso/transporte.
Não executa Docker nem acessa `data/` do autor. O teste de API com o RustFS fixado
é uma etapa operacional explícita no [runbook](STORAGE.md#piloto-implementado--execução-gradual);
passar os testes de controle não confirma compatibilidade real do servidor.

### Recarga da release restaurada

`tests/storage/test_restored_release.py` integra o tox padrão: controles gerados
carregam uma release após remover os originais e rejeitam corrupção, pin externo
incorreto, identidade diferente e score divergente. O caso nativo é ignorado por
padrão e executado explicitamente conforme o [runbook](STORAGE.md#release-completa-recuperação-de-bytes-e-recarga).
Usa o loader/HTTP existentes em FastAPI TestClient, sem TCP, Docker ou acesso S3.
A ativação exige `FRAUD_STORAGE_RELEASE_LIVE=1` e `FRAUD_STORAGE_RELEASE_BUNDLE`.
O tox padrão não repassa essas variáveis; o comando nativo usa pytest diretamente
no ambiente Poetry do autor. O relatório aparece em `data/storage/receipts/`.
A preparação com fixtures tem escopo próprio. Depois o autor aprovou o caso
nativo: 1 passed/6 deselected em 0,32 s, erro zero no score, readiness 200 e
entrada inválida 422. [Evidência da execução](../../references/evidence/storage_release_reload_author_validation_2026-10-15.json).
É HTTP em processo, sem teste de TCP, deploy, PVC, carga ou SLA de produção.

### Preparo da Bronze completa

`tests/storage/test_bronze_archive.py` usa 70 partições de bytes opacos não
desserializáveis, além de manifesto/auditoria e inventário gerados. Exercita
cobertura acima de 64 arquivos, preservação de bytes e identidade, reuso,
verificação sem fonte/inventário originais e rejeição de corrupção, locks,
bancos, alteração de auditorias e arquivos extras no bundle. O perfil tem teto
próprio; o piloto mantém formato e limites anteriores, e recusa publicar Bronze.
Os testes bloqueiam rede e funções de desserialização pickle após gerar a fonte.
A fixture usa a extração existente com resposta HTTP simulada, sem dados nativos.

Tox `py314` da preparação, selecionando armazenamento, ingestão Bronze e serving:
177 passed, 1 skipped em 8,71 s; Ruff global, formatação e lockfile aprovados.
O caso de recarga nativa foi ignorado por padrão. Depois, o autor aprovou o preparo
Bronze nativo (183 partições/188 arquivos). Isso ainda não é migração S3; o [runbook](STORAGE.md#bronze-completa--preparo-offline-antes-da-transferência)
distingue esses estados.


### Transporte Bronze: pytest/tox e SDK isolado

`tests/storage/test_bronze_transfer.py` usa a mesma fonte opaca de 70 partições,
um cliente em memória e os verificadores existentes. Cobre manifesto por último,
reenvio sem PUT, falha parcial/retomada, corrupção com tamanho preservado,
HTTP 403 versus ausência, PUT condicional concorrente, paginação, limites de
manifesto/stream, imagem desatualizada e restauração sem fonte/inventário/bundle
locais. Falhas de bytes removem o download temporário; falha do verificador
mantém a quarentena para diagnóstico e nunca publica o destino final.
Esses testes entram no `py314` padrão, sem Docker, SDK ou rede.

```bash
poetry run tox -e py314 -- tests/storage tests/data/ingestion/test_handbook_bronze.py tests/serving
poetry run tox -e bronze-sdk
```

O ambiente explícito `bronze-sdk` instala boto3/botocore e transitivas fixados por
hashes em `infra/observability/storage-requirements.txt`; não muda `poetry.lock`
nem sincroniza o ambiente científico. Usa botocore Stubber para validar os
parâmetros reais de PUT/checksum/condição, GET, erros e paginação. Não se conecta
a S3. `--confcutdir` evita carregar as fixtures científicas no ambiente operacional.
Na suíte padrão, esse módulo é ignorado por ausência do SDK. Na preparação:
196 passed/2 skipped no `py314`; SDK validado separadamente, conforme
[evidência de preparação](../../references/evidence/bronze_transfer_preparation_2026-10-15.json).
O autor depois executou o build Docker e o round-trip RustFS do runbook:
189 objetos publicados/conferidos, reenvio sem uploads e restauração completa de
183 partições com o verificador Bronze e inventário baixado.
[Evidência relatada](../../references/evidence/bronze_transfer_author_validation_2026-10-15.json).
Essa execução nativa usou os CLIs operacionais já cobertos pelos testes; os
resultados não são uma nova execução pytest/tox. Os quatro casos Stubber validam
parâmetros do SDK, enquanto o ensaio nativo cobre a interoperabilidade efetiva.
Este fechamento altera somente narrativa/evidência: conferência de links,
JSON/notebooks, bytes congelados e aplicação/reaplicação do pacote; sem nova
execução da suíte de runtime nem repetição dos dados nativos.


### Silver arquivada: contrato, auditorias e recuperação

`tests/storage/test_silver_archive.py` usa Parquets gerados pelo builder existente,
com contrato/inventário fixados e auditorias fora de `silver_v1/`. Após gerar a
fonte, remove Bronze e bloqueia rede/pickle. Cobre preparo idempotente, bytes
históricos, preservação de auditorias, exclusão de EDA, ausência/corrupção/lock,
symlinks, mudanças durante staging e recuperação após remover a Silver original,
referências e bundle preparado. O verificador existente rejeita schema, contagens,
IDs duplicados e metadados inconsistentes mesmo quando hashes do archive são
atualizados. Exercita reenvio, falha parcial e quarentena semântica com o mesmo
transporte do perfil Bronze. Casos Bronze e piloto são mantidos na regressão.

```bash
poetry run tox -e py314 -- tests/storage tests/data/datasets/test_silver_dataset.py tests/data/ingestion/test_handbook_bronze.py tests/serving
poetry run tox -e bronze-sdk
```

`bronze-sdk` mantém o nome operacional existente e testa o SDK compartilhado com
Stubber; não acessa RustFS. O schema/prefixo Silver é selecionado pelo mesmo worker.
Os resultados da preparação estão na
[evidência](../../references/evidence/silver_archive_preparation_2026-10-15.json).
Ver [gates nativos](STORAGE.md#silver-completa-preparo-e-recuperação-com-contrato).


O autor aprovou depois o preparo e round-trip Silver no RustFS: 188 objetos
publicados/conferidos, zero uploads no reenvio e `restored_verified` com
183 partições, 1.754.155 linhas/IDs distintos, schema e contagens SQL reconciliados.
Contrato/inventário baixados, sem leitura da fonte original ou Bronze.
[Evidência nativa relatada](../../references/evidence/silver_archive_author_validation_2026-10-15.json).
Foi uma execução dos CLIs operacionais, distinta dos testes com fixtures/Stubber.
A preparação permanece 235 passed/2 skipped no tox py314 e quatro aprovados no
SDK. O fechamento atual altera somente narrativa e nova evidência: diff, hashes,
links, JSON/notebooks e aplicação/reaplicação protegida conferidos, sem repetir
suite de runtime, rebuild ou restore nativo.


### Gold original: contrato temporal e recuperação

`tests/storage/test_gold_archive.py` usa Gold gerada uma vez pelo builder existente,
com quatro partições, três splits e 19 features. As cópias para os testes têm
somente Gold e referências: Silver/Bronze não estão presentes. Após setup,
pickle/rede/recomputação de features ficam bloqueados. Cobre preservação,
idempotência, auditorias externas, quatro referências, exclusão de estudo/bancos
e outras auditorias, corrupção/ausência/lock/symlink e alteração durante staging.
O round-trip remove Gold original, referências e bundle local antes de restaurar.

Os casos adversos reescrevem hashes do Parquet/manifesto de arquivamento para
provar que o verificador existente ainda rejeita schema, datas, valores de
features, IDs duplicados, contagens de split e protocolo incoerentes. Testa
falha parcial/retomada, reenvio sem uploads, perfil/prefixo e quarentena semântica.
Os perfis Bronze/Silver/piloto entram na regressão. SDK isolado usa Stubber;
não há Docker ou S3 nativo na preparação do assistente.

```bash
poetry run tox -e py314 -- tests/storage tests/data/datasets/test_gold_dataset.py tests/data/datasets/test_silver_dataset.py tests/data/ingestion/test_handbook_bronze.py tests/serving
poetry run tox -e bronze-sdk
```

Resultados e limites na [evidência de preparação](../../references/evidence/gold_archive_preparation_2026-10-15.json).
Ver [gate nativo](STORAGE.md#gold-v1-completa-arquivo-com-contratos-e-protocolo).


O autor executou também o gate nativo Gold em 2026-10-15: preparo e build,
49 uploads/49 objetos lidos/conferidos, reenvio zero uploads/49 reutilizados e
restore de 48 arquivos com 42 partições, 19 features, 402.877 linhas e os três
splits reconciliados. O host reaplicou `verify_gold` com referências baixadas,
sem consultar originais/Bronze/Silver ou recomputar features. Não houve teste
nativo de falha de storage neste fechamento.
[Evidência nativa](../../references/evidence/gold_archive_author_validation_2026-10-15.json).
O fechamento altera apenas documentação/notebooks e acrescenta essa evidência;
JSON, links, diff, células históricas e aplicação protegida são conferidos sem
reexecutar runtime. Permanecem os resultados da preparação: tox `py314`
267 passed/2 skipped e `bronze-sdk` quatro casos aprovados com Stubber.


### Dataset de desenvolvimento separado do estudo

`tests/storage/test_development_gold_archive.py` gera uma vez o dataset pelo
builder existente (63 Parquets/189 linhas/19 features). As cópias dos testes têm
somente dataset e duas referências, com sentinelas de estudo que não podem ser
abertas. Depois do setup, recomputação de features e pickle ficam bloqueados.
Cobre bytes/run/hash preservados, preparo repetível, arquivos faltantes/corruptos,
symlinks, banco em path indevido, política inválida, run incoerente e mutação
no staging. O round-trip em S3 de memória remove dataset, referências, estudo
e bundle preparado antes do restore. Sem novo treino, Docker ou S3 nativo.

Casos adversos atualizam hashes físicos e ainda exigem rejeição semântica de
schema, datas, features inválidas, IDs duplicados, contagens e política incoerente.
Cobre limites de perfil/referências, publicação parcial/retomada, manifesto por
último, reenvio sem uploads e quarentena semântica/destino final protegido.
A regressão inclui storage, verificação de desenvolvimento/Optuna, Bronze/Silver/
Gold e serving; SDK segue isolado com Stubber.

```bash
poetry run tox -e py314 -- tests/storage tests/modeling/experiments/test_temporal_development_data.py tests/modeling/experiments/test_hgb_optimization.py tests/data/datasets/test_gold_dataset.py tests/data/datasets/test_silver_dataset.py tests/data/ingestion/test_handbook_bronze.py tests/serving
poetry run tox -e bronze-sdk
```

[Resultados e limites](../../references/evidence/development_gold_archive_preparation_2026-10-15.json).
O teste do novo perfil não abre o SQLite real nem valida backup do estudo;
esses são gates separados. [Procedimento](STORAGE.md#gold-de-desenvolvimento-dataset-e-estudo).


O autor aprovou também preparo/build e ciclo S3 nativo do dataset de desenvolvimento
em 2026-10-15: 67 objetos enviados/conferidos, reenvio zero uploads/67 reutilizados
e restore `restored_verified` de 66 arquivos/38.202.741 bytes. O verificador
reconciliou 63 partições/19 features/603.669 linhas com política/inventário
baixados e run/hash do manifesto preservados. Não consultou originais/upstream
ou estudo, recomputou features ou produziu nova avaliação de confirmação.
Não houve injeção de falha nativa de storage nem backup do SQLite neste ciclo.
[Evidência nativa](../../references/evidence/development_gold_archive_author_validation_2026-10-15.json).
Este fechamento altera documentação/notebooks e acrescenta evidência; não exige
reexecutar runtime. Permanecem os resultados da preparação: tox `py314`
309 passed/2 skipped/4 warnings e SDK isolado quatro casos aprovados com Stubber.
JSON, links, diff, preservação das células e aplicação protegida foram conferidos.


### Backup offline consistente do estudo HGB

`tests/storage/test_hgb_study_backup.py` cria uma vez um estudo Optuna/SQLite real
com dois trials, três folds, nove fits e tracking simulado, sobre 63 Parquets/
189 linhas. Após setup, treino, recomputação de features e escrita em MLflow são
bloqueados. A preparação verifica snapshot e modelos/predições/recibos sem
carregar modelos. Os bytes da fonte e relatório permanecem; preparo repetido
conserva a identidade. Outra cópia isolada passa após remover fonte/referências,
com o dataset disponível apenas como restore de seu bundle original.

Cobre lock realmente ocupado, metadados faltantes, SQLite corrompido, entradas
fora do perfil (inclusive banco/sidecar aninhados), trials inacabados e mutação
não cooperativa dos artefatos. Um SQLite em WAL com conexão aberta demonstra que
a API inclui linhas já commitadas e deixa snapshot autossuficiente sem WAL.
Casos adversos atualizam checksum físico, digest lógico e recibo de snapshot,
e ainda exigem rejeição de objetivo do trial, métricas, modelo, decisão do
relatório ou vínculo de dataset alterados. Perfil de backup exige callback semântico e mantém bancos restritos. O novo
perfil de transferência roteia o estudo para bucket de artefatos, separado dos
datasets; perfis anteriores mantêm suas fronteiras.

```bash
poetry run tox -e py314 -- tests/storage tests/modeling/experiments/test_temporal_development_data.py tests/modeling/experiments/test_hgb_optimization.py tests/data/datasets/test_gold_dataset.py tests/data/datasets/test_silver_dataset.py tests/data/ingestion/test_handbook_bronze.py tests/serving
```

[Resultados](../../references/evidence/hgb_study_backup_preparation_2026-10-15.json).
Esta preparação usa arquivos/SQLite reais gerados, sem dados Handbook nativos,
Docker, S3, migração de backend ou nova confirmação estatística. A adaptação
controlada de `study_path` ocorre somente no relatório da visão descartável de
verificação; o relatório arquivado e referências de tracking nunca são reescritos.


### Transferência e recuperação do estudo HGB

`tests/storage/test_hgb_study_transfer.py` reutiliza estudo SQLite/artefatos reais
pequenos gerados e S3 de memória, com bucket explicitamente conferido em cada
operação. Cobre manifesto por último, republicação sem uploads, recuperação
após remover fonte/referências/estudo preparado e manter só dependência em restore
isolado. Reconfere trials/fit budget/hash lógico e relatório preservado.

Dependência ausente falha antes da rede; vínculo diferente ou erro semântico
preserva quarentena. Testa retomada após publicação parcial, corrupção no download,
recibos de worker com bucket/prefixo/candidato inválidos e destino final protegido.
SDK isolado Stubber cobre também PUT/checksum/readback/listagem em `fraud-artifacts`.
A regressão inclui os perfis anteriores, sem S3/Docker nativos ou treino adicional
no caminho de backup/recuperação. [Resultados](../../references/evidence/hgb_study_transfer_preparation_2026-10-15.json).
O autor aprovou o preparo offline nativo:
[evidência](../../references/evidence/hgb_study_backup_author_validation_2026-10-15.json).


Em 2026-10-16 o autor aprovou também publicação/reenvio/restore nativos do estudo:
323 objetos, reenvio zero uploads/323 reutilizados e restauração de 322 arquivos,
com 20 trials/63 tentativas, integridade/foreign keys, digest lógico e dependência
preservados. Fonte original não foi lida no restore; modelos não foram carregados,
não houve refit nem migração MLflow. [Evidência nativa](../../references/evidence/hgb_study_transfer_author_validation_2026-10-16.json).

Este fechamento altera documentação/notebooks e acrescenta evidência do autor.
Verificações: aplicação/reaplicação protegidas, índice/HEAD/arquivo pessoal
preservados, rejeição de edições/colisões antes da escrita, links/JSON/notebooks
e diff conferidos. Células de código/outputs e arquivos fora do escopo mantêm
seus bytes. Não houve repetição de runtime para este fechamento documental.
A preparação do transporte registrou regressão selecionada via tox `py314`:
337 passed/2 skipped/4 warnings, Ruff/formatação/lockfile aprovados, e cinco casos
SDK Stubber aprovados no ambiente isolado; isso não é uma nova execução da suíte completa.


### Inventário MLflow somente leitura

`tests/storage/test_mlflow_inventory.py` usa o SDK fixado para criar SQLite MLflow
real, runs ativas/terminadas/apagadas, histórico de métricas, logged model e
artefatos deliberadamente não desserializáveis. Confere ausência de conexão ao
banco original/rede, preservação de bytes, fingerprints estáveis, dados presentes
no WAL, detecção de mutação de banco/artefato/referência/conjunto, recusa de links
simbólicos/journal/schema inválidos, classificação de URIs sensíveis e relatório
privado separado. O teste não treina modelos nem usa o tracking do autor.

```bash
poetry run tox -e py314 -- tests/storage/test_mlflow_inventory.py tests/integrations/test_mlflow_tracking.py tests/integrations/test_serving_release_export.py
```

Os testes de integração exercitam a publicação e exportação já existentes em
fixtures. O inventário não usa esses caminhos para consultar a origem nativa.
O módulo entra na suíte padrão por estar em tests/storage; não exige ambiente
tox adicional, novo serviço ou dependência. Coleta nativa inicial recebida; a
classificação de UUIDs locais foi corrigida e aguarda nova coleta. Migração
PostgreSQL + S3 continua pendente. [Runbook](MLFLOW.md#inventário-antes-da-migração-postgresql--s3).

Preparação: tox `py314` aprovou **28 testes/27 avisos**, incluindo **19 casos do
inventário**, Ruff, formatação e lockfile. [Evidência de preparação](../../references/evidence/mlflow_inventory_preparation_2026-10-16.json).


A correção de classificação de 2026-10-16 remove a inferência de run_id MLflow
pela presença de model_uri irmão. Relatório e auditoria de diagnóstico mantêm
UUID local independente; mlflow_run_id e contextos explícitos ainda detectam
referências ausentes. Tox py314 aprovou **30 testes/27 avisos**, incluindo **21 casos
do inventário**, Ruff/formatação/lockfile. A coleta nativa corrigida e a revisão
do diretório Default continuam pendentes. [Evidência](../../references/evidence/mlflow_inventory_classification_fix_preparation_2026-10-16.json).


O autor executou a versão corrigida do inventário em 2026-10-16: **21 testes,
um aviso em 7,31 s**, coleta completa com zero referências não resolvidas e zero
localizações gerenciadas ausentes. A inspeção do Default informou zero runs/modelos
e diretório ausente; o status bruto review_required foi preservado, com revisão
documentada. [Evidência nativa](../../references/evidence/mlflow_inventory_author_validation_2026-10-16.json).
Este fechamento altera somente documentação/Markdown dos notebooks e acrescenta
essa evidência. Runtime não foi repetido: permanecem os 30 testes/27 avisos da
regressão selecionada anterior, com Ruff/formatação/lockfile aprovados.
O pacote confere aplicação/reaplicação, recusa edições/colisões, preserva índice
Git/HEAD/configuração pessoal e verifica links/JSON/notebooks/diff. Código,
outputs históricos, contratos, dependências e evidências anteriores preservados.


### Preparo e verificação offline do tracking MLflow

`tests/storage/test_mlflow_backup.py` usa a fixture SDK/SQLite real compartilhada
em tests/storage/conftest.py. Cobre unidade acima de 512 arquivos, auxiliares e
diretórios vazios, preservação de IDs/histórico/URIs, idempotência e verificação
após remover fontes/referências. Um WAL com dado somente nele é incluído e a
fonte mantém seus bytes. Nenhum modelo é desserializado no caminho de backup.

Também recusa captura sem declaração de quiescência, Default com conteúdo novo,
artefatos externos não mapeados, registry/novas tabelas sem cobertura, outro banco
ou SQLite disfarçado, colisão de metadados, mutação da fonte, excedente de orçamento,
paths inseguros, symlinks e corrupção semântica mesmo com hashes de arquivos refeitos.

```bash
poetry run tox -e py314 -- tests/storage/test_archive.py tests/storage/test_mlflow_inventory.py tests/storage/test_mlflow_backup.py tests/storage/test_restored_release.py tests/integrations/test_mlflow_tracking.py tests/integrations/test_serving_release_export.py
poetry run tox -e bronze-sdk
```

O SDK isolado usa confcutdir e não carrega a fixture MLflow do diretório pai.
Os novos testes entram no tox padrão; não há ambiente adicional. Preparação com
fixtures é separada do futuro preparo nativo e da recarga/transferência/migração.

Preparação aprovada: tox py314 com **111 passed/1 skipped/27 warnings**, incluindo
37 casos do backup; Ruff/formatação/lockfile aprovados. SDK isolado: cinco casos
aprovados. [Evidência de preparação](../../references/evidence/mlflow_backup_preparation_2026-10-16.json).


### Regressão do backup com dois stores

O preparo nativo anterior parou no store documentado de interface. A correção
acrescenta 12 casos reais SDK/SQLite a `tests/storage/test_mlflow_backup.py`:
dois stores com IDs/artefatos separados e referências conciliadas, idempotência,
verificação sem fontes, WAL auxiliar, mutação auxiliar, budget agregado,
recusa de store ativo/localização ausente ou externa/Default com associações,
banco desconhecido/colisão de metadados/IDs duplicados e corrupção lógica do
segundo banco mesmo com hashes do manifesto refeitos. Os testes anteriores de
um store continuam ativos. O mesmo tox py314 e comando selecionado acima se aplicam.
Isso não representa captura ou recuperação operacional dos bancos do autor.
[Evidência da correção](../../references/evidence/mlflow_backup_multistore_preparation_2026-10-16.json).

Validação desta correção: **123 passed, 1 skipped, 27 warnings** no tox py314
(regressão selecionada, 122,97 s no total), incluindo 49 casos de backup e 21
de inventário. Ruff, formatação de 235 arquivos e lockfile aprovados. O skip é
o check nativo opt-in de release restaurada; não houve suíte global ou Docker.


### Transferência S3 e recuperação do snapshot MLflow

`tests/storage/test_mlflow_transfer.py` acrescenta **16 casos** com stores reais
criados pelo SDK e transporte S3 em memória: publicação/readback/manifesto por
último, reenvio sem uploads, retomada após falha parcial, recuperação com os
originais e bundle local removidos, diretórios vazios e permissões, snapshot local
corrompido antes de S3, objetos ausentes/corrompidos/extras/403, recibos inválidos,
quarentena sem publicação em falha semântica, corrupção lógica SQLite com hashes
externos refeitos e perfil com mais de 512 arquivos. Nenhum modelo é carregado.

A regressão selecionada também cobre os transportes Bronze/estudo e os perfis
Silver/Gold/desenvolvimento, pois o worker é compartilhado. Tox py314: **210 passed,
1 warning**, Ruff, formatação (237 arquivos) e lockfile aprovados. SDK isolado:
**6 passed** com botocore Stubber, incluindo o bucket/prefixo de MLflow. O teste
SDK não carrega a fixture MLflow do diretório pai. Sem Docker ou S3 nativo no assistente.

```bash
.venv/bin/python -m tox -e py314 -- tests/storage/test_archive.py tests/storage/test_mlflow_inventory.py tests/storage/test_mlflow_backup.py tests/storage/test_mlflow_transfer.py tests/storage/test_bronze_transfer.py tests/storage/test_hgb_study_transfer.py tests/storage/test_silver_archive.py tests/storage/test_gold_archive.py tests/storage/test_development_gold_archive.py
.venv/bin/python -m tox -e bronze-sdk
```

O job continua sem imports científicos e sua allowlist de build abrange os quatro
módulos incluídos no handshake. O Dockerfile deve ser reconstruído no host; build
nativo e publicação/reenvio/restore foram aprovados pelo autor no registro posterior.
[Evidência de preparação](../../references/evidence/mlflow_transfer_preparation_2026-10-16.json).
O preparo/verify nativos anteriores são um registro separado:
[70 testes e bundle verificado](../../references/evidence/mlflow_backup_author_validation_2026-10-16.json).


### Validação nativa do ciclo S3 MLflow e fechamento documental

O autor executou os 16 novos casos no tox py314: **16 passed, 1 warning**, 12,34 s
no pytest/18,79 s no tox. Build do job e HTTP 200 do RustFS aprovados; publicação,
reenvio idempotente e recuperação dos dois stores tiveram contagens/hashes
reconciliados nas saídas de terminal. [Evidência nativa](../../references/evidence/mlflow_transfer_author_validation_2026-10-16.json).
O fechamento altera somente documentação/Markdown de notebooks e acrescenta a
evidência. Runtime não foi repetido: seguem os 210 testes selecionados + seis
SDK da preparação e os 16 casos nativos reportados. Aplicador e pacote são
conferidos por aplicação/reaplicação, recusas antes de mutação, preservação de
índice/HEAD/configuração pessoal, links/JSON/notebooks/diff e bytes dos arquivos
não alterados. Células de código, outputs e IDs dos notebooks permanecem intactos.

## Recuperação de histórico e modelo MLflow

```bash
.venv/bin/python -m tox -e py314 -- tests/storage/test_mlflow_inventory.py tests/storage/test_mlflow_backup.py tests/storage/test_mlflow_transfer.py tests/storage/test_mlflow_recovery.py tests/integrations/test_mlflow_tracking.py
```

O tox desativa a telemetria do MLflow antes de importar fixtures. Para execução
direta de pytest, usar `MLFLOW_DISABLE_TELEMETRY=true poetry run pytest -q tests/storage/test_mlflow_recovery.py`.
Os testes geram stores/modelo de controle; não leem datasets nativos ou serviços.
Cobrem URIs `runs:/` e `models:/`, histórico de métricas, originais removidos,
restores preservados, hash externo antes da carga, alteração inesperada no mapa,
falhas/timeouts/limpeza e bloqueios de I/O no worker. O subprocesso é parte real
da validação de recarga. O ensaio nativo usa os mesmos verificadores, foi aprovado pelo autor e é comandado
explicitamente pelo [runbook](MLFLOW.md#ensaio-descartável-de-recuperação-mlflow).

A regressão da assinatura histórica inclui entradas `double` para todos os
preditores e assinaturas mistas nas duas URIs de modelo, além de contagens cuja
conversão para float64 seria inexata. Esse caso faltava às fixtures iniciais.
O comando tox da seção anterior cobre também essa correção. A retentativa sobre
os restores foi aprovada pelo autor: 22 testes passaram, 21 avisos em 23,72 s
(tox OK em 29,18 s), seguidos do ensaio com erro de score zero.
[Resultado e limites](MLFLOW.md#recuperação-mlflow-validada-pelo-autor--2026-10-16).

## Inventário de persistência do checkout

```bash
.venv/bin/python -m tox -e py314 -- tests/storage/test_persistence_inventory.py
```

14 testes usam checkouts/arquivos gerados. Cobrem a inclusão de `data/storage/` e
arquivos soltos, classificação de runtime mesmo no índice Git, ausência de leitura
de conteúdo por `Path.open`, exclusões explícitas sem esconder caches dentro de
dados, links/erros de percurso, bloqueio de escrita por link, catálogo inválido e
produtores ausentes. Não iniciam serviços, abrem bancos ou carregam modelos.
O resumo nativo precisa ser informado separadamente. Arquivo de teste novo; testes
científicos e produtores anteriores preservados.

## Contenção inicial dos destinos de escrita

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py \
  tests/modeling/contracts/test_model_interface.py \
  tests/monitoring/test_statistics_and_exporter.py \
  tests/serving/test_docker_runtime_checks.py
```

O teste de paths verifica recusas antes de treino, Docker e criação de credenciais;
paths relativos/absolutos, áreas irmãs, travessia, symlinks e componentes inválidos.
As chamadas internas de integração mantêm seus diretórios temporários. Sem serviços
externos nem necessidade de repetir ensaios nativos pesados. Ver cobertura parcial
em [Storage](STORAGE.md#destinos-de-escrita--primeira-cobertura-operacional).

Validação no assistente: **24 testes novos** e **82 testes na seleção final do tox**,
com Ruff, formatação e lockfile aprovados. A suíte ampla aprovou 941 testes e
identificou uma asserção antiga que fixava o catálogo em 12 entradas. A entrada de
`data/reports` tornou essa contagem obsoleta; o teste agora confere cobertura e
classificação. Ele passou na seleção final. Não houve mudança de produção depois
da execução ampla. A suíte ampla não foi repetida após essa correção somente no
teste. [Registro](../../references/evidence/managed_output_paths_preparation_2026-10-16.json).

## Serviço MLflow isolado: controle, runtime e prova nativa

```bash
# Sem PostgreSQL, RustFS ou Docker reais; os testes reais ficam ignorados:
.venv/bin/python -m tox -e py314 -- tests/tracking

# Opcional para manutenção do runtime: instala o lock operacional separado.
# Servidor HTTP real com SQLite/arquivos descartáveis; não prova PostgreSQL/S3:
.venv/bin/python -m tox -e tracking-runtime

# Depois de subir o laboratório: cria uma run e recria somente os containers dele.
.venv/bin/python -m tox -e tracking-lab
```

`tracking-lab` e `tracking-runtime` não integram `env_list`; nenhum serviço é
interrompido pela suíte padrão. O teste real exige o opt-in configurado no ambiente
correspondente. [Passos, recibo e limites](../../infra/tracking/README.md).
As conferências SQL são somente leitura e escopadas à run; os SDKs do worker e do
servidor estão fixados no lock operacional, sem mudança de `poetry.lock`.

### Regressão do canal JSON do worker

`tests/tracking/runtime/test_http_proxy.py` executa a CLI em subprocessos contra
um servidor HTTP real descartável, com a impressão de URLs do SDK habilitada.
Exige que stdout inteiro seja um JSON, URLs fiquem em stderr e a leitura após
reiniciar o servidor reproduza o registro. Antes da correção, reproduz o
`JSONDecodeError` observado pelo autor. O teste anterior chamava funções no mesmo
processo e não cobria esse contrato de comunicação.

Os testes offline em `tests/tracking/test_tracking_lab.py` cobrem mensagens na
construção do cliente e no encerramento da run, saída vazia em exceção e recusa
de stdout contaminado/concatenado. Executar com `py314 -- tests/tracking`;
`tracking-runtime` habilita a prova HTTP com dependências operacionais isoladas.
O teste nativo `tracking-lab` continua sendo necessário para PostgreSQL/S3 e
recriação de containers. [Retomada e limites](../../infra/tracking/README.md#retomar-após-a-falha-no-canal-json--2026-10-16).

### Gate nativo PostgreSQL/S3/proxy aprovado — 2026-10-17

Após a correção do canal JSON, o autor aprovou o teste existente
`tests/tracking/live/test_tracking_rehearsal.py`: **1 passed in 21.56s**; tox
`tracking-lab` OK em 23,79 s. SQL e S3 independentes e proxy foram conferidos
antes e depois de recriar os containers; IDs diferentes, mesmas imagens e volume.
O recibo `rehearsal-71acfcf3daca43348a02baa1d3cd27af.json` fica em
`data/storage/tracking-lab/receipts/`.

Este fechamento altera somente documentação, narrativa acrescentada aos notebooks
e evidência relatada pelo autor. Não repete o teste live nem a suíte científica.
Foram conferidos JSONs, links locais, preservação de células históricas, hashes,
diff e aplicação/reaplicação do pacote sem alterar o índice Git. O gate aprovado
cobre uma run e um JSON operacional; modelo pelo novo servidor, migração e backup
PostgreSQL exigem testes específicos.
[Evidência e limites](../../references/evidence/tracking_lab_author_validation_2026-10-17.json).

### Produtor de modelo HTTP: regressão e aceite nativo

```bash
# Regressão offline, inclusive backend local existente:
.venv/bin/python -m tox -e py314

# Servidor HTTP real com backend/artefatos descartáveis, sem Docker/RustFS:
.venv/bin/python -m tox -e contribution-http

# Escreve no laboratório existente e verifica recarga/roteamento local:
.venv/bin/python -m tox -e tracking-contribution
```

Os dois ambientes novos são opt-in, fora de `env_list`. A prova HTTP executa a
CLI em subprocesso e lê stdout inteiro como JSON; retira a saída local do caminho
e recarrega pelo proxy. Também testa indisponibilidade sem fallback, retorno local
explícito e run `FAILED` quando a paridade falha. Os testes offline cobrem URI,
exclusividade, restauração de contexto e recusa de artefatos fora do proxy.

O aceite nativo confere configuração/saúde dos dois serviços, metadados, recarga
independente do modelo com a saída local temporariamente renomeada e restaurada,
paridade e roteamento local em store descartável. Confere containers inalterados
e preserva recibo mesmo em falha. Não reinicia serviços ou treina candidatos
Handbook. [Procedimento, campos e limites](../modeling/CONTRIBUTING_MODELS.md#primeiro-produtor-http-no-laboratório).

### Regressão da seleção automática de multipart no S3

O fixture HTTP original usava arquivos locais e não anunciava a capacidade de
download direto do S3. A primeira tentativa nativa expôs esse ponto: cinco arquivos
listados, cinco downloads por chunks falharam, antes da recarga independente e do
rollback local. Os 79 testes nativos de regressão permaneceram aprovados.

`tests/tracking/runtime/test_model_contribution_http.py` acrescenta um gateway
loopback diante do MLflow real: o endpoint de capacidades anuncia multipart e o
destino assinado devolve erro. Antes da correção, a CLI falhou com a mesma classe
de erros por arquivo. Com a política explícita, exige sucesso, paridade, GET do
modelo no proxy e ausência de pedidos a URLs assinadas/destino direto. O teste
usa apenas SQLite e arquivos descartáveis; não cria serviços ou objetos RustFS.

Os testes offline verificam também restauração de flags presentes/ausentes e
flags anteriores `true`, inclusive em exceção. Executar `tox -e contribution-http`
para a prova HTTP e `tox -e tracking-contribution` para a retentativa nativa.
[Diagnóstico e limites](MLFLOW.md#download-por-chunks-e-capacidades-s3--correção-de-2026-10-17).

### HGB: identidade do destino e transporte HTTP

`tests/modeling/experiments/test_hgb_tracking_destination.py` e a extensão da
suíte HGB conferem URI explícita, compatibilidade do formato local, identidade de
retomada, orçamento e recusa de mudança de destino/proveniência antes de novos
fits. Os testes HTTP usam o mesmo servidor descartável dos testes de contribuição,
agora em `tests/tracking/runtime/conftest.py`.

`tox -e hgb-http` exercita modelo HGB/skops, métricas, recarga sem modelo local,
reuso sem fit/publicação, erro de servidor e erro de paridade. Há também execução
do harness nativo contra o fixture HTTP com inspeção Docker substituída; isso
valida o recibo, sem alegar PostgreSQL/RustFS. `tox -e contribution-http` mantém
a regressão do produtor já aprovado.

`tox -e tracking-hgb` é opt-in e fica fora de `env_list`: um fold sintético
no serviço existente, com recibo, sem busca Optuna ou recriação de containers.
O fold HGB nativo foi aprovado pelo autor em 2026-10-17. [Comandos e escopos](HGB_TRACKING.md).

### Três baselines pelo tracking HTTP

A suíte de destinos em `tests/modeling/experiments/test_baseline_tracking_destination.py`
confere opções exclusivas, comportamento local anterior e contenção da nova saída
HTTP antes do fit. A regressão existente continua cobrindo seleção, histórico,
tracking local e falhas de publicação.

`tox -e baseline-http` usa servidor MLflow real com SQLite/arquivos descartáveis:
três modelos pelo CLI, recarga sem saídas locais, falha de paridade no segundo
modelo preservando o primeiro ID, ausência de fallback e retorno local explícito.
O harness nativo também é executado nesse fixture com apenas a inspeção Docker
substituída, sem alegar validação nativa PostgreSQL/RustFS.

`tox -e tracking-baseline` é opt-in e fora de `env_list`. Ele executa o CLI com
experimento sintético separado, registra dummy/regressão/HGB e verifica os três
modelos sem arquivos locais disponíveis. Não repete Handbook/Optuna nem altera
containers. Recibo preservado em sucesso/falha. O aceite nativo do trio foi aprovado em 2026-10-17.
[Procedimento completo](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).

### Aceite nativo dos três baselines

`tracking-baseline` foi aprovado pelo autor: 1 teste, 3 avisos de dependência,
6,55 s (tox OK em 19,96 s). As três runs e métricas foram conferidas; os modelos
foram recarregados sem saídas locais disponíveis, com erro zero. Containers e
volume preservados; sem tracking SQLite novo.
[Evidência](../../references/evidence/baseline_tracking_author_validation_2026-10-17.json).

O fechamento é documental e não altera fontes, testes, parâmetros ou serviços.
Não exige repetir o teste nativo ou a suíte científica. O sucesso não é um teste
de restauração PostgreSQL, perda de volume, migração histórica ou promoção.

### Revisão de fontes dos consumidores de tracking

A suíte de `test_persistence_inventory.py` verifica o modo explícito de consumo:
fonte com código que falharia se importado é somente analisada; leitura de dados,
banco/modelo ou `.env` é rejeitada pelo teste. Mudança de hash, nova fonte com
chamada observada, função ausente, duplicação e link simbólico invalidam a revisão.
O modo anterior de inventário mantém sua garantia de somente metadados.

A revisão real do catálogo é exercitada sem serviços ou credenciais. Não há
novo teste live, job de dump ou restauração PostgreSQL nesta etapa.
[Comandos e critérios futuros](TRACKING_RECOVERY.md).

### Captura PostgreSQL/S3 da bancada

`tests/tracking/test_capture.py` cobre limites de subprocessos binários reais,
falhas/interrupções, restart do container original, recibos e verificação offline.
`tracking-capture-sdk` exercita o SDK fixado com Stubber e a construção SQL com
transporte simulado; não requer serviços e não integra o lock científico.
O módulo SDK é pulado quando suas dependências não estão instaladas.

`tracking-capture` é opt-in, fora de `env_list`. Declara ausência de outros
escritores, pausa apenas MLflow e captura a bancada; nunca roda no teste padrão.
Ele valida a captura e o retorno à saúde, **não um restore**. Preparar a imagem
com `python -m scripts.tracking.capture build`. Procedimento completo em
[TRACKING_RECOVERY.md](TRACKING_RECOVERY.md).

### Restore isolado PostgreSQL/S3

`tests/tracking/test_restore.py`: isolamento, controle anterior ao alvo, falhas e
cleanup. `tests/tracking/capture_sdk/test_restore_worker.py`: integridade e história
API com transportes simulados, no ambiente SDK já existente. O novo
`tests/tracking/runtime/test_restore_client.py` usa MLflow HTTP real com dados
sintéticos e testa hash/identidade/colunas/scores divergentes; continua sem Docker
ou PostgreSQL nativo. `tracking-restore` é o teste live opt-in, fora de env_list,
que usa a captura aprovada, cria e remove só o alvo temporário.
Não executar novo treino ou nova captura para esse gate.
[Procedimento](TRACKING_RECOVERY.md).

### Aceite nativo do restore em 2026-10-19

`tracking-restore`: 1 teste aprovado em 20,89 s (tox 23,30 s), com 38 objetos,
8 runs, 6 modelos e 44 pontos históricos conciliados; recarga de 20 scores com
erro zero, fonte/captura preservadas e cleanup concluído. SDK: 56 aprovados,
1 pulado (fixture opcional de binários PG locais). O teste Docker é explícito;
não faz parte da suíte padrão. O fechamento documental não exige repeti-lo.
[Evidência do autor](../../references/evidence/tracking_restore_author_validation_2026-10-19.json).

### Primeiro ensaio de CD/rollback do serving

**Ensaio blue-green aprovado pelo autor em 2026-10-19:** troca A → B → A pela
mesma URL NGINX, pacote inválido bloqueado e falha de B observada como HTTP 502.
O retorno a A foi verificado em **3,10 s**, com score idêntico e entradas intactas;
os recursos temporários foram removidos. B usa os mesmos pesos e imagem de A.
O gate Docker está concluído neste recorte. O build e a exportação da entrega
versionada também foram aprovados nativamente; o consumo do arquivo em outro
alvo tem aceite nativo informado pelo autor; CI remota aprovada no aceite abaixo. Não há aprovação de novo modelo, CD de produção ou zero downtime.
[Procedimento e limites](SERVING_ROLLOUT.md).

Aceite nativo: `py314 -- tests/serving` aprovou **110 testes**, com **2 opt-in
ignorados**, pytest 4,28 s e tox 6,94 s; Ruff e lockfile aprovados.
`serving-rollout` executou o teste Docker: **1 aprovado em 21,42 s**, tox 23,90 s.
Os gates exigem identidade, score e HTTP 422; a sequência inclui bloqueio do
pacote inválido, troca, falha 502 e retorno pela mesma URL. O valor de 3,10 s é
uma observação local, não um SLA. Não é necessário repetir o teste para aplicar
este fechamento documental. [Evidência](../../references/evidence/serving_rollout_author_validation_2026-10-19.json).

### Imagem e pacote de entrega vinculados ao commit

`tests/serving/test_delivery_gate.py` cobre origem divergente, alterações de
entradas commitadas e preservação do índice pessoal, build único com bases
fixadas, rótulo/plataforma incorretos, falha HTTP/rollout/export/cleanup, mudança
da origem ou imagem antes da exportação, ausência de descritor de sucesso e
verificação offline contra adulteração de arquivos/descritor. O contrato do
workflow exige dependência de quality, hash esperado, artifact após os gates e
permissões somente de leitura. Não há Docker no teste unitário.

`tests/serving/live/test_delivery_gate.py` e tox `serving-delivery` são opt-in:
exigem entradas commitadas e Docker Linux 28+, constroem uma imagem e executam
HTTP/rollback reais com fixture sintética. A execução CI usa o mesmo controller
diretamente após a suíte completa. O autor aprovou o gate local após a correção de permissões, conforme o fechamento
abaixo. A execução GitHub Actions continua pendente; a prova anterior da
referência não é reutilizada como aceite de outra imagem.
[Procedimento](SERVING_ROLLOUT.md#entrega-versionada-na-ci--implementação-de-2026-10-19).

### Regressão da permissão de leitura da fixture

A falha nativa do primeiro `serving-delivery` expôs uma pré-condição não coberta
pelo mock do checker: JSONs atômicos 0600 não são legíveis pelo UID 10001.
Os novos casos em `test_delivery_gate.py` exigem modos de mount adequados antes
do checker, sob umask 022/077, bytes preservados e origem sem chmod; um terceiro
caso rejeita `verified_reference` antes da normalização. O teste nativo continua
sendo o aceite de acesso real no container, sem relaxar UID, mount ou hashes.
[Diagnóstico e correção](SERVING_ROLLOUT.md#correção-das-permissões-da-fixture--2026-10-19).

### Aceite nativo de build e exportação

`serving-delivery`: **1 teste aprovado em 62,04 s**, tox OK em **64,51 s**.
O ensaio verificou build da imagem do commit esperado, HTTP e blue-green com
fixture sintética, exportação da mesma imagem, hashes/recibos do pacote e cleanup.
O descritor foi novamente conferido pelo autor contra o hash impresso pelo teste.
A correção da fixture para o UID 10001 passou no fluxo completo.

O gate local está concluído. Não é necessário repetir teste ou build para aplicar
este fechamento documental. A suíte de regressão do assistente (184 aprovados e
3 opt-in ignorados) mantém seu escopo separado da execução Docker. A execução
remota do workflow e a recarga do tar em outro alvo continuam sem aceite.
[Evidência](../../references/evidence/serving_delivery_author_validation_2026-10-19.json).

### Consumo da entrega e transferência entre jobs

`test_delivery_consumer.py` cobre hashes inválidos antes de Docker, colisão de
tag sem sobrescrita, identidade/plataforma/revisão divergentes, carga parcial,
falha HTTP/cleanup e mutação da origem. Confere preservação de bytes/permissões
da entrega e de outra tag para o mesmo ID. O contrato CI exige ID do artifact e
hash nos outputs do produtor, outro job e ausência de rebuild. Docker é
controlado nesses testes; eles não constituem aceite nativo ou remoto.

`tests/serving/live/test_delivery_consumer.py`, tox `serving-consumer`, carrega
o pacote nativo já aprovado e executa o checker HTTP real em container novo.
Não constrói imagem nem gera fixture por fit; não depende do tracking. O mesmo
engine pode conter camadas/imagem em cache, condição registrada no recibo.
O workflow consome o artifact em outro runner; apenas a execução no GitHub
poderá fechar essa prova. O aceite nativo foi informado pelo autor; o aceite GitHub Actions continua pendente.
[Comandos e contrato](SERVING_ROLLOUT.md#consumo-da-imagem-arquivada-e-ci-entre-jobs--2026-10-19).

### Consumo nativo aprovado — 2026-10-19

O autor aprovou `serving-consumer`: **1 teste em 32,41 s**, tox OK em 44,93 s.
Imagem recarregada do arquivo já aprovado, identidade/score HTTP e 422 verificados,
sem rebuild ou fit; origem preservada e recursos próprios removidos. O ID da
imagem estava ausente antes do load, sem alegar cache vazio ou engine separado.
**Três jobs do GitHub Actions aprovados; próximo: promoção explícita e provisionamento**, sem repetir
os gates locais encerrados. [Resultado e limites](SERVING_ROLLOUT.md#aceite-nativo-do-consumo-da-imagem--2026-10-19).

### CI de entrega e consumo aprovada — 2026-10-19

A [execução 39306262564](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/39306262564)
passou nos três jobs do commit `6a3782b0ccb9d6c0a098251743483aed73e49079`:
qualidade → build/qualificação → consumo do artifact em outro runner. A API
confirma sucesso e runners distintos; o artifact pequeno do consumidor foi
baixado, teve digest conferido e seus recibos de recarga/HTTP/cleanup lidos.
**Gate remoto encerrado.** Próximo: promoção explícita e provisionamento
reproduzível KinD/Helm; registry e deploy permanente ainda têm critérios próprios.
[Resultado e limites](SERVING_ROLLOUT.md#aceite-do-fluxo-no-github-actions--2026-10-19).

### Saúde de dados e features persistidas

`tests/monitoring/test_feature_health.py` cobre suporte e sobreposição, cálculos
fechados de Jensen–Shannon, valores ausentes/inválidos, coerências Gold, relógios
com nanosegundos e labels limitados. Testa o módulo do exporter sem site-packages.
A integração cobre leitura do checkpoint preservando bytes, baseline estável,
janela recente limitada, saída gerenciada imutável e erro de qualidade distinto
de indisponibilidade do coletor. Não constitui aceite do checkpoint do autor,
Docker, promtool ou painel real.

Executar o tox padrão com `tests/monitoring`, `tests/storage/test_managed_paths.py`
e `tests/storage/test_persistence_inventory.py`; depois os comandos de demo e
snapshot do [runbook](FEATURE_MONITORING.md). A demonstração é gerada e não faz
fit ou injeta alterações na fonte nativa. Sem alteração de lock/dependências.

### Aceite nativo mínimo do monitoramento de features — 2026-10-19

O autor confirmou 116 testes aprovados e um opt-in ignorado, demonstração gerada,
snapshot nativo, exporter reconstruído, oito regras válidas e coleta Prometheus.
Os valores do dashboard foram conciliados com o snapshot: 1.000 decisões por
janela sem sobreposição, contagens de qualidade/relógio zeradas e quatro
divergências entre 0,001457 e 0,004398. O limiar descritivo é 0,1.
O aceite cobre esse recorte operacional; não mede concept drift, desempenho
futuro ou frescor de ingestão externa e não dispara CT/promoção. O firing dos
novos alertas não foi ensaiado na bancada. A contagem recebe cor fixa neutra,
com conferência visual após refresh ainda pendente. Segue o provisionamento
incremental do fluxo completo em KinD + Helm.
[Procedimento, explicação conceitual e evidência](FEATURE_MONITORING.md#aceite-nativo-mínimo--2026-10-19).

### Fundação KinD/Helm — preparação de 2026-10-20

Primeiro incremento do fluxo completo: cluster dedicado de um node Kubernetes
1.35.8 fixado por digest, chart com RustFS/PostgreSQL e dois PVCs novos. Imagens
dos serviços preservadas dos pins já utilizados. Kubeconfig, caches, credenciais
privadas e recibos sob `data/storage/kind-platform/`; contexto explícito.
O ensaio grava marcadores próprios SQL/S3 e exige releitura após recriar os pods
com os mesmos PVCs. A execução nativa ainda está pendente. Não migra os dados
históricos, não troca o tracking e não comprova backup ou perda do cluster.
[Runbook, limites e comandos](KIND_FOUNDATION.md).

### MLflow no KinD — aceite nativo de 2026-10-20

O autor aprovou a regressão (135 testes, 1 live ignorado) e o gate nativo
(1 teste em 21,56 s). Publicação e recarga independente pelo proxy, hash do modelo
no RustFS e nova recarga após recriar o pod MLflow foram aprovados, com erro de
score zero e identidades da fundação preservadas. O Deployment e a imagem
permaneceram os mesmos; o pod ganhou novo UID. [Evidência e limites](KIND_TRACKING.md#aceite-nativo--2026-10-20).

Próximo recorte: objetos/dados no RustFS do KinD, com conferência de identidade
e leitura, preservando as fontes até aceitar o destino. O fluxo completo segue
em andamento; não houve migração histórica, refit histórico, promoção ou
backup/restore deste novo destino.

## Gate de datasets no KinD

Regressão offline: `tests/infra/test_kind_datasets.py` e
`tests/infra/test_kind_dataset_layers.py`. Usa S3 em memória, bytes opacos Bronze
e Parquet sintético com os verificadores existentes das quatro camadas. Cobre
reenvio, corrupção, bucket sem ownership, manifesto por último, fonte local
ausente na restauração e limpeza das credenciais temporárias após falha.

O opt-in `.venv/bin/python -m tox -e kind-datasets` usa os quatro bundles já
preparados e a fundação existente. Publica, repete e restaura cada camada;
o gate permanece pulado na suíte comum. Aceite nativo do autor: 1 teste passou
em 66,78 s; tox concluiu em 81,18 s, com as quatro camadas aprovadas. A regressão
offline de preparação teve 69 testes aprovados no ambiente do assistente; isso
não substitui a execução nativa. [Recibos, resultado e limites](KIND_DATASETS.md#aceite-nativo--2026-10-20).

## Jobs de dados no KinD

`tests/infra/test_kind_pipeline_workers.py` exercita produtores/verificadores
reais com fixtures sintéticas e S3 em memória; verifica a cadeia e recupera
saídas após remover projetos e áreas de trabalho. O inventário congelado é
conferido antes de desserializar pickle. `tests/infra/test_kind_pipeline.py`
renderiza o chart com Helm real, verifica ausência de credenciais na transformação,
imutabilidade do Job Silver no upgrade e interrupção antes de Gold em caso de falha.

O opt-in `.venv/bin/python -m tox -e kind-pipeline` usa o snapshot congelado no
RustFS do KinD, executa dois Jobs e restaura suas novas saídas. É pulado na suíte
comum; seu aceite é separado dos testes offline e da renderização dos templates.
Resultado nativo do autor em 2026-10-20: 1 teste passou em 63,98 s; tox concluiu
em 80,06 s. Ambos os Jobs, ligação de bundles, restores, contagens, imagens e
limpeza da release temporária foram aprovados. A regressão offline teve 74 testes
aprovados no ambiente do assistente; não foi inferida como execução nativa.
[Resultado, recursos e diagnóstico](KIND_PIPELINE.md#aceite-nativo--2026-10-20).

### Gate de checkpoint do replay no KinD

`tests/infra/test_kind_replay.py` executa interrupção real de processo com
transação SQLite aberta, retomada e comparação integral com referência sintética.
Também confere os templates Helm reais e rejeita identidades/artefatos alterados.
`tox -e kind-replay` executa o gate opt-in no cluster existente, recria somente
o pod do ensaio e preserva o PVC. [Procedimento](KIND_REPLAY.md).

Aceite nativo do autor em 2026-10-20: 1 teste passou em 17,89 s, tox em 33,80 s.
O pod foi recriado durante `feedback:2`, com recuperação da transação incompleta,
mesmo PVC e igualdade de todas as tabelas. O fixture terminou com 20 eventos,
10 scores e 6 alertas; somente quatro scores foram calculados após a retomada.
A regressão de preparação (181 testes/4 live ignorados) pertence ao ambiente do
assistente; não foi inferida como nova execução nativa.
[Evidência e limites](KIND_REPLAY.md#aceite-nativo--2026-10-20).

### Serving no KinD e aposentadoria da instalação sintética

O live gate histórico da release `fraud-serving` foi aposentado após a retirada
nativa. Seu aceite original está preservado em [KIND_SERVING.md](KIND_SERVING.md).
Os testes exclusivos do controlador/instalador antigo foram removidos.

A segurança do chart compartilhado continua em
`tests/operational_round/test_controller_and_templates.py`. Os gates HTTP reais,
identidade de readiness/info/score, rejeição de input e convergência/retry ficam
em `tests/serving/test_docker_runtime_checks.py`. Atualizações, recuperação do
seeder, causalidade e transações de promoção conservam suas suítes próprias.
A CI mantém `serving-delivery` e `serving-consumer`.

O gate diário `platform-check` confere o estado atual e recusa divergência ou
corrida de identidade, sem declarar um novo ciclo de parada/subida. Seus cenários
estão em `tests/infra/test_kind_operations.py`. Não substitui `platform-verify`.

### Observabilidade no KinD

`tests/infra/test_kind_observability.py` verifica cópia SQLite consistente,
rejeição de fonte ativa/alterada, hash/limite/atomicidade do transporte, contratos
Helm reais, mounts somente leitura, importação por plataforma e controlador sob
chamadas controladas. Reutiliza fixture sintética dos testes de monitoramento.
Não acessa fontes nativas ou Docker/KinD.
`tox -e kind-observability` é opt-in: consulta exporter/Prometheus/Grafana reais,
verifica provisão e recria somente o exporter, preservando identidades e estado.
[Runbook](KIND_OBSERVABILITY.md). Aceite nativo registrado abaixo.

Aceite nativo de observabilidade em 2026-10-21: **1 teste em 62,64 s**, tox OK
em 73,91 s. Bytes e summary conciliados, scrape e oito regras conferidos,
proxy Grafana e quatro dashboards aprovados antes/depois da recriação do
exporter, com mesmos PVCs e demais recursos preservados. O autor confirmou
posteriormente a conferência visual; ela é separada do gate automatizado.
Não houve novo teste de firing, backup/restore ou reboot.
[Evidência](../../references/evidence/kind_observability_author_validation_2026-10-21.json).

Refinamento visual aprovado em 2026-10-21: [política semântica de cores](DASHBOARD_COLORS.md#aceite-da-política--2026-10-21),
com informações azuis e estados explícitos em texto/cor. Configuração verificada
pela API antes/depois de recriar o exporter e conferência visual confirmada pelo
autor. Métricas, arquivos de origem/cópia e três PVCs iguais ao aceite anterior.
Próximo: operação de parada/subida, retomada após reboot e fechamento integrado.

O recibo atualizado identifica `dashboard_color_policy_verified=semantic_colors_v1`
nas duas verificações. Duração do controlador: 62,36 s; tempos pytest/tox não
foram fornecidos neste anexo. Regra de cores e apoio da legenda não alteram
consultas, unidades ou resultados. Sem necessidade de repetir testes para
aplicar somente este fechamento documental.


## Gate operacional opt-in do KinD

O controlador atual usa baseline canônico v2 e o gate compartilhado de acesso à
API ativa. Testes cobrem active blue/green, inativo zero/ausente, ownership,
ponteiro/alias divergentes, contagens salvas, staging recusado e recibos v1
preservados. O aceite nativo anterior abaixo corresponde ao laboratório inicial;
o ciclo canônico deve gerar sua própria evidência.

Regressão sem Docker: `poetry run python -m pytest -q tests/infra/test_kind_operations.py`. O ambiente tox `kind-operations` exige `FRAUD_KIND_NO_OTHER_WRITERS=1` e `FRAUD_KIND_TRACKING_REPORT` explícitos. Interrompe e retoma o cluster próprio; não deve entrar na CI comum. O teste live permanece skipped na suíte padrão. Reboot manual tem gate separado `verify --expect-reboot`; ambos os gates nativos foram aprovados em 2026-10-21. O ciclo teve 1 teste em 252,23 s; o reboot foi uma verificação CLI separada. Mocks não comprovam persistência nativa. [Comandos e limites](KIND_OPERATIONS.md).


## Entradas Make e acessos locais

Os testes `tests/infra/test_platform_entrypoints.py` executam GNU Make com um Poetry gravador, verificam falhas/precondições/ordem e usam doubles dos forwards para testar ownership e limpeza por Ctrl+C. Não abrem Docker/Kubernetes. Execute `poetry run tox -e py314 -- tests/infra/test_platform_entrypoints.py` no ambiente nativo. Os 18 testes locais de preparação passaram em Python 3.14.4 usando GNU Make e bibliotecas padrão, com conferência adicional em Python 3.12.14. O acesso aos serviços reais e o perfil tox nativo permanecem pendentes para este incremento. A validação de cores, operações e serviços anteriores não é reaberta.


## Atualização recorrente — retenção nativa aceita

Em 2026-10-22, `successor-v1` e `optimized-v1` retornaram `retain_champion`.
Inputs, artefatos e métricas recalculadas dos três candidatos por rodada foram
conferidos em 67.255 linhas com rótulos maduros. Nenhuma mudança de champion
foi reportada. A rodada fixa incluiu `active_identity_verified` via HTTP; a
otimizada não forneceu uma nova medição de latência.
[Recibo e limites](../../references/evidence/model_updates_author_validation_2026-10-22.json).
Os 259 testes passados, 4 skipped e 42 warnings informados pelo autor verificam
a suíte de implementação; não substituem o aceite nativo de ativação de um
sucessor com pesos diferentes, rollback e recuperação, ainda pendente.
Este fechamento muda somente documentação/evidência; não requer novo treino.


## Fluxo do produto via HTTP

`tests/product_flow` usa FastAPI/Uvicorn real em loopback, eventos e modelo
sintéticos, fonte SQLite somente de leitura e o mesmo algoritmo causal/triagem.
Cobre reconciliação, pausa/retomada, duplicatas, falhas 503/422/conexão, identidade
incorreta, interrupção entre HTTP e commit, adulteração de requests/responses/
triagem, ausência de carregamento de pesos no cliente e comandos GNU Make.
A suíte local não possui daemon KinD. O teste live é skipped por padrão.

A preparação exige código/política commitados e fonte concluída. Após
`make product-prepare PRODUCT_NO_OTHER_WRITERS=1`, execute
`FRAUD_PRODUCT_FLOW_NO_OTHER_WRITERS=1 .venv/bin/python -m tox -e product-flow`.
O teste chama a API já instalada, pausa e retoma o ledger próprio, confere
Registry/rota/HTTP e publica um recibo somente depois dos gates finais.
Não modifica workloads, pesos, aliases ou o replay fonte. [Runbook](PRODUCT_DELIVERY.md#executar-eventos-pela-api-do-champion).


## Retomada idempotente da release

O ensaio nativo do produto passou com 1 teste em 493,00 s; consulte a
[evidência do autor](../../references/evidence/product_http_flow_author_validation_2026-10-22.json).
O anexo traz a saída do teste e cinco casos, sem o rehearsal.json completo;
não se inferem contagens totais ou métricas de qualidade a partir dessa amostra.

`tests/serving_recovery` verifica release igual sem regravação, release parcial,
interrupção da cópia e da publicação atômica, recusa de divergências/links,
aprovação, recusa de estado de terceiros, retomada da publicação de current.json
e leitura de recibo/ledger HTTP antigo após a transição operacional exata.
O teste da especificação do Job usa doubles; o reinício real é validado no KinD
por `serving-recovery-prepare`, antes de qualquer alteração do slot ativo.
Não é necessário repetir o replay completo ou treinar outro modelo.
[Procedimento](SERVING_RECOVERY.md).


### Aceite nativo da manutenção do instalador — 2026-10-22

O autor retomou o Job existente sem novo build, aplicou a portadora com o digest
revisado e executou `serving-recovery-verify` e `product-cases`. Resultado:
`serving_seed_maintenance_applied` e `serving_seed_recovery_verified`, versão 1
no blue, mesmo hash de modelo e release, entrada inválida recusada com HTTP 422
e cinco casos consultáveis. O smoke HTTP mediu p95 de 91,33 ms em 20 requisições;
essa amostra funcional não constitui teste de carga nem SLA.

Regressão local da correção: 361 testes aprovados, 5 ignorados e 12 subtests,
com Ruff aprovado. O ensaio isolado valida reinício do init container no mesmo
volume; este aceite não executou outro reboot do host. A promoção recorrente com
pesos diferentes e rollback continua pendente de candidato qualificado.
[Evidência do autor](../../references/evidence/serving_seed_recovery_author_validation_2026-10-22.json).


## Explicações SHAP dos casos HTTP

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/case_explanations tests/serving_recovery tests/product_flow \
  tests/model_updates tests/operational_round tests/evaluation/test_validation_diagnostics.py \
  tests/serving tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py tests/infra/test_platform_entrypoints.py
```

Usa HTTP real com release sintética e SHAP real. Confere contribuições iguais ao
diagnóstico existente, score registrado, aditividade, seleção sem rótulos, fontes
inalteradas, integridade do relatório e digest externo. Divergência de modelo,
score, requisição, ledger, arquivos, links ou mudança da API bloqueia publicação.
A verificação concluída não carrega modelo. O ensaio opt-in `case-explanations`
usa os casos persistidos do champion no KinD e não treina nem repete o replay.
[Procedimento e entrega](CASE_EXPLANATIONS.md).


### Aceite nativo SHAP por caso — 2026-10-22

O ensaio `case-explanations` publicou cinco casos e retornou
`case_explanations_verified` na verificação offline, sem carregar modelo.
Pesos/release do champion foram preservados. O HTML fornecido contém 19 valores
e contribuições por caso; as somas e a sigmoide conferem na precisão exibida.
O console confirma o digest `1f780b2d7eb9de7a553435f3ccd69ede37eca735cbdddae6dadbb01981e0d91c`.
O anexo não inclui os SVG/JSON/manifest, nem o resumo final de quantidade/duração
do pytest; o aceite registra exatamente as saídas de geração/verificação fornecidas.
[Evidência](../../references/evidence/case_explanations_author_validation_2026-10-22.json).


### Portal de investigação — preparação

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/fraud_portal tests/case_explanations tests/product_flow \
  tests/serving_recovery tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py tests/infra/test_platform_entrypoints.py &&
.venv/bin/python -m tox -e portal-ui
```

O primeiro grupo verifica exportação reconciliada com a fonte, idempotência,
recibos, contratos e regressões existentes. Os testes de UI usam o lock separado
com Streamlit AppTest e Plotly: navegação, filtros, paginação, caso com/sem SHAP,
feedback, rejeição de corrupção e saúde HTTP de um servidor real com fixture.
O ambiente científico pode pular os testes opcionais de UI; `portal-ui` os executa.
A inspeção visual no navegador nativo complementa AppTest; não foi declarada
aprovada nesta preparação. Para aceite: [runbook](FRAUD_PORTAL.md).


### Página de desempenho e contrato independente do algoritmo

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/fraud_portal tests/case_explanations tests/product_flow \
  tests/serving_recovery tests/storage/test_managed_paths.py \
  tests/storage/test_persistence_inventory.py tests/infra/test_platform_entrypoints.py &&
.venv/bin/python -m tox -e portal-ui
```

O teste HTTP usa MLflow real com candidatos de fixture e liga a avaliação a uma
regressão logística, preservando aliases. Outros testes verificam que train/test
não são lidos pelo loader de validação, que a política conserva primeira passagem
e orçamento, publicação idempotente, métricas corrompidas/fora de contexto e UI
para HGB, regressão e identidade de algoritmo futuro. Os rótulos não participam
da seleção de alertas. [Aceite nativo e comandos](PORTAL_PERFORMANCE.md).


### Aceite nativo da página de desempenho — 2026-10-22

O autor executou portal-performance-prepare, portal-performance-verify e
portal-smoke. A avaliação usou 67.255 transações; o snapshot manteve 200 decisões,
199 clientes, 13 alertas, 200 feedbacks e cinco explicações. O verificador offline
não carregou modelo. Hash de pesos e champion preservados; conferência visual
explicitamente concluída pelo autor. O manifest da avaliação está registrado na
[evidência](../../references/evidence/portal_performance_author_validation_2026-10-22.json).
Não implica nova confirmação independente, promoção de sucessor ou chegada
contínua de novos eventos ao portal.

## Sessão operacional do produto

`tests/product_session` verifica HTTP real, pausa, retomada, unicidade persistida,
identidade, leitura concorrente e feedback disponível no relógio correto.
`tests/fraud_portal/test_session_ui.py` integra o ambiente `portal-ui`.
O ambiente opcional `product-session` exige sessão preparada nova e declaração
de writers; executa o ensaio no champion corrente sem treino ou promoção.
Veja [PRODUCT_SESSION.md](PRODUCT_SESSION.md) para comandos e aceite visual.

## Ampliação de sessões

`tests/product_session/test_scaling.py` testa captura temporal, 262 decisões HTTP reais, paginação SQL, totais globais, feedback e reconstrução independente. `portal-ui` testa navegação de páginas sem reduzir os indicadores globais. O ambiente opt-in `product-session-scale` valida uma nova sessão de 2.000 decisões e preserva os bytes da sessão de 200 já aceita. [Procedimento](PRODUCT_SESSION_SCALE.md).

## Monitoramento da sessão identificada

`tests/session_monitoring` exercita HTTP real com autenticação, corpo limitado,
expiração, retomada, identidade incorreta, regressão de watermark/contagens,
coleta de checkpoints causais HTTP antes/depois de pausa e feedback, renderização
Helm com/sem extensão e retomada após falha parcial de instalação.
`tox -e session-monitoring` é opt-in e exige a sessão ampliada concluída e a
extensão instalada. Confere a projeção no Prometheus e proxy do Grafana, expiração,
retomada e preservação dos bytes aceitos. Não gera novas decisões nem comprova
chegada concorrente no KinD, firing de alertas ou avaliação visual.
[Comandos](SESSION_MONITORING.md).

### Aceite do monitoramento da sessão — 2026-10-24

O autor executou o ambiente `session-monitoring`: **1 teste passou em 68,39 s**,
total tox 82,19 s. Os agregados de `arrival-2k-v1` foram conferidos no receiver,
Prometheus e proxy do Grafana; expiração e retomada passaram. A revisão visual
foi confirmada separadamente, preservando o campo automatizado original.

A suíte geral teve **1.705 testes aprovados, 51 pulados, 165 warnings e 12
subtestes aprovados**, em 365,26 s; `py314: OK`, total 386,24 s. Ruff e formatação
foram aprovados (474 arquivos). As saídas são evidência fornecida pelo autor,
não execução nativa no ambiente do assistente.

O novo baseline passou na checagem de leitura atual; snapshot do portal e
avaliação de 67.255 transações mantiveram seus manifests. O escopo não inclui
outro stop/start ou reboot, firing de alertas, chegada concorrente no KinD ou
promoção/rollback de sucessor com pesos diferentes.
[Evidência](../../references/evidence/session_monitoring_author_validation_2026-10-24.json).

## Portal único com sessão vinculada

`test_unified_portal.py` exercita a mesma aplicação com sessão, investigação e
evidências, falhas independentes, limites de página e identidade exata para SHAP.
`test_session_link.py` e `test_session_link_host.py` conferem ponteiro, paths,
credenciais, idempotência e troca explícita sem alterar as fontes.
O ambiente `portal-unified` é opt-in, usa os arquivos reais já aceitos em um
Streamlit AppTest isolado e confere os hashes antes/depois. Não pontua nem treina;
saúde HTTP e revisão visual são registradas separadamente. [Guia](UNIFIED_PORTAL.md).

### Aceite do portal unificado e cabeçalho — 2026-10-24

O autor forneceu `py314: OK` para o recorte (103 testes, 12 pulados, 13 warnings),
`portal-ui: OK` (44 testes) e `portal-unified: OK` com os arquivos reais (1 teste,
0,96 s; tox 12,69 s). A navegação AppTest e os hashes das fontes passaram; portal
e desempenho mantiveram seus manifests. O autor confirmou o refinamento visual
com "Ficou muito bom!". Essa confirmação é separada dos campos automatizados
originais, que não são reescritos. [Evidência](../../references/evidence/unified_portal_author_validation_2026-10-24.json).

O footprint global de Docker/KinD ainda requer medição própria; revisão de
Dockerfiles e recibos antigos não certifica o tamanho de todas as imagens ativas.
