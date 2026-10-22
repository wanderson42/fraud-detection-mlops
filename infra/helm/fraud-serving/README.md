# Serving de laboratório via Helm

Chart dedicado à API já qualificada, com uma baseline blue e gateway permanentes
no laboratório. Durante o ensaio, adiciona uma candidata green de mesma imagem e
pesos com outra identidade de release. Um Service ClusterIP seleciona o slot
ativo. O gateway mantém seu pod/configuração enquanto o seletor muda.

Cada backend recebe os três arquivos de release por init container e emptyDir;
a API monta o modelo somente para leitura. A imagem auxiliar contém apenas as
releases sintéticas e o copiador/verificador. O runtime da API é o mesmo arquivo
de imagem qualificado, importado sem rebuild. Não usa modelo binário em ConfigMap,
PVC compartilhado ou credenciais de armazenamento nos pods.

O chart fixa uma réplica por slot e permite zero para a falha controlada da green.
Sem NodePort, LoadBalancer, ingress público, HPA ou permissões de ServiceAccount.
O controlador verifica também os digests CRI reais e os UIDs dos endpoints.
Use `docs/operations/KIND_SERVING.md`; valores diretos não substituem os gates.
