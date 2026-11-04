# Retomada identificada da cadeia Silver → Gold

**Integração:** aceite nativo concluído; PR #24 aberto, sem merge nesta revisão
(2026-11-04). Conferir CI do último commit antes de integrar e sincronizar main.
Os comandos deste guia pertencem à branch do incremento até essa integração.

## Decisão e escopo

A análise sobre `45fddeb` encontrou retomada de sessão por checkpoint e proteção
local de concorrência, mas a antiga cadeia `kind_pipeline.rehearse()` gerava outro
UUID a cada chamada. Se o controlador parasse antes do recibo final, repetir
`round-data` podia iniciar outra tentativa enquanto Jobs anteriores continuavam
no Kubernetes. Isso é uma conclusão da leitura do código, não um incidente
observado na VM aceita.

Este incremento acrescenta coordenação identificada **nos controladores atuais**.
Não instala Prefect, não cria outro serviço e não modifica produtores científicos,
contratos, política de treino, limiares, modelos ou imagens já aceitas. A opção
histórica `kind_pipeline.rehearse()` continua sendo um ensaio novo a cada chamada;
ela não é o comando de retomada e não incorpora tentativas históricas.

Para rodadas inicializadas com esta implementação, `round-data` usa o owner UUID
da rodada como ID estável da cadeia. Um plano persistido precede a criação dos
Jobs. Rodadas antigas já concluídas preservam `data.json` e os verificadores
existentes. Rodada antiga incompleta é recusada para revisão explícita; não se
cria outra tentativa por baixo de um estado desconhecido.

## Identidades e estados

Os arquivos privados ficam em
`data/storage/kind-platform/pipeline/runs/<32-hex>/`:

| Arquivo | Papel |
| --- | --- |
| `plan.json` | ID, Bronze, ownership da fundação, imagens e fontes do controlador |
| `operation.lock` | Exclusão de duas invocações locais da mesma tentativa |
| `values-silver.json`, `values-gold.json` | Plano Helm imutável por etapa |
| `dispatch-*.json` | Intenção gravada antes de solicitar criação do Job |
| `job-*.json` | Nome e UID do Job reconhecido, ligado ao plano |
| `stage-*.json` | Recibo publicado e restore independente, verificados e fechados por etapa |
| `cleanup.json` | Intenção de remover somente a release dessa tentativa, após validar ambas as etapas |
| `result.json` | Sucesso final após limpeza conferida; mantém formato consumido pela rodada |
| `failure-*.json` | Diagnóstico privado da invocação interrompida; não representa sucesso |

Novos recibos são publicados completos por arquivo temporário, fsync e link
exclusivo; arquivos concluídos não são sobrescritos. Uma interrupção pode deixar
um temporário privado, que não é adotado como recibo. Não apagar arquivos para
contornar uma recusa.

A retomada reaproveita um Job existente somente após conferir run/owner, plano
renderizado, imagens e UID conhecido. Saídas já registradas passam novamente
por verificação de manifesto, payloads e semântica. Se a publicação terminou mas
o recibo local não foi salvo, o mesmo Job é consultado; pode ser necessário um
novo download de verificação, mas o produtor não é disparado novamente.

Job falhado, Job despachado ausente, UID substituído, fonte/imagem alterada ou
bytes inválidos fazem a execução parar para inspeção. **Não há retry cego nem
recriação automática de Job perdido.** Uma interrupção entre intenção e criação
pode exigir inspeção manual: não se afirma execução exatamente uma vez sob
qualquer falha. Uma tentativa pode ser retomável ou ser recusada com segurança.

Os CLIs mantêm também o lock de plataforma existente. Isso coordena invocações
no mesmo checkout/armazenamento local; não é lock distribuído para outro host
escrevendo diretamente no mesmo cluster/S3. Outros writers devem continuar
coordenados pelo operador.

## Uso explícito fora de uma rodada nova

Prepare imagens e Bronze pelos procedimentos existentes. Não refaça a rodada
aceita para testar a coordenação. Escolha um novo ID somente para um **ensaio
novo autorizado**, nunca para esconder falha de uma tentativa anterior:

```bash
export PIPELINE_RUN_ID="$(.venv/bin/python -c 'from uuid import uuid4; print(uuid4().hex)')"
make pipeline-run-prepare
make pipeline-run-status
make pipeline-run PIPELINE_STOP_AFTER=silver
make pipeline-run
```

Se houver mais de um bundle Bronze, informe `PIPELINE_BRONZE_BUNDLE_ID` em prepare.
O ID e o diretório retornado precisam ser preservados para todas as retomadas.
`pipeline-run-status` lê somente metadados locais; não atesta saúde atual dos Jobs.
`PIPELINE_STOP_AFTER=gold` pausa após verificar Gold e antes de limpeza/recibo
final, permitindo um ensaio controlado de recuperação desse ponto.

Rodadas novas continuam usando `make round-data ROUND_ID=...`. Esse comando passa
a reutilizar a tentativa vinculada à rodada. Uma rodada completa continua com
seus dados aceitos; nenhuma reexecução é necessária para atualizar documentação.

