# Operação da Bronze

Escopo: CLI local da revisão `18242fcc8e340bad52a394c7c3624b78bd809b6c`.
Execute os comandos na raiz do checkout. Ambiente de referência: Python 3.14.4 e
Poetry 2.4.3. A fonte e os contratos estão em [Data pipeline](DATA_PIPELINE.md).

## Preparar e conferir o ambiente

```bash
poetry install
poetry check --lock
poetry run ruff check .
poetry run ruff format --check .
poetry run pytest -q
```

A CI executa essas verificações. Os testes da extração simulam a rede e não baixam o
dataset real. A verificação de uma Bronze real é um procedimento separado.

## Adquirir uma amostra e conferir reutilização

```bash
poetry run python -m fraud_detection_mlops.dataset extract \
  --start-date 2018-04-01 --end-date 2018-04-07
poetry run python -m fraud_detection_mlops.dataset verify
```

Num snapshot novo, o esperado é baixar sete arquivos. A verificação deve mostrar
`Verified: 7/183; complete: False`. Um snapshot que já tenha mais dias pode mostrar
cobertura maior. Para conferir a reutilização, repita a extração do mesmo intervalo;
esperamos `Downloaded: 0; skipped: 7` quando os arquivos estiverem íntegros.

## Completar o snapshot

```bash
poetry run python -m fraud_detection_mlops.dataset extract
poetry run python -m fraud_detection_mlops.dataset verify --require-complete
```

Omitir datas seleciona todo o inventário. Arquivos existentes são conferidos e
reutilizados. O resultado esperado final é `Verified: 183/183; complete: True`.
O comando `verify` trabalha offline e retorna erro quando a cobertura exigida ou a
integridade não é atendida. O conteúdo semântico das transações não é validado aqui.

## Localizar os artefatos

O diretório padrão do snapshot é:

```text
data/raw/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/
```

Ali ficam os arquivos `.pkl`, `manifest.json` e `runs/<run_id>.json`. O identificador
impresso no fim da extração permite encontrar sua auditoria. A leitura dos JSONs
pode ser feita no editor; para conferir a estrutura do manifesto pelo terminal:

```bash
python3 -m json.tool \
  data/raw/handbook/6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a/manifest.json
```

O recibo versionado em `references/evidence/` resume um marco histórico. Preserve o
manifesto e as auditorias nativas com o snapshot, pois `data/` não vai para o Git.
O comando `verify` não cria uma auditoria de extração nova.

## Opções

| Opção de `extract` | Uso e padrão |
| --- | --- |
| `--start-date`, `--end-date` | Intervalo inclusivo dentro do inventário; formato `YYYY-MM-DD` |
| `--output-root` | Diretório pai dos snapshots; padrão `data/raw/handbook/` |
| `--inventory` | Inventário revisado; padrão `references/handbook_source.json` |
| `--timeout` | Timeout por operação de rede, não da execução inteira; padrão 30 s, CLI aceita 1 a 60 s |
| `--attempts` | Limite de tentativas por arquivo; padrão 3, CLI aceita 1 a 5 |

`verify` aceita `--output-root`, `--inventory` e `--require-complete`. Use a mesma raiz
e o mesmo inventário da extração. Consulte a CLI para sua referência executável:

```bash
poetry run python -m fraud_detection_mlops.dataset extract --help
poetry run python -m fraud_detection_mlops.dataset verify --help
```

## Diagnóstico e recuperação

| Situação | Como agir |
| --- | --- |
| Falha transitória de rede | O extrator tenta novamente para erros transitórios e HTTP 429/500/502/503/504. Se falhar, confira a auditoria, restabeleça o acesso e repita o comando. |
| HTTP 404 | Confira URL, inventário e commit. A falha não recebe novas tentativas automáticas. |
| Download interrompido | Repita o intervalo. Downloads temporários da tentativa são removidos quando a limpeza normal é executada; arquivos já aceitos são reutilizados. |
| Arquivo esperado ausente | Selecione sua data ao repetir a extração para recuperá-lo da fonte fixada. |
| Arquivo com corrupção | Preserve uma cópia para investigação. Retire explicitamente o arquivo inválido do snapshot e repita sua data para obter uma cópia verificada. O extrator não sobrescreve a corrupção automaticamente. |
| Manifesto ausente após falha | Repetir o intervalo permite conferir e registrar arquivos válidos já presentes. |
| Arquivo conhecido sem registro | Inclua sua data na extração. O arquivo é conferido antes de ser incorporado ao manifesto. |
| Arquivo estranho à fonte | Inspecione e retire-o explicitamente do snapshot. O verificador exige correspondência exata entre `.pkl` e manifesto. |
| Lock existente | Confirme que não há extração ou verificação ativa. Um encerramento abrupto pode ter deixado `.extract.lock`; só então remova o lock residual. |
| Auditoria em `running` | Investigue possível encerramento abrupto e lock residual. Não trate essa execução como bem-sucedida. |
| Metadados inconsistentes | Preserve os documentos para investigação e compare com o inventário. Não edite checksums para contornar a verificação. |

Após qualquer recuperação, execute novamente `verify`; para a base completa, use
`--require-complete`. Em caso de perda irrecuperável do manifesto, uma nova aquisição
em outra `--output-root` preserva o snapshot antigo para investigação.

## Fechar uma entrega

Confira o diff, documentação, evidências e os checks técnicos aplicáveis antes do
commit. Os dados permanecem locais. Após o push, consulte a execução da CI que
corresponde ao novo commit. Siga a [política de documentação](DOCUMENTATION_POLICY.md)
e registre resultados observados com a origem da evidência.
