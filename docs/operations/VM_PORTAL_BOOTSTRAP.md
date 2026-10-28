# Portal da sessão verificada na reprodução limpa

A sessão `repro-arrival-200-v1` foi concluída e reconciliada na VM: 200 decisões
HTTP, 13 alertas e feedbacks causais, usando o modelo treinado nesse ambiente.
O exportador antigo exigia especificamente o recibo `rehearsal.json` do ensaio
histórico de pausa/retomada. O verificador da sessão produz outro recibo válido:
`verification-timing-*.json`. Esses aceites têm escopos diferentes.

## Adaptação

O caminho existente `portal-prepare` com `PORTAL_WITHOUT_EXPLANATIONS=1` aceita
agora uma sessão v1 reconciliada, quando não existe recibo legado. Ele exige:

- `session.json` vinculado ao flow ID e aos bytes de `input.json`.
- Recibo real `verification-timing-*.json` que identifica o checkpoint atual.
- Reconstrução independente completa pelo verificador HTTP já existente.
- Concordância de todos os campos da reconciliação, contagens, modelo e release.
- Arquivos preservados e ausência de writers/sidecars SQLite.
- Verificação da API/Registry corrente pelo exportador existente.

Nenhum `rehearsal.json` é criado ou renomeado. O snapshot registra
`reconciled_session_not_product_pause_resume_rehearsal`. Um recibo legado
existente, inclusive inválido, continua seguindo o gate histórico: o caminho
novo não é fallback para contornar falha daquele gate.

O código das explicações históricas e seus pins permanecem intactos.
A UI continua usando seu runtime isolado; o exportador roda no ambiente
científico. Não são adicionadas dependências ou alterações no modelo.

## Preparação inicial do portal da VM

Depois de testar e commitar a alteração no checkout da VM:

```bash
make portal-prepare \
  PORTAL_ID=repro-investigation-v1 \
  PRODUCT_FLOW_ID=session-repro-arrival-200-v1 \
  PORTAL_WITHOUT_EXPLANATIONS=1 \
  PRODUCT_NO_OTHER_WRITERS=1 &&
make portal-verify PORTAL_ID=repro-investigation-v1 &&
make portal-session-link \
  PORTAL_ID=repro-investigation-v1 \
  PORTAL_SESSION_ID=repro-arrival-200-v1 &&
make portal-session-verify PORTAL_ID=repro-investigation-v1
```

Este primeiro snapshot contém 200 decisões e zero explicações SHAP, explicitamente
indicadas como não preparadas. Não reutiliza explicações do Alienware nem afirma
ter reproduzido essa parte do produto. Os snapshots são imutáveis: uma futura
preparação com SHAP exige um novo snapshot/revisão e vínculo explícito, mantendo
um único portal em execução.

A evidência de validação pode ser preparada separadamente com
`PORTAL_EVALUATION_ROUND=repro-v1` e ID de desempenho próprio da VM. Isso reutiliza
a validação já observada, não constitui novo holdout e não faz fit.

## Próximos gates

Abrir o portal em loopback e acessá-lo pelo túnel SSH, conferir saúde HTTP e
revisar visualmente. Fechar o bootstrap do monitoramento sem depender do replay
histórico ausente. A adaptação presente não instala Prometheus/Grafana nem
reivindica aceite do monitoramento ou explicações.

Os recibos antigos de eventos permanecem vinculados à revisão em que foram
produzidos. Não reescrever seus commits ou hashes para acompanhar esta alteração
de código: preservar os artefatos e seus verificadores da revisão correspondente.
