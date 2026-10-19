# Tracking HTTP do HGB: adoção por etapas

O exemplo de contribuição foi aprovado no serviço nativo em 2026-10-17: recarga
independente com saída local indisponível, erro máximo zero e retorno explícito
a um store local descartável. [Evidência do autor](../../references/evidence/model_contribution_http_author_validation_2026-10-17.json).

O produtor HGB recebeu a opção HTTP e teve seu fold nativo aprovado. Seu estudo concluído tem 20 trials/63 tentativas e
conservou a referência. Repetir essa busca apenas para testar infraestrutura
não produz nova evidência científica. Por isso, o aceite nativo desta etapa
usa **um fold sintético**, com os mesmos caminhos de fit, artefatos e tracking.

## O que muda no runner

`hgb_optimization.optimize_hgb` aceita `http_uri` com `tracking_root=None`.
Na CLI, `optimize` aceita `--tracking-uri` ou `--tracking-root`, exclusivos.
Sem opções, o destino continua sendo `data/tracking`. Uma raiz local explícita
na CLI deve estar sob `data/tracking/<store>`; as APIs internas mantêm caminhos
explícitos para integrações e testes.

| Responsabilidade | Destino / regra |
| --- | --- |
| Metadados MLflow de novas execuções HTTP | Servidor escolhido explicitamente; no laboratório, PostgreSQL |
| Artefatos MLflow | `mlflow-artifacts:/...` pelo servidor; RustFS no laboratório |
| Estado transacional do Optuna | `study/study.db` local, com lock de escritor e backup próprio |
| Identidade de estudo HTTP | Endpoint normalizado e experimento `fraud-temporal-optuna-http-v1` |
| Identidade de estudo local | Campo histórico `tracking_root`, com o mesmo formato |
| Falha de tracking ou paridade | Falha explícita; sem troca automática de destino ou refit |

O endpoint/experimento pertencem à identidade persistida. Trocar HTTP por local,
mudar o endpoint ou mudar os insumos de um estudo existente exige outro estudo.
Um fold concluído com recibo e arquivos íntegros é reutilizado sem treino ou
nova publicação. Uma tentativa incompleta continua consumindo orçamento e exige
revisão; este incremento não cria um mecanismo automático de reparo.

A política de proxy já aprovada é reutilizada. Assinatura, skops/tipos revisados,
paridade, folds, espaço de busca, sementes, orçamentos e critérios científicos
continuam sob os contratos existentes.

## Verificação agora: um fold sintético

Com o laboratório já saudável:

```bash
.venv/bin/python -m tox -e py314 -- \
  tests/modeling/experiments/test_hgb_optimization.py \
  tests/modeling/experiments/test_hgb_tracking_destination.py \
  tests/integrations/test_mlflow_http_tracking.py

.venv/bin/python -m tox -e tracking-hgb
```

O segundo comando usa o pytest e fixtures sintéticos; não executa `optimize`.
Cria um único modelo HGB no experimento **`fraud-hgb-fold-smoke-http-v1`**, faz
recarga independente enquanto o modelo local está indisponível e reutiliza o
recibo sem novo fit/publicação. Não recria containers nem lê o estudo Handbook.
O cliente não precisa de variáveis AWS. Um teste HTTP com servidor real também
exercita o harness, substituindo apenas a inspeção Docker; isso não é aceite nativo.

Os dados de teste e o fold ficam em
`data/storage/tracking-lab/hgb-fold-<id>/dataset`; o resultado operacional fica em
`data/storage/tracking-lab/receipts/hgb-fold-<id>.json`, inclusive se falhar.
O ensaio não cria um banco Optuna ou tracking SQLite. Temporários do pytest são
descartáveis; as saídas preservadas permanecem na área inventariada do projeto.
Compartilhar a saída e o recibo. O autor aprovou esse fold no serviço em 2026-10-17: 84/21 linhas, erro zero,
uma tentativa e reutilização sem novo fit/publicação; containers preservados.

Opcionalmente, `tox -e hgb-http` valida transporte, paridade, falha do servidor,
recibo incompleto e reutilização com servidor HTTP/SQLite/arquivos descartáveis.
O teste padrão do scheduler usa fixtures e tracking substituído para conferir
orçamento/identidade. Essas provas têm escopos diferentes do ensaio nativo.

## Antes de um estudo completo pelo serviço

O runner continua exigindo política e implementação commitadas, preparação
compatível e orçamento autorizado. **Não execute outra otimização Handbook nesta
etapa.** O ensaio de um fold não remove essas proteções e não atesta uma busca
Optuna completa sobre PostgreSQL/S3. Nenhum commit é necessário para o teste de
integração sintético; seu fixture de proveniência está limitado ao pytest.

A verificação offline e os archives existentes continuam disponíveis. Não há
migração dos estudos históricos ou do backend Optuna. Os consumidores de
referência/avaliação que ainda assumem tracking local, o inventário de referências
entre stores, backup PostgreSQL e o arquivamento de futuros estudos HTTP precisam
de critérios próprios antes da adoção ampla. O inventário MLflow histórico é
local: IDs remotos em novos estudos fora de `data/storage` podem aparecer como
não resolvidos nele; não os trate como corrupção nem altere os IDs para escondê-los.

O fold nativo e, depois, o trio do runner de baselines foram aprovados pelo autor.
Consumidores e recuperação/retomada continuam necessários à adoção ampla. A migração de outros produtores,
limpeza, Helm/PVCs e eventual feature store permanecem no roadmap com justificativa
própria. Não há nova confirmação de qualidade, promoção ou migração primária.

## Aceite do autor e fronteira do trio inicial

O autor informou `status=success`, 1 teste aprovado em 4,31 s e tox OK.
Run `e21ffc6b9e12481489223486408d1b00`, experimento 4, erro máximo zero.
[Evidência e limites](../../references/evidence/hgb_fold_tracking_author_validation_2026-10-17.json).
A comparação inicial de dummy/regressão/HGB pertence a outro runner; sua adoção
HTTP também teve [aceite nativo](../modeling/BASELINE.md#tracking-http-do-trio-de-baselines).
