# Simplificação do código e dos comandos

## Primeira revisão — 2026-10-22

Base revisada: `740ca24bcbd17c57465ae9f654f9dbbfefd47d30` (main após o PR #10).
O objetivo é tornar a operação corrente fácil de localizar e retirar código que
já cumpriu sua função. Alterações são entregues em recortes verificáveis.

| Achado | Alteração |
| --- | --- |
| Makefile misturava operação, desenvolvimento e ciência em um arquivo longo | Raiz reduzida ao índice e ajuda; receitas existentes em `infra/make/development.mk` e `science.mk` |
| Ajuda mostrava treino, holdout e ensaios junto dos acessos diários | `make help` resume operação; `make help-all` conserva o catálogo completo |
| `platform-api` seguia o plano sintético e `round-api` seguia a primeira rodada | Um acesso operacional lê o deployment atual; `round-api` é alias; sintético tem comando explícito |
| Revisão de plano anterior à correção de leitura vazia já aplicada | Removidos o utilitário pontual e seus quatro cenários exclusivos; verificador real e negativos de integridade mantidos |
| Roteiro apresentava o Registry histórico como destino operacional | Roteiro passa a apresentar `fraud-detector`, seus Inputs e o champion ativo |

A redução de testes corresponde à retirada de uma capacidade transitória, não à
supressão de falhas para acelerar a CI. Os testes do acesso corrente cobrem outro
slot/versão, ausência de estado, divergência de pesos/Registry/rota, mudança de
identidade durante o acesso e encerramento do forward. A suíte de Make continua
verificando declaração de writers, argumentos com espaços, ordem e parada após
erro sob `-j`.

## Dependências que a revisão confirmou

`scripts/operational_round/controller.py` importa `scripts/storage/gold_archive.py`
e calcula pins sobre `scripts/storage`, o pacote científico e charts. O ciclo de
atualizações reutiliza a rodada, serving e transporte. Portanto, os diretórios
`storage`, `platform`, `tracking` e os ensaios Docker não podem ser classificados
como obsoletos apenas pela idade ou por existir KinD. Por exemplo, a qualificação
da entrega da imagem continua sendo executada pela CI antes de seu consumo.

Os módulos científicos e os pins de modelos/dados não são alterados neste
recorte. O acesso atual reutiliza os gates de `model_updates.deployment` e as
rotinas de forward existentes; não cria outro servidor ou controlador de deploy.
A primeira rodada permanece reproduzível pelo seu CLI e protocolo. O alias Make
`round-api` passa a significar acesso operacional, não seleção histórica por
`ROUND_ID`; essa mudança está explícita na ajuda e no guia.

## O que foi retirado

- `scripts/platform/kind_history_readback_fix.py`.
- O teste parametrizado de quatro cenários exclusivo dessa revisão de plano.
- A receita duplicada de acesso por `round-api` e as instruções correntes de uso
  do utilitário retirado.

O utilitário antigo não integra os pins dos planos históricos atuais. Sua
remoção não reescreve recibos, fontes, artefatos ou estado privado. Código e
procedimento continuam recuperáveis no Git anterior a este recorte. Evidências
datadas permanecem como registro do que foi executado; não viram instruções para
nova instalação.

## Verificação e aceite

Regressão local em Python 3.14.4: **186 testes aprovados, seis skips e 12
subtests**, em 59,25 s; Ruff e formatação aprovados. Foram conferidos Make,
transferência histórica, recuperação do serving, fluxo HTTP, atualizações, portal
e caminhos gerenciados. Os 167 pins da rodada, 187 das atualizações e 193 do
produto permaneceram idênticos aos blobs da main integrada.
[Evidência de preparação](../../references/evidence/operations_simplification_preparation_2026-10-22.json). A validação nativa do acesso é:

```bash
make help
make help-all
make platform-api
```

O último comando requer o cluster e o champion existentes saudáveis, imprime
versão/slot/pesos/release e verifica HTTP. Mantenha-o aberto e encerre com Ctrl+C.
Não execute um novo ensaio de falha para validar essa reorganização. O pacote não
inicia nem altera o cluster, não executa treino e não exclui dados locais.

## Continuação

O próximo recorte conecta produtor com ritmo, checkpoint, ledger, portal e
métricas da mesma sessão. A revisão mais ampla de duplicações deve usar esse
caminho operacional como referência e demonstrar que cada remoção deixou de ter
consumidores. A promoção de sucessor com novos pesos continua dependente de um
candidato que passe o gate; não é declarada concluída por esta simplificação.

## Aceite nativo do primeiro recorte

O autor executou 186 testes, seis skips e 12 subtests em 37,59 s. O acesso
`platform-api` abriu o champion versão 1, slot blue, pesos
`6dad9a78a5da0bda34cae6957b0ddbe391ecd848fbbc6604ae930bea3501370b`, release
`e8350e0a841f4136983e0d905068a743`; HTTP 422 conferido e p95 observado de
86,79 ms em 20 requisições. Ctrl+C foi executado.
[Evidência nativa](../../references/evidence/operations_simplification_author_validation_2026-10-22.json).
Esse tempo é a observação desse ensaio, não um SLA.
