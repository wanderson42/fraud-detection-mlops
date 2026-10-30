# Fonte de eventos para uma instalação nova

## Aceite nativo da fonte e de sua sessão — 2026-10-30

O autor executou o código commitado em `7642fc5` na VM e forneceu as saídas:
242 testes passaram, com cinco skips e 14 subtests em 58,94 s (tox 64,41 s).
O plano inicial recusou o Silver ausente em `data/interim/handbook`; a fonte foi
preparada a partir do Silver já restaurado no ambiente, com caminho explícito.
Este recorte não adquiriu Bronze nem instalou outra plataforma.

| Identidade | Resultado |
| --- | --- |
| Silver selecionado | 15 partições; manifesto `95a4e7ccdc7d0b336b9d7219a5365ff42cbaa0561e86ff89cad1270e2ba125ac` |
| Fonte `clean-events-v1` | 288.172 eventos: 144.086 transações e 144.086 feedbacks |
| Manifesto da fonte | `5876c318c773a267edf5bde879bdcf258198f8a2e273c575cee0890c7df56713` |
| Sessão `clean-arrival-v1` | Captura limitada: 269.334 eventos, 134.667 transações/feedbacks, 200 decisões e 13 alertas |
| SHA da sessão | `24850bb5522da5bfdc4f68b23b3eba68bc59214c23d4a113f70a3e3310b317ec` |
| SHA do checkpoint reconciliado | `fd5c27c4a4ef5f9ed51917cc3974996f2e981f8e235cda9361951ea01805bbd5` |

A geração da fonte foi verificada pelo hash declarado, sem fit ou pontuação e
com Silver preservado segundo a saída do produtor. O plano sozinho só conferia
metadados (`partition_payloads_verified=false`); a preparação posterior verificou
os bytes selecionados. A sessão nova chamou a API corrente e o verificador
reconstruiu requisições causais e triagem a partir dos eventos e recibos HTTP.
Foram 200 decisões e 200 recibos HTTP, sem reutilização dos scores da fonte.
O feedback foi concluído sem novas chamadas HTTP.

O modelo permaneceu `401d7c36fc17bfd4176e506d45da2a559e0e2d44b7305af2f77c4cd8ac04ca67`,
release `308a1a82036a44e49d477a1b16747b6e`; não houve treino ou promoção.
A diferença entre os 288.172 eventos da fonte e os 269.334 da sessão corresponde
à captura limitada por contexto e prefixo de pontuação, não a perda de eventos.
A comparação integral com o ledger da sessão anterior não foi realizada.

O run levou 1.690,68 s: 1.671,16 s de warmup e 19,52 s de chegadas, com 200
tentativas HTTP. O feedback levou 552,90 s e a verificação 13,81 s. A latência
HTTP de 200 medições teve p50 28,54 ms, p95 37,69 ms e máximo 50,81 ms; exclui
warmup, pacing e reconciliação. Esses tempos não são SLA nem duração da instalação.
RSS de 376.872 KiB no run é máximo do processo, não alocação incremental da sessão.

O resultado é `product_flow_reconciled`, não uma nova avaliação independente de
qualidade. Um alerta com feedback positivo de transação não estima a precisão
global da investigação. Não houve aceite de UI, troca do monitoramento para a
nova sessão ou reexecução nativa da sessão completa para provar idempotência.
Os testes sintéticos têm cobertura de repetição própria. Não se apresenta uma
releitura dos hashes da sessão anterior, que não foi fornecida neste gate.

**Aceito:** Silver existente → fonte sem modelo anterior → sessão HTTP nova →
feedback → reconciliação independente. **Pendente:** instalação integral em
ambiente vazio, com aquisição/cadeia de dados, infraestrutura e identidades próprias.
Não repetir o run de aproximadamente 28 minutos apenas para documentar o resultado.

[Recibo documental](../../references/evidence/session_event_source_author_validation_2026-10-30.json).
Origem: terminal fornecido pelo autor; não é execução do assistente. CI e merge
desta branch não são afirmados por este registro. Os recibos privados originais
e os flags dos registros de preparação permanecem intactos.

Implementação preparada após o PR #20 (`730a1d2`) e validada nativamente pelo autor em 2026-10-30 no recorte descrito acima. O guia abaixo conserva o procedimento; instalação integral continua pendente.

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
Esses testes não são execução do Handbook na VM. O aceite nativo posterior da fonte e da sessão está registrado acima; a instalação integral permanece pendente.

[Contrato de estado e gate integral](VM_STATE_AND_CLEAN_INSTALL.md) ·
[Sessão](PRODUCT_SESSION.md) · [Rodada e dados](OPERATIONAL_ROUND.md).
