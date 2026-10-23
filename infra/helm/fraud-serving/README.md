# Chart compartilhado da primeira implantação canônica

A primeira implantação usa este chart com a release `fraud-detector`, pelos
gates de `scripts/operational_round/serving.py`. Blue e gateway permanecem
estáveis durante a troca do seletor; green é o slot alternativo. As atualizações
recorrentes usam o chart `fraud-model-updates` e sua política governada.

Cada backend recebe três arquivos pinados via init container/emptyDir. A API
monta o modelo somente para leitura; o carrier contém as releases e o instalador
idempotente, sem checkout ou credenciais. Runtime, pesos e contrato são conferidos
pelos gates antes do uso. Uma identidade de release diferente não comprova pesos
ou qualidade diferentes.

O chart limita réplicas, usa ClusterIP e não cria PVC, RBAC ou exposição pública.
Seus testes de segurança e estabilidade ficam na suíte da rodada operacional.
A antiga release sintética `fraud-serving` foi aposentada, sem remover este chart
compartilhado. Para instalar, siga
[OPERATIONAL_ROUND.md](../../../docs/operations/OPERATIONAL_ROUND.md).
