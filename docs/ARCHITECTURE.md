# Organização e controle de complexidade

O código se organiza por responsabilidades que já existem. O pacote continua na
raiz do checkout; não criamos pastas vazias para infraestrutura futura.

Esta é a arquitetura implementada. Metas, prioridade e critérios das entregas
futuras têm uma referência única no [mural do projeto](ROADMAP.md).

| Código | Responsabilidade | Testes |
| --- | --- | --- |
| `artifacts.py` | Escrita JSON atômica e SHA256 em memória limitada | `tests/test_artifacts.py` |
| `bronze.py`, `profiling.py`, `silver.py`, `gold.py` | Aquisição, diagnóstico e contratos de dados | `tests/test_<módulo>.py` |
| `features.py`, `temporal.py`, `eda.py` | Features causais, protocolo e EDA | Testes correspondentes na raiz |
| `modeling/baseline.py` | Política fixa e verificação não executável de artefatos | `tests/modeling/test_baseline.py` e `test_train.py` |
| `modeling/train.py` | Ajuste, seleção na validação e publicação da execução | `tests/modeling/test_train.py` |
| `modeling/metrics.py` | Métricas de ranking e priorização diária | `tests/modeling/test_metrics.py` |
| `modeling/persistence.py` | Persistência skops e contrato de recarga | `tests/modeling/test_persistence.py` |
| `modeling/tracking.py` | API nativa MLflow e CLI de UI/verificação | `tests/modeling/test_tracking.py` |
| `modeling/diagnostics.py` | Diagnóstico da validação com modelo existente, permutação e SHAP | `tests/modeling/test_diagnostics.py` |
| `modeling/comparison.py` | Comparação pareada e gate de desenvolvimento | `tests/modeling/test_comparison.py` e `test_experiments.py` |
| `modeling/experiments.py` | Ablações limitadas, checkpoints e retomada | `tests/modeling/test_experiments.py` |
| `modeling/freeze.py` | Recibo da referência e política final, com paridade na validação | `tests/modeling/test_freeze.py` |
| `modeling/evaluation.py` | Avaliação do holdout congelado, decisão e publicação nativa | `tests/modeling/test_evaluation.py` |

As fixtures compartilhadas de modelagem ficam em `tests/modeling/conftest.py`.
Arquivos de teste não importam uns aos outros. O pytest usa `importlib`; tox e CI
continuam executando a mesma suíte com as versões do lockfile. Os cenários de
integração usam dados sintéticos pequenos, diretórios temporários e SQLite local.

O espelhamento é por responsabilidade: `silver.py` fica na raiz do pacote e seu
teste na raiz de `tests/`; `modeling/train.py` corresponde a
`tests/modeling/test_train.py`. Não é necessário repetir o nome do pacote dentro
de `tests/` nem criar um arquivo de teste para cada arquivo Python. Uma integração
pode cobrir vários módulos; [Testing](TESTING.md) descreve a organização e os riscos.

## Decisões desta refatoração

- A migração histórica foi concluída; o runner de conversão e seus testes específicos
  saíram da árvore ativa. O Git e as evidências preservam o histórico.
- Novos modelos usam o suporte nativo do MLflow para skops. Evitamos wrappers pyfunc
  próprios, um registry prematuro e um sistema paralelo de retomada de migrações.
- Operações de arquivo compartilhadas têm interfaces públicas. Modelagem não acessa
  funções privadas de treinamento ou Bronze para persistir artefatos.
- Importar o pacote não carrega `.env` nem altera o logger do processo.
- Os exemplos vazios `predict.py` e `plots.py` foram removidos. Serving será criado
  quando existir um contrato de inferência; gráficos reais já estão na EDA.
- `baseline_v2` versiona a mudança de persistência. `baseline_v1` permanece verificável
  como evidência histórica, sem qualquer carregamento ou conversão de joblib.

## Critérios para próximas mudanças

1. Implementar uma necessidade concreta e dizer qual operação ou falha ela resolve.
2. Preferir a API da biblioteca antes de criar uma abstração própria.
3. Separar responsabilidades quando isso reduzir acoplamento ou esclarecer o fluxo;
   a quantidade de módulos ou linhas não é uma meta isolada.
4. Testar riscos reais: causalidade, população de avaliação, integridade, recarga e
   falhas de publicação. Remover testes de funcionalidades removidas.
5. Não manter caminhos antigos de escrita indefinidamente. Compatibilidade de leitura
   deve ser pequena e necessária para as evidências que decidimos preservar.
6. Revisar dependências e documentação junto com o código. Recursos novos precisam
   justificar seu custo de manutenção no portfólio.

Os runners de dados e de experimentos concentram verificação, publicação e
recuperação; merecem atenção conforme evoluírem. Separar um componente é útil
quando ele tiver contrato próprio, duplicação ou acoplamento que dificulte uma
mudança. Dividir arquivos apenas por tamanho acrescentaria navegação sem resolver
esses problemas. Novas integrações devem preservar interfaces pequenas.

O layout atual e os caminhos padrão suportam execução a partir do checkout.
O tox instala o projeto de forma editável; essa validação não comprova um wheel
independente com todos os recursos de `references/`. Antes do serving/container,
definiremos explicitamente o empacotamento desses recursos e os caminhos operacionais.
Migrar para `src/` só fará sentido junto desse trabalho, sem depender de atalhos em
`sys.path`. Docker, Prefect e armazenamento remoto serão entregas com escopo próprio.
