# Política de cores dos dashboards

A política `semantic_colors_v1` substitui thresholds implícitos por uma
interpretação explícita. Os números, consultas PromQL, regras de alerta,
reduções, unidades e modelos permanecem iguais. Cor não é evidência adicional
sobre a qualidade do modelo e não autoriza treino ou promoção.

| Cor | Significado | Aplicação |
| --- | --- | --- |
| Azul | Informação sem aprovação/reprovação automática | Contagens, AP, precisão, recall, velocidade, latências, ocupação e estado de conclusão |
| Verde | Condição válida explicitamente verificada | Collector válido; zero violações ou lacunas observadas |
| Amarelo | Investigar | Divergência de feature acima de 0,1 com suporte; lacuna de medição; atraso positivo de feature |
| Vermelho | Falha/violação explícita | Collector inválido; ausência, valor inválido, schema/relógio inválido ou relógio futuro |
| Cinza | Indisponível ou não aplicável | Ausência de amostra; comparação indisponível; sessão parada |

## Aceite da política — 2026-10-21

A configuração `semantic_colors_v1` foi aprovada no Grafana nativo antes e depois
da recriação do exporter. Cores, thresholds, mappings, textos e legenda foram
conferidos pela API; o autor confirmou o resultado visual após aplicar o ajuste.
O controlador registrou **62,36 s** nesta execução. O anexo contém o recibo, sem
os tempos de pytest/tox; esses tempos não foram inferidos.

As contagens continuam em 556.824 eventos, 143.945 decisões e 1.453 alertas.
Oito regras saudáveis, proxy Grafana e quatro dashboards aprovados. As métricas,
os arquivos originais e copiados e os três PVCs coincidem com o aceite anterior.
O exporter ganhou outro pod, mantendo Deployment/PVC; os demais recursos foram
preservados. Não houve replay, treino, novo resultado de qualidade, firing ou
validação de reboot por esta atualização.

[Evidência completa](../../references/evidence/dashboard_color_policy_author_validation_2026-10-21.json).
O flag visual do recibo automatizado continua falso: a revisão humana é registrada
separadamente, sem editar o recibo. Próximo: parada/subida, retomada após reboot
e fechamento integrado do marco de infraestrutura.

## Leitura sem depender apenas da cor

Todos os painéis numéricos têm `noValue` e mappings de null/NaN em cinza com texto
**Sem dados / sem suporte**. Zero continua sendo um número real, não ausência.
Collector mostra **Válido/Inválido**; sessão mostra **Em execução/Parada**;
comparação mostra **Comparação disponível/Sem comparação**. Cada painel tem
explicação no tooltip e cada dashboard mantém uma legenda textual visível.
A indicação de sessão parada é esperada num replay concluído.

Percentuais e AP não recebem metas arbitrárias: 80% não é um requisito implícito
deste projeto. Alertas, capturas/perdas e supressões por orçamento são resultados
descritivos do período/política, sem classificação automática pelo seu volume.
Latências ficam informativas: esta execução acelerada não comprova SLA em 1×.

## Limites e suporte

A divergência de features só é exportada quando a comparação tem suporte e
qualidade suficientes. Ela fica azul até **0,1 inclusive** e amarela estritamente
acima. Como o Grafana aplica thresholds inclusivos, o JSON usa o próximo float
representável após 0,1 (`0.10000000000000002`), preservando o critério `> 0.1`
da regra. O amarelo sinaliza investigação, sem conclusão de concept drift.

O Jensen–Shannon dos **scores** permanece azul. Sua regra depende também de
1.000 observações e duração; colorir esse número isolado como alerta perderia
o gate de suporte. O estado da regra pode ser consultado no Prometheus.

Contagens inteiras de violação passam a vermelho em 1. Para ausência por feature,
a menor fração positiva é 1/1000, pois a janela possui no máximo 1.000 decisões.
Lacunas de medição ficam amarelas a partir de 1. Atraso positivo de feature,
com resolução de nanossegundos, fica amarelo a partir de 1e-9 s. Esse destaque
não cria uma nova regra de alerta nem um SLA; relógio futuro é violação separada.

## Aplicar no KinD existente

O pacote inclui o fechamento funcional anterior com seu recibo original. O
refinamento visual é uma alteração posterior, com validação própria aprovada
no Grafana nativo. Não editar recibos anteriores para apresentá-los
como teste destas cores.

```bash
poetry run python -m scripts.platform.dashboard_colors validate &&
poetry run python -m scripts.platform.kind_observability up &&
.venv/bin/python -m tox -e kind-observability
```

`up` atualiza o chart e pode recriar os pods da stack de observabilidade devido
ao checksum de configuração. Mantém imagens, dados e PVCs existentes. O gate
confere agora também cores, thresholds, mappings, textos e legenda pela API
Grafana, além das verificações funcionais anteriores. A conferência visual
humana dessa atualização foi confirmada pelo autor; novas alterações exigem
nova conferência.

Depois abrir o Grafana como no [runbook](KIND_OBSERVABILITY.md). Conferir contagens
azuis, collector **Válido** verde, sessão **Parada** cinza e ausência de erros
vermelhos quando os contadores correspondentes são zero. As quatro divergências
da fonte conhecida estão abaixo de 0,1 e devem aparecer azuis. AP/precisão/recall
continuam azuis, com os mesmos valores. Nenhum painel deverá herdar o threshold
vermelho genérico 80.

Para Compose, os três dashboards originais recebem a mesma política; o provider
lê os arquivos montados na atualização periódica. Não requer build do exporter.
A aplicação nativa em Compose não foi revalidada por este ajuste.

## Vínculo entre imagens e configuração

Os recibos antigos incluíam chart e controlador de host entre os pins de imagem,
embora esses arquivos não estejam no Dockerfile. O controlador passa a separar
`runtime_pins()` (entradas reais da imagem e lock de registry) dos pins completos
de implantação, registrados no gate. Assim uma mudança de painel não força
rebuild ou reimportação da imagem, sem relaxar a integridade do runtime.

Recibos antigos são aceitos apenas se coincidirem integralmente com um perfil
conhecido, protegido por SHA-256, e seus pins de runtime forem iguais aos atuais.
Novos recibos de imagem usam somente os pins de runtime. Todos mantêm node,
owner, tag e identidades CRI verificados. Nenhum recibo é reescrito. Mudanças
reais no collector, contrato, worker, Dockerfile ou lock continuam bloqueadas.

Referências: [thresholds Grafana](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-thresholds/),
[value mappings](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-value-mappings/).
