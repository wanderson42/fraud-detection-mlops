# Consumo da entrega qualificada no KinD

Entrada: diretório de entrega com `delivery.json`, `image.tar`, releases e recibos,
acompanhado do commit e SHA-256 do descritor obtidos do produtor confiável.
O verificador existente valida os bytes antes de qualquer acesso ao Docker.

O runtime qualificado é carregado sem rebuild e recebe uma tag local exclusiva.
O carrier de releases usa Python 3.14.4 por digest, sem dependências pip, com três
arquivos blue e três green, mais o copiador/verificador. Seu contexto de build é
novo e restrito; não recebe checkout, imagem original, dados ou credenciais.
A candidata muda apenas o manifesto/identidade operacional; os pesos não mudam.

As três imagens são carregadas somente no node `fraud-platform-control-plane`:
runtime qualificado, carrier e NGINX fixado por digest. O recibo registra IDs
Docker e identidades locais CRI, que não são publicação em registry.
O seeder valida manifesto/hashes antes de copiar para um emptyDir vazio; a API
valida novamente o pacote e o score de controle ao iniciar.