## Testes de preparação

Os testes usam dados sintéticos reais, bundles/Parquet verificados e object store
em memória, com a fronteira Kubernetes simulada. Cobrem controlador interrompido
com Job preservado, pausa entre etapas, Gold terminado antes de recibo local,
limpeza interrompida, reutilização final, artefato corrompido, Job falhado ou
substituído e exclusão entre processos. Também cobrem o intervalo entre término
da cadeia e persistência do recibo da rodada.

A regressão verifica a cadeia antiga e leitores das sessões/rodadas aceitas.
A transição operacional é exata e precisa estar commitada; nenhum recibo privado
é reescrito. O catálogo de consumidores MLflow tem o hash do controlador revisado,
sem nova chamada MLflow no executor de dados.

## Ensaio nativo opt-in na VM nova

**Executado e aceito pelo autor em 2026-11-04**, conforme o registro abaixo.
A evidência de preparação permanece separada do aceite da VM. Esse ensaio cria novos Jobs Silver/Gold e novos
bundles/restores, consumindo CPU, memória e disco, mas não treina nem pontua o
champion. Use a VM `fraud-clean-install` saudável, Bronze e imagens aceitos,
sessão concluída e observador supervisionado ativo. Não rode writers de dados,
Registry ou outras operações de plataforma simultaneamente.

Após aplicar/testar e commitar o pacote, com as fontes compatíveis e o observador
retomado:

```bash
export FRAUD_PIPELINE_NO_OTHER_WRITERS=1
export FRAUD_PIPELINE_SESSION_ID=clean-install-arrival-v1
.venv/bin/python -m tox -e kind-pipeline-resume
```

Se necessário, informe `FRAUD_PIPELINE_BRONZE_BUNDLE_ID` para selecionar o bundle.
O teste verifica a sessão antes/depois, persiste um run ID e:

1. Inicia o controlador como processo filho e aguarda o Job Silver ativo.
2. Confirma que uma segunda invocação é recusada pelo lock.
3. Termina **somente seu processo filho**, preservando o Job no Kubernetes.
4. Retoma o mesmo UID Silver, pausa, executa Gold e pausa antes do recibo final.
5. Conclui a limpeza, repete a tentativa completa e confere reutilização.
6. Confere sessão, hashes e recursos protegidos sem novas inferências na sessão.

O teste salva `native-acceptance.json` e log privado no diretório da tentativa,
inclusive em caso de falha. Se falhar, **preserve o run ID e o diretório e envie a
saída**; não repita o tox com outro UUID automaticamente e não remova seus Jobs.
Os comandos `pipeline-run-status/run` com o mesmo ID permitem inspeção/retomada
explícitas, mas um estado ambíguo precisa de revisão.

Não interrompe o cluster, não recria PVCs, não altera aliases ou tráfego e não
remove releases fora da tentativa. O gate não demonstra retomada de treino
parcial, HA, perda física do host ou coordenação distribuída.

## Aceite nativo da retomada

A implementação em `87c672f` passou na VM: 290 testes da regressão e um teste
nativo. O teste opt-in levou 84,67 s (tox 100,37 s) e registrou:

| Verificação | Resultado |
| --- | --- |
| Controlador terminado e Job preservado | Mesmo UID Silver `9293c505-c042-40f3-941d-393255771b38` |
| Invocação concorrente | Recusada |
| Retomada das etapas | Sem criação de Jobs substitutos |
| Resultado completo | Reutilizado |
| Recursos temporários | Jobs da tentativa removidos |
| Sessão `clean-install-arrival-v1` | Preservada, sem novas inferências |
| Modelo | Champion preservado; nenhum fit |

Tentativa `dbf3deebc02d41f99b3e0ddcfd7d0a50`, com plano e hashes na
[evidência pública](../../references/evidence/pipeline_run_author_validation_2026-11-04.json). O recibo privado
é `pipeline/runs/<run_id>/native-acceptance.json` sob o workspace KinD. Não foram
recebidos todos os arquivos privados; não atribuímos hash ao recibo nativo em si.
Os hashes exibidos pertencem ao plano, etapas e resultado indicados no terminal.

## Decisão de coordenação para o escopo atual

Manter os controladores existentes para a cadeia limitada e a operação manual
qualificada no mesmo checkout da VM. O gate nativo encerra este incremento de
retomada. A implementação, os testes e a evidência seguem para PR/CI; não se
reexecuta a cadeia apenas para publicar documentação.

Reavaliar um orquestrador quando houver requisito concreto de agendamento/eventos,
operação por múltiplos usuários/hosts ou visibilidade central das execuções.
Uma ferramenta desse tipo continuaria consumindo os contratos de identidade,
verificação e retomada dos efeitos externos; adotá-la não substitui esses gates.
Não foi feita POC de Prefect nem afirmada equivalência entre soluções.

Após integração/sincronização, planejar Terraform local sobre a infraestrutura
estabilizada, protegendo ownership, state, segredos e dados. O aceite não cobre
coordenação distribuída, treino parcial, HA, restore integral ou perda física.
