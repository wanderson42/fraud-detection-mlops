# Runtime da interface de investigação

Streamlit e Plotly têm ambiente separado: `make portal-install` instala os wheels
fixados por hashes em `.portal-venv`; não muda Poetry, pesos ou runtime da API.
`requirements.in` define as dependências diretas; `requirements.txt` fixa a árvore.
O lock de testes acrescenta pytest; `tox -e portal-ui` usa somente esse ambiente.
Não copie `.portal-venv` para o Git ou para o bundle de entrega.

Após alterar uma dependência, regenere os dois locks para Python 3.14 com uv pip
compile e `--generate-hashes`, revise a árvore e execute os testes de interface.
uv é ferramenta de manutenção do lock; a instalação do usuário usa venv/pip.

[Operação, jornada, dados e aceite](../../docs/operations/FRAUD_PORTAL.md).
A aplicação local é cliente do backend KinD, sem imagem ou chart adicional neste recorte.
