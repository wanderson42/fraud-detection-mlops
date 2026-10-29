# Acabamento dos dashboards da sessão

Esta revisão reúne as 19 features em uma consulta instantânea por tabela,
extrai o nome da feature para uma coluna e mostra apenas Feature e Valor.
Os filtros de validade da sessão, capacidade de detalhes e linhas observadas
permanecem. Ausência de dados não vira zero. Os títulos longos ficam abreviados;
a descrição preserva o significado completo. O painel original da sessão fica
intacto. Não há build de imagem, treino, scoring ou mudança de política.

A transição aceita em memória os hashes anteriores exatos, sem reescrever recibos.
Os planos anteriores ficam como histórico das respectivas versões; use o comando
de apresentação para verificar esta revisão, em vez de reaplicar o plano antigo.

## Aplicar na VM

Aplicar o pacote, executar os testes e commitar antes da manutenção.
Encerrar o observador com Ctrl+C e confirmar que não há outro watch ativo.
Não remover o arquivo de lock. A atualização usa a release Helm e seus valores
atuais, preservando imagem do receiver e recibos. Pode haver rollout dos pods de
observabilidade; o acesso persistente ao Grafana pode precisar ser reiniciado.

```bash
poetry run python -m scripts.platform.session_dashboard_presentation apply \
  --id grafana-presentation-v1 --session-id repro-arrival-200-v1 --no-other-writers
```

O comando grava o plano e a revisão Helm anterior antes de alterar o cluster.
Confere os quatro dashboards pela API, incluindo transformações e apresentação,
os agregados, os volumes, os arquivos da sessão e os recibos de monitoramento.
O aceite visual continua dependendo da captura dos painéis na VM.

Após sucesso, reiniciar o observador. Se o acesso falhar, reiniciar apenas o
encaminhamento Grafana com access-stop/access-start; manter o túnel SSH aberto.

## Recuperar

Se o rollout ou a verificação falhar, manter o observador parado e preservar a
saída. O rollback usa a revisão Helm guardada e pode funcionar sem receiver pronto:

```bash
poetry run python -m scripts.platform.session_dashboard_presentation rollback \
  --id grafana-presentation-v1 --session-id repro-arrival-200-v1 --no-other-writers
```

A recuperação recusa revisões Helm de outras manutenções. Restaura o chart anterior
no cluster e preserva o código revisado e os recibos; os painéis voltarão à versão
anterior. Não iniciar outra manutenção antes de resolver esta.

## Validação local

Sete testes em Python 3.14: tabelas, títulos, transição exata e rejeição de deriva,
backup anterior ao Helm, falha e rollback, bloqueio por observador/manutenção e
verify sem instalação. Helm lint estrito e renderização real aprovados no perfil
session-only. Não houve acesso ao KinD da VM nem revisão visual nativa desta revisão.
