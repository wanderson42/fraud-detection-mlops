# Validação com pytest, Poetry e tox

A suíte protege contratos e falhas do sistema. **Último relato local do autor:
164 testes aprovados em 21,11 s; tox em 24,42 s**, na etapa de congelamento.
Houve 66 avisos nas categorias de dependências já identificadas; aprovação não
significa ausência de avisos. [Evidência](../references/evidence/freeze_execution_2026-10-08.json).
Isso é evidência local, separada de uma execução de CI para cada revisão.

## Responsabilidades e comandos

| Ferramenta | Papel |
| --- | --- |
| Poetry | Gerenciar o projeto e instalar as versões de `poetry.lock` |
| tox | Criar `.tox/py314` e executar a sequência de validação |
| Ruff | Verificar lint e formatação |
| pytest | Executar cenários e conferir resultados |

Na raiz do checkout, com Python 3.14 e Poetry disponíveis:

```bash
poetry install
make validate
```

`make validate` executa `poetry run tox -e py314`, a mesma referência usada pela
CI. Para desenvolvimento rápido, use `poetry run pytest -q` ou `make test`.
Para selecionar uma parte da suíte:

```bash
poetry run -- tox -e py314 -- tests/modeling/test_experiments.py
```

Lint e formatação continuam nessa execução. O primeiro `--` encerra opções do
Poetry; o segundo encaminha argumentos ao pytest através do tox. Python ausente
causa falha; só declaramos suporte a 3.14. A CI usa 3.14.4.

## Por que os testes têm esta organização

O layout acompanha **responsabilidades e caminhos relativos ao pacote**:

| Implementação | Testes |
| --- | --- |
| `fraud_detection_mlops/bronze.py` | `tests/test_bronze.py` |
| `fraud_detection_mlops/silver.py` | `tests/test_silver.py` |
| `fraud_detection_mlops/features.py` | `tests/test_features.py` |
| `fraud_detection_mlops/gold.py` | `tests/test_gold.py` |
| `fraud_detection_mlops/modeling/train.py` | `tests/modeling/test_train.py` |
| `fraud_detection_mlops/modeling/tracking.py` | `tests/modeling/test_tracking.py` |
| `fraud_detection_mlops/modeling/experiments.py` | `tests/modeling/test_experiments.py` |

Outros módulos seguem a mesma convenção. Não repetimos o nome
`fraud_detection_mlops` dentro de `tests/`: a raiz dos testes já corresponde à
raiz do pacote. `tests/modeling/conftest.py` compartilha fixtures; arquivos de
teste não importam uns aos outros. Integrações podem cobrir vários módulos, e
alguns cenários de comparação estão em `test_experiments.py`. A correspondência
não exige um arquivo por implementação.

