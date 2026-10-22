# fraud-data-jobs

Chart de Jobs temporários, instalado pelo controlador
`scripts/platform/kind_pipeline.py` no namespace `fraud-platform`.

Uma release exclusiva por execução começa com Silver; o upgrade acrescenta
Gold após publicação e restauração independente de Silver. Os Jobs usam imagens
locais identificadas e três containers: download, transformação e publicação.
Nenhum Service, PVC, Secret, RBAC ou recurso das releases existentes é alterado.

Values contêm run/owner IDs, imagens e bundles, sem credenciais. O Secret existente
é referenciado somente nos containers S3. O controlador confere ownership,
imagens efetivas, conclusão e preservação da fundação antes de remover sua release.

Runbook: `docs/operations/KIND_PIPELINE.md`. O chart renderizado não comprova
execução nativa, isolamento de rede ou migração primária. As fontes deste chart
fazem parte dos pins da imagem; fechamento de evidência deve atualizar o runbook,
sem alterar estes arquivos depois do build aceito.
