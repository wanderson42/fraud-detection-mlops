# Acessos locais com nohup e logs

## Uso cotidiano

Os workloads continuam instalados e supervisionados pelo Kubernetes. Os comandos
abaixo gerenciam os processos locais de acesso: port-forwards dos serviços e a
interface Streamlit. Não instalam Helm, iniciam dados, treinam ou trocam modelos.
Com o cluster pronto e o snapshot do portal já preparado:

```bash
make access-up
make access-status
```

A abertura usa `nohup`, nova sessão de processo, entrada em `/dev/null` e stdout/
stderr no log privado do serviço. A URL só é apresentada como pronta após resposta
HTTP 200 do endpoint de saúde. A API mantém os gates de Registry/rota/pesos/HTTP
do acesso ativo já aceito. Não é preciso manter os terminais abertos.

| ACCESS_SERVICE | Destino | Porta local |
| --- | --- | --- |
| `api` | Gateway do champion ativo | Efêmera, impressa na saída |
| `mlflow` | Tracking/Registry do KinD | Efêmera, impressa na saída |
| `grafana` | Grafana do KinD | 13001 |
| `prometheus` | Prometheus do KinD | 19091 |
| `exporter` | Exporter do KinD | 19101 |
| `rustfs` | API S3 da fundação KinD | Efêmera, impressa na saída |
| `portal` | Portal Streamlit local | 18501 |

`access-up` abre API, MLflow, Grafana e portal. Os demais acessos são opcionais:

```bash
make access-start ACCESS_SERVICE=prometheus
make access-start ACCESS_SERVICE=exporter
make access-start ACCESS_SERVICE=rustfs
make access-start ACCESS_SERVICE=grafana ACCESS_PORT=13011
make access-start ACCESS_SERVICE=portal PORTAL_ID=investigation-v1
```

A mesma configuração já saudável é reutilizada. Para trocar parâmetros, encerre
esse acesso antes. Uma porta fixa ocupada é recusada; não se encerra o processo
que a ocupa. API, MLflow e RustFS usam portas efêmeras. PostgreSQL permanece
interno: não há necessidade de abrir uma conexão administrativa para usar o
produto. A console RustFS não é publicada pelo chart atual.

## Logs e parada

```bash
make access-logs ACCESS_SERVICE=api ACCESS_LOG_LINES=100
make access-stop ACCESS_SERVICE=api
make access-start ACCESS_SERVICE=api
make access-stop-all
```

Arquivos privados em `data/storage/kind-platform/local-access/<serviço>/`:

- `<id>.log`: stdout e stderr daquela abertura; cada reinício mantém o log anterior.
- `current.json`: PID, boot, instante de criação do processo, comando, diretório,
  grupo e URL; usado para conferir que a parada pertence à abertura registrada.
- `operation.lock`: impede abertura/parada simultânea do mesmo acesso.

Esses arquivos já são excluídos por `/data/` no `.gitignore`; a subpasta também
está explicitamente listada. Os contextos Docker usam allowlists iniciadas por
`**` sem liberar `data/`, portanto logs/recibos não entram nas imagens. Os ignore
files Docker são preservados para não alterar pins das imagens aceitas.

Os logs de acesso não substituem os logs dos containers. São diagnóstico de
abertura, health checks e port-forward. O histórico de requisições do workload
continua nos logs Kubernetes e na observabilidade. Os logs locais não são
apagados pelo stop; remova os antigos manualmente quando não forem mais úteis.

A parada confere PID e identidade antes de sinalizar o grupo criado por esta
abertura. Não usa `pkill`, busca por nome, contexto global kubectl ou portas para
escolher processos. Não encerra o projeto de energia, acessos abertos manualmente,
o cluster ou os workloads. Faça `access-stop-all` antes de uma parada planejada
do cluster. Após um reboot, execute novamente `access-up`: nohup sobrevive ao
fechamento do terminal, não ao desligamento da máquina.

## Falhas e identidade

Abertura com erro mantém o log e retorna código não zero. Uma falha no meio de
`access-up` deixa os acessos anteriores disponíveis para diagnóstico; não declara
que o conjunto inteiro foi aberto. Consulte `access-status` e o log do serviço.
`access-status` confere processo e saúde HTTP, sem realizar outra qualificação
científica ou substituir a verificação governada de Registry. Após promoção,
manutenção, recriação de pod ou interrupção do node, feche e abra o acesso para
obter uma URL e uma verificação atualizadas. Não há reinício automático que
esconda falhas ou use o serving sintético como fallback.

`access-up` reabre API, MLflow, Grafana e portal. Prometheus, exporter e RustFS
são acessos locais opcionais: após `access-stop-all`, podem aparecer como
`stopped_or_stale` até executar `access-start` para cada um. Isso não significa
que os workloads Kubernetes estejam parados. O ciclo canônico de 2026-10-23
verificou os serviços e reabriu os quatro acessos principais.

O portal continua consultando o snapshot aceito. Deixá-lo em segundo plano não
faz chegar novas transações; a sessão operacional contínua é o próximo recorte.
Os comandos de primeiro plano continuam disponíveis (`platform-api`,
`platform-mlflow`, `platform-grafana`, `portal-open`). Não execute simultaneamente
as duas formas na mesma porta fixa.

## Validação

```bash
.venv/bin/python -m tox -e py314 -- tests/infra/test_local_services.py tests/infra/test_platform_entrypoints.py tests/storage/test_managed_paths.py tests/storage/test_persistence_inventory.py
```

Os testes de ciclo real exigem Linux e `/proc` no mesmo namespace dos processos.
Verificam nohup/SIGHUP, stdin nulo, captura de stderr, reutilização sem duplicar,
reinício com logs preservados, PID reutilizado e parada sem afetar outro processo.
No ambiente do autor, o aceite nativo deve incluir `access-up`, URLs, status, logs
e `access-stop-all`. Nenhuma dessas rotinas demanda repetir treino ou ensaiar falha
de modelo.