O pytest usa `--import-mode=importlib`. A documentação oficial apresenta tanto
testes separados da aplicação quanto testes junto ao pacote; espelhamento
literal não é um requisito do framework. [Boas práticas do pytest](https://docs.pytest.org/en/stable/explanation/goodpractices.html).
Decisões de empacotamento ficam na [arquitetura](ARCHITECTURE.md).

## Quantidade de testes e controle de complexidade

A contagem do pytest inclui casos parametrizados; não equivale necessariamente
a 152 funções diferentes. Contagem e cobertura não são metas isoladas. Um teste
se justifica quando protege comportamento, contrato ou falha relevante e pode
revelar um defeito que outro cenário não detectaria.

Revisamos testes junto das mudanças: removemos cenários de funcionalidades
aposentadas, unificamos duplicações que protegem o mesmo comportamento e evitamos
assertions que apenas reproduzem a implementação. Dados pequenos e execução
determinística limitam o custo. Não acrescentamos testes para cada edição de
documentação nem dividimos a suíte em camadas vazias só para manter simetria.

Os aproximadamente 18 segundos relatados são aceitáveis para o ciclo atual;
tempo curto não comprova qualidade. Se a suíte crescer, mediremos quais cenários
custam mais e separaremos integrações justificadas dos checks rápidos. Novos
testes de serviço, replay e implantação entram com suas funcionalidades.

## Riscos cobertos hoje

| Área | Exemplos de comportamento protegido |
| --- | --- |
| Bronze e artefatos | Integridade, reutilização, falha de download/publicação, corrupção e escrita atômica |
| Silver e perfil | Tipos, aceitação semântica, zeros, reconciliação e preservação da origem |
| Features e Gold | Oracle independente de datetime, limites em nanossegundos, peers, atraso de rótulos, invariância ao futuro e splits |
| EDA | Datas de treino, exclusão dos holdouts, agregações e corrupção de entradas/saídas |
| Métricas | Agregação por cliente/dia, score máximo, empate por ID, orçamento de cem e média diária |
| Treinamento e persistência | Ajuste só no treino, exclusão do teste, recarga com paridade de scores e rejeição de tipos não revisados |
| MLflow e diagnóstico | Runs independentes, falha parcial, assinatura, modelo congelado, SHAP e separação de casos ilustrativos |
| Ablações e comparação | População pareada, referência correta, política versionada, retomada sem refit/duplicação e exclusão de dias |
| Congelamento | Git antes da execução, alterações de insumos, política válida, recibo sem sobrescrita e paridade com MLflow/skops |
| Avaliação final | Modelo fixo, leitura exclusiva do teste, gate com dois critérios, acesso registrado, retries sem duplicação e recomputação offline |

As features são comparadas a um cálculo independente por máscaras temporais,
não só ao próprio SQL. A integração de modelagem usa estimadores e MLflow/SQLite
reais em fixtures pequenas. A persistência rejeita tipos não revisados **antes**
de desserializar. Esses cenários protegem riscos que poderiam invalidar avaliação
ou execução mesmo quando o pipeline termina sem erro.

A integração de congelamento usa o modelo e store MLflow/skops reais sobre dados
sintéticos. A fixture linear tem sua seleção direcionada a HGB neste teste para
isolar identidade e paridade; a política de ranking é conferida em testes próprios.
Após preparar baseline/ablações, o teste remove os Parquets sintéticos de treino e
teste e proíbe novos fits. O congelamento precisa funcionar somente com validação.
Esse teste não produz resultados reais do portfólio.

Na preparação do congelamento, o tox no Python 3.14.4 aprovou **164 testes**, com 66
avisos nas mesmas categorias de terceiros já observadas. A
[evidência de preparação](../references/evidence/freeze_preparation_2026-10-08.json)
separa essa execução controlada do congelamento real, concluído posteriormente.

O autor concluiu depois o congelamento real; a evidência está vinculada acima.
Os testes novos da avaliação final usam dados controlados e não consultam seu teste
real. A integração usa um HGB e MLflow/skops reais, remove os Parquets sintéticos
de treino/validação antes da avaliação e proíbe novos fits. Confere o gate,
reutilização da run de avaliação e preservação da run do modelo. Esses checks
não demonstram eficácia antifraude ou funcionamento de uma política online.

Na preparação da avaliação final, a suíte completa com 16 casos novos aprovou
**180 testes em 32,77 s; tox em 38,74 s**, com 71 avisos nas mesmas categorias
de terceiros. [Evidência de preparação](../references/evidence/final_evaluation_preparation_2026-10-08.json).
Esse resultado é separado da execução do holdout real, ainda pendente.

`verify` usa os contratos próprios de cada etapa. Testar sua implementação em
fixtures não substitui executar a verificação sobre os artefatos reais.

## Sequência e isolamento

1. O tox cria ou reutiliza `.tox/py314`, separado da `.venv` de desenvolvimento.
2. O [check de ambiente](../scripts/check_test_environment.py) confirma que o Poetry
   aponta para o Python executado pelo tox.
3. `poetry check --lock` exige coerência entre projeto e lockfile.
4. `poetry sync --only main,dev` instala as versões fixadas e o projeto editável.
5. O Python desse ambiente executa Ruff e pytest; falhas interrompem a validação.

O tox define `VIRTUAL_ENV` e impede que Poetry crie outro ambiente. A checagem
precede `sync`, que pode remover pacotes extras **dentro de `.tox/py314`**;
a `.venv` principal não é o alvo. Poetry é a fonte das versões; não repetimos
dependências numa lista independente do tox. O cache de downloads pode ser
compartilhado, mantendo os ambientes separados. [Configuração tox](../tox.toml).

## CI, recuperação e limites

O [workflow](../.github/workflows/ci.yml) prepara Python/Poetry e executa tox.
Os comandos Ruff/pytest têm uma referência única na configuração tox.

- Ferramenta ausente: execute `poetry install` com o grupo `dev`.
- Python ausente: confira a instalação de 3.14 e `poetry run python --version`.
- Alvo Poetry incorreto: revise a configuração; não contorne o check antes de `sync`.
- Ambiente inconsistente: use `poetry run tox -r -e py314`.
- Lockfile divergente: revise as dependências e gere o lockfile consistente.

Os testes usam dados controlados, diretórios temporários e rede simulada para
aquisição. Não baixam o Handbook nem reconstroem os medalhões reais. Dependências
podem exigir rede para instalação. Os arquivos de preparação e resultados
históricos permanecem em `references/evidence/`; não são uma aprovação da revisão
atual. [Política de documentação](DOCUMENTATION_POLICY.md).

O projeto é validado a partir do checkout, com instalação editável e
`package = "skip"` no tox. Isso não comprova distribuição wheel independente nem
recursos de `references/` empacotados: esse contrato será tratado antes do container.
A suíte não demonstra latência, disponibilidade, streaming ou eficácia antifraude
real; esses marcos estão no [mural de metas](ROADMAP.md).

Avisos de terceiros permanecem visíveis. Falhas são investigadas e depreciações
acompanhadas na atualização das dependências; não suprimimos avisos globalmente
para deixar o resultado visualmente limpo.

## Contrato de integração de modelos

`tests/modeling/test_interface.py` verifica os três modelos registrados e a
contribuição sintética externa: isolamento das factories, seed/parâmetros,
pré-processamento aprendido só no treino, invariância à ordem das entradas e
paridade após recarga. A comparação com Pipelines construídas a partir do protocolo
fixo protege os parâmetros e scores históricos em dados controlados.

Os cenários negativos rejeitam dados desalinhados, features inválidas, factory ou
pré-processador já ajustado, classe positiva incompatível e probabilidades de
formato/domínio incorretos antes de publicação. A contribuição externa usa o mesmo
fit, artefatos e MLflow; o teste confere uma run principal e sua identidade. O
[recibo de preparação](../references/evidence/model_interface_preparation_2026-10-08.json)
registra o ambiente e os checks observados. Isso não mede qualidade de um novo
modelo sobre os dados reais do projeto.


## Busca temporal e Optuna

`test_development.py` usa partições sintéticas com bytes reservados deliberadamente
inválidos, demonstrando que setembro não é aberto. Verifica contexto causal,
atraso de rótulos, integridade, schema, autorização dos cortes e reutilização.
`test_search.py` exercita SQLite real, retomada, orçamento global, falhas e trial
abandonado, lock de escritor, mudança de identidade e corrupção antes de novos fits.
Compara a sequência TPE contínua/retomada além da inicialização e rejeita recibos
inconsistentes. A referência interrompida exige revisão, sem ajuste silencioso.

Um teste de integração usa MLflow nativo e skops reais, confirma identidade/run,
parâmetros, download e paridade dos scores. Os demais isolam o tracking para testar
o ciclo de estudo com baixo custo. Gates usam resultados controlados para conferir
retenção e elegibilidade sem promoção. Isso não demonstra ganho de AP, estabilidade
ou custo sobre o conjunto Handbook: essa execução pertence ao autor.

Validação da preparação: **224 testes, Ruff, lockfile e tox aprovados**; 77 avisos
de dependências permanecem visíveis. O [recibo estruturado](../references/evidence/optuna_preparation_2026-10-08.json)
identifica código, ambiente, checks e limitações. O teste do sampler exerce o runner
público em uma chamada e em lotes, com SQLite real e objetivo sintético controlado.
