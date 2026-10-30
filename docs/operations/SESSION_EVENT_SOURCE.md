# Fonte de eventos para uma instalação nova

Preparação em 2026-10-30, após o PR #20 (`730a1d2`). Implementação e testes
locais não substituem a execução nativa no Silver da VM nem o gate de instalação
integral. O estado aceito `repro-arrival-200-v1` permanece preservado.

## O problema e a mudança

`session-prepare` captura eventos de um estado fonte. Até aqui, precisava de um
checkpoint durable replay v2 concluído, embora descartasse seus scores. O padrão
`data/replay/paced-native-v1` não vem no checkout. Não é necessário executar o
replay de um modelo anterior apenas para produzir transações para o champion atual.

O novo produtor `scripts.product_flow.event_source` parte do Silver identificado
pelo inventário Handbook e pelos contratos existentes. Confere o manifesto e os
bytes das partições, reutiliza a verificação independente de schema, domínios,
duplicatas e features causais, e materializa transações com feedback disponível
sete dias depois. As features temporárias servem à validação e não são publicadas
como nova avaliação. Não carrega modelos, não usa Registry, não chama API e não
executa fit. A sessão posterior continua reconstruindo suas próprias features.

O destino é `data/replay/session-sources/<source-id>/`, com dois arquivos:

- `checkpoint.sqlite`: índice de eventos para seleção, SQLite user_version **3**;
- `source.json`: schema `fraud_session_event_source_v1`, procedência Silver,
  commit/pins de código, protocolo, contagens e hash do banco.

O nome do banco mantém a interface `--source-state`, mas **não é um checkpoint de
decisões**. Só existem identity/events/transactions/feedback; não há tabelas de
scores, decisões, alertas ou checkpoint de processamento. O capturador distingue
o formato novo do durable replay v2. O caminho v2 conserva as verificações anteriores.

O produtor reserva 14 dias de contexto antes da janela de pontuação de 1–7 dias.
Limites: 21 partições, 500.000 transações e pelo menos 1 GiB de disco livre.
Opera com duas threads e limite DuckDB de 1 GB; esses valores não são uma medição
de pico RSS. Não sobrescreve um ID existente. Um lock local coordena preparações
da fonte; não bloqueia writers externos do Silver, que deve estar imutável.
Os hashes são reconferidos antes da publicação. O destino é privado, sem symlinks.

## 1. Conferir o Silver — somente metadados

Depois de aplicar, testar e commitar esta implementação, na raiz do checkout:

```bash
make session-source-plan \
  SESSION_EVENT_SOURCE_ID=clean-events-v1 \
  SESSION_SILVER_ROOT=data/interim/handbook
```

O parâmetro Silver é o diretório **acima** de `<source-commit>/silver_v1/`.
O plano exige cobertura de 2018-09-02 a 2018-09-16, inclusive, para a janela padrão
de 2018-09-16 a 2018-09-17 (fim exclusivo). Abre somente o manifesto e as referências;
`partition_payloads_verified=false` é esperado. Não gera fonte nem acessa Kubernetes.

Se esse caminho não existir, localizar o Silver já construído/restaurado pela
cadeia de dados e passar seu diretório pai correto. Esta leitura lista apenas
caminhos de manifestos locais; não verifica payloads nem os envia para serviços:

```bash
rg --files --hidden --no-ignore data/interim data/storage/kind-platform/pipeline \
  -g manifest.json -g '!**/credentials*'
```

Não escolher um manifesto Bronze ou Gold. Não criar um arquivo vazio, copiar um
checkpoint do autor ou refazer a rodada apenas para eliminar um erro de caminho.
Numa instalação realmente nova, adquirir/verificar Bronze e construir Silver
vem antes desse comando. O produtor não baixa dados nem restaura bundles implicitamente.

## 2. Preparar e verificar a fonte

Com o Silver correto, sem writers dessas partições:

```bash
make session-source-prepare \
  SESSION_EVENT_SOURCE_ID=clean-events-v1 \
  SESSION_SILVER_ROOT=data/interim/handbook
```

Preservar a saída e o `manifest_sha256` retornado. Usar esse valor real:

```bash
make session-source-verify \
  SESSION_EVENT_SOURCE_ID=clean-events-v1 \
  SESSION_EVENT_SOURCE_SHA256=SHA256_RETORNADO_NA_PREPARACAO
```

O verificador confere hash, integridade SQLite, schema, identidade e contagens.
O consumidor também verifica o manifesto/banco antes e depois da captura. Não
recria um histórico de decisões. Se o destino já existir, verificar o existente;
não apagar a fonte para tentar produzir outro resultado sob o mesmo ID.

As contagens vêm das partições reais; não exigir as contagens de um ensaio
anterior. Contar eventos não confirma qualidade preditiva ou generalização.

## 3. Usar depois numa sessão nova

Somente após fonte verificada e champion consistente no próprio ambiente:

```bash
make session-prepare \
  SESSION_ID=clean-arrival-v1 \
  SESSION_SOURCE_STATE=data/replay/session-sources/clean-events-v1 \
  SESSION_NO_OTHER_WRITERS=1
```

Este é um exemplo para uma sessão **nova**, não um comando de retomada da sessão
aceita da VM. Preparação consulta API/Registry e vincula o champion; pontuação só
acontece ao executar `session-run`. Produtor, feedback, portal e monitoramento
continuam com seus gates próprios. Não executar esta etapa como efeito implícito
da geração da fonte. IDs e caminhos são explícitos em todas as chamadas.

O padrão legado de `SESSION_SOURCE_STATE` não foi trocado. Os recibos aceitos
não são editados: uma transição exata de três fontes operacionais cobre o novo
capturador, seu produtor e o mecanismo de compatibilidade. Mudanças científicas,
políticas e contratos continuam bloqueadas. Não requer rebuild do receiver ou
alteração dos PVCs/serviços para preparar os eventos no host.

## Validação e limite do aceite

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/product_flow tests/product_session \
  tests/infra/test_source_compatibility.py \
  tests/storage/test_persistence_inventory.py \
  tests/infra/test_platform_entrypoints.py
```

Testes usam Silver sintético com manifestos próprios, a geração e captura reais,
serviço HTTP local, reconciliação causal e reexecução sem rescoring. Exercitam
corrupção, tabela de decisões indevida, symlinks, mudanças não commitadas,
destino existente, plano de metadados, Make e compatibilidade dos perfis antigos.
Não são uma execução do Handbook na VM. O aceite nativo da nova fonte e a
instalação integral continuam pendentes até as respectivas saídas reais.

[Contrato de estado e gate integral](VM_STATE_AND_CLEAN_INSTALL.md) ·
[Sessão](PRODUCT_SESSION.md) · [Rodada e dados](OPERATIONAL_ROUND.md).
