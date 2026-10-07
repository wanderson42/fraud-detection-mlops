# Validação com pytest, Poetry e tox

Estado: integração implementada e validada no ambiente de preparação. O autor informou
execução local aprovada: 70 testes em 1,57 s; tox completo em 4,61 s, com lint e
formatação aprovados. O commit da integração e sua CI ainda não foram informados.
Base publicada: `acf15169fed522751b974efae032d8683b64a036`, com
[CI da Silver aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/37563463479).

## Responsabilidades

| Ferramenta | Papel no projeto |
| --- | --- |
| Poetry 2.4.3 | Gerenciar o projeto e instalar versões exatas de `poetry.lock` |
| tox 4.32.0 | Criar o ambiente isolado `py314` e executar a sequência de validação |
| Ruff | Verificar lint e formatação |
| pytest | Executar os testes e avaliar suas assertions |

O tox é uma dependência do grupo `dev`, fixada no lockfile. Sua configuração fica
em [tox.toml](../tox.toml). O formato TOML evita ambiguidades de parsing e segue a
[recomendação atual do tox](https://tox.wiki/en/stable/reference/config.html).
Os pacotes já existentes mantiveram suas versões; foram acrescentados o tox e
suas dependências transitivas.

## Comandos

Execute na raiz do checkout com Python 3.14 e Poetry disponíveis:

```bash
poetry install
poetry run tox -e py314
```

`make validate` é um atalho para o mesmo comando. Para desenvolvimento rápido,
`poetry run pytest -q` e `make test` continuam disponíveis no ambiente principal.
O comando tox completo é a referência para validar a entrega e reproduzir a CI.

Para selecionar testes, os argumentos após `--` são encaminhados ao pytest:

```bash
poetry run -- tox -e py314 -- tests/test_silver.py
```

Lint e formatação continuam sendo verificados nessa execução.
O primeiro `--` encerra as opções do Poetry; o segundo encaminha os argumentos
ao pytest através do tox.

Python ausente causa falha, em vez de pular silenciosamente o ambiente. O projeto suporta Python
3.14; não declaramos compatibilidade com outras versões. A CI usa 3.14.4.

## Sequência e isolamento

1. O tox cria ou reutiliza `.tox/py314`, separado da `.venv` de desenvolvimento.
2. O [check de ambiente](../scripts/check_test_environment.py) confirma que o Poetry
   aponta para o mesmo ambiente do Python executado pelo tox.
3. `poetry check --lock` exige coerência entre `pyproject.toml` e o lockfile.
4. `poetry sync --only main,dev` instala as versões fixadas e o projeto em modo editable.
5. O Python de `.tox/py314` executa Ruff e pytest. Qualquer falha interrompe a validação.

O tox define `VIRTUAL_ENV` para seu ambiente e desabilita a criação de outro ambiente
pelo Poetry. A checagem ocorre antes de `sync`, para interromper a execução se o alvo
for diferente. `sync` pode remover pacotes extras dentro de `.tox/py314`; a `.venv`
principal não é o alvo desse comando.

O Poetry continua sendo a fonte das versões das dependências. Não repetimos pandas,
pytest ou Ruff em uma lista `deps` independente no tox. A primeira execução instala
as dependências no novo ambiente; as seguintes verificam e sincronizam o ambiente
existente. O cache de downloads pode ser compartilhado, mantendo os ambientes separados.

## Escopo e limites

O projeto atual é executado a partir do checkout e usa os inventários versionados em
`references/`. Por isso, esta configuração deixa o Poetry instalar o projeto em
modo editable e desativa o empacotamento automático do tox com `package = "skip"`.
Ela valida o código e as dependências em um ambiente separado. Um teste de wheel
instalada fora do checkout, com distribuição dos recursos necessários, será outro
contrato se o projeto passar a oferecer essa forma de distribuição.

Os testes usam dados controlados e respostas de rede simuladas. O tox não baixa o
Handbook nem reconstrói Bronze/Silver reais. A validação dos dados permanece nos
comandos próprios de `dataset` e `silver`. Instalar dependências pode exigir rede.

## CI e recuperação

O workflow [ci.yml](../.github/workflows/ci.yml) prepara Python e Poetry, confere o
lockfile, instala as ferramentas e executa `poetry run tox -e py314`. A lista de
checks fica no tox; a CI não mantém outra cópia dos comandos Ruff/pytest.

- `No module named tox`: execute `poetry install` com o grupo `dev` disponível.
- Interpretador ausente: confira `poetry run python --version` e a instalação do Python 3.14.
- Alvo Poetry diferente: examine a configuração de ambiente; não contorne o check antes de `sync`.
- Ambiente residual inconsistente: use `poetry run tox -r -e py314` para recriar o ambiente de teste.
- Lockfile desatualizado: revise a alteração de dependências e gere o lockfile; a CI exige correspondência.

`.tox/` já é ignorado pelo Git. Preserve no repositório apenas configuração, código,
lockfile e recibos pequenos. Resultados de preparação, execução local do autor e
CI são evidências separadas, conforme a [política](DOCUMENTATION_POLICY.md).

## Evidência da preparação

O [recibo](../references/evidence/tox_preparation_2026-10-07.json) registra a execução
com ambiente recriado: 70 testes aprovados, lint/formatação/lockfile aprovados e
checagem explícita do alvo Poetry. Também passou a seleção dos 20 testes da Silver,
e a proteção rejeitou um alvo apontando para a `.venv` principal.
A execução local do autor e a CI da integração tox continuam pendentes.

## Testes da EDA

A etapa EDA acrescenta testes com dados sintéticos para limites das datas, exclusão
de distribuições dos holdouts, preservação da Silver, reconciliação de agregações,
falhas de escrita, alteração de inputs e corrupção dos outputs. O painel é renderizado
sem display via Agg. O tox continua usando o mesmo lockfile, agora com Matplotlib.
Esses testes não executam a EDA do histórico real de 183 dias.
