# Correção numérica do replay — v1

A execução original terminou o processamento, mas reprovou o gate de scores.
O diagnóstico do autor (`report(4).json`, 2026-10-13) registrou 556.824 eventos,
143.945 decisões e 1.453 alertas. Duas decisões divergiram do oráculo offline;
a maior diferença de score foi 0,02664462601589221. As médias de 1 dia diferiram
por 7,1e-15 e 1,4e-14, atravessando cortes do HGB. Não houve mudança de
**elegibilidade ao limiar** entre os dois caminhos originais. Isso não aprova
paridade de scores nem consolida a qualidade dos alertas.

O mesmo modelo reproduziu exatamente os scores quando recebeu as mesmas
features. O erro de `native-result-receipt.json` ausente foi consequência do
gate reprovado; o resultado não chegou a ser publicado. O checkpoint original
permanece evidência da falha, sem migração nem substituição de seus scores.

## Regra e identidade

[`exact_binary64_mean_v1`](../../fraud_detection_mlops/features/exact_binary_mean.py)
soma exatamente os valores binários observados com um acumulador inteiro em
unidades de 2^-1074. Divide a soma exata pela contagem e converte o racional
resultante uma única vez para `float64`. Assim, a ordem da soma não altera a
média e não há escolha arbitrária de casas decimais. As razões dividem o valor
da transação pela média já arredondada; histórico vazio ou média zero gera zero.
A regra preserva os valores observados, não recupera um valor decimal anterior.

O caminho offline usa janelas `RANGE` em DuckDB sobre os eventos já
materializados. O caminho incremental usa consultas sobre o histórico SQLite
observado/confirmado. As fronteiras de tempo continuam independentes, incluindo
pares no mesmo instante, limite inferior inclusivo e feedback no instante exato
excluído. O verificador exige **igualdade exata das 19 features**, além do gate
original de scores (`atol=1e-12`, `rtol=0`) e da simulação independente de decisões.

O [protocolo corretivo](../../references/native_causal_replay_numerical_v1.json)
identifica a regra, o código original e os hashes externos do diagnóstico:

| Artefato original | SHA-256 |
| --- | --- |
| Manifesto da entrada | `20c916866dbd8bc285038f5c34840f198ed1e65c3fd87dbe33a77cbc9c1bfb6f` |
| Checkpoint | `908dd5a063c4d8d1ebd708c7c7da76ca5e19c1858f15298807d9a235b18a00cd` |

Modelo, release, política, limiar 0,8826493808370081, orçamento, janela e tolerância
permanecem vinculados aos artefatos congelados. A correção é técnica e versionada,
sem refit, calibração, nova seleção do limiar ou promoção em produção. Os
executores/cálculos antigos permanecem intactos para reproduzir sua definição.
Isso não atualiza retroativamente o Gold de treinamento nem suas avaliações.

## Procedimento local

Na raiz do projeto, aplique e valide o pacote `fraud-numerical-corrective-replay.zip`,
commite na branch `fix/deterministic-replay-features` e publique-a. Os scripts de
instalação e validação usam apenas fixtures e não executam o replay nativo.
Exija CI aprovada para o commit final antes dos comandos de dados:

```bash
bash ~/Downloads/fraud-numerical-corrective-replay/prepare-correction.sh
bash ~/Downloads/fraud-numerical-corrective-replay/run-correction.sh --stop-after 5000
bash ~/Downloads/fraud-numerical-corrective-replay/run-correction.sh
bash ~/Downloads/fraud-numerical-corrective-replay/verify-correction.sh
```

O preparo consome `data/replay/native-input-v1/` e confere
`data/replay/native-september-v1/checkpoint.sqlite`, usando o recibo externo da
entrada original. **Não refaça `prepare-native.sh` nem apague o banco original.**
Nenhuma partição Silver é reaberta. A entrada corretiva contém um novo oráculo
independente; os eventos são lidos do artefato original identificado pelo hash.

| Novo artefato | Caminho |
| --- | --- |
| Entrada e oráculo | `data/replay/native-input-numerical-v1/` |
| Recibo externo da entrada | `data/replay/native-numerical-input-receipt.json` |
| Checkpoint e avaliação | `data/replay/native-september-numerical-v1/` |
| Recibo externo final | `data/replay/native-numerical-result-receipt.json` |

`--stop-after` conta novos eventos. Após uma interrupção, repita `run-correction.sh`
para processar apenas o sufixo não confirmado. Falha do gate final preserva o novo
checkpoint e impede a publicação do resultado; resultado/recibo são publicados
juntos. `verify-correction.sh` verifica hashes e recompõe a avaliação sem refit,
scoring novo ou alteração dos checkpoints. Destinos redirecionados, conflitos de
recibos, mudanças em entradas/código/ambiente e sobreposição com artefatos originais
causam rejeição. Não ignore esse erro nem amplie tolerâncias para contorná-lo.

## Evidências e leitura do resultado

O relatório exige cobertura integral dos eventos e feedbacks, 15 dias avaliados,
paridade independente e preservação do hash original. `comparison_to_original`
conta mudanças de features/scores, elegibilidade, status de triagem, alertas
adicionados/removidos e alteração da transação que disparou cada alerta.
A comparação foi medida: um score mudou em relação ao original, sem mudança
de elegibilidade, status ou alertas; isso é distinto da paridade offline/online.

`quality` consolida alertas reais, precisão, recall de cliente-dia, capturas/perdas,
ocupação e comparação retrospectiva. O resultado informado em 13/10 fornece
evidência nativa do ensaio acelerado no ambiente do autor, com os limites de
inspeção registrados abaixo. Os testes gerados demonstram os mecanismos, incluindo
os cortes 33,769999999999996 e 79,545; a evidência nativa é um registro separado.

Os custos medidos continuam referentes ao processamento serial acelerado, sem
pacing. Latência desde emissão, transporte, backlog, recuperação cronometrada e
SLA continuam pendentes. Não somar as métricas da execução original às da corretiva
como se fossem um único ensaio, nem comparar throughput sem considerar o custo
da nova agregação exata. Não há publicação de alertas em sistemas externos.

## Resultado nativo informado pelo autor — 2026-10-13

O autor forneceu `run` e `verify` com `status: success` e `complete: true`.
Os relatórios coincidem, excluindo `reused`, presente apenas no retorno do run.
A [evidência reconciliada](../../references/evidence/native_numerical_correction_author_validation_2026-10-13.json)
preserva o resultado verificado e o hash do texto fornecido. Os 556.824 eventos
compreendem 278.412 transações e 278.412 feedbacks, incluindo aquecimento;
143.945 transações foram pontuadas na janela de 16–30/09.

A paridade exata dos 19 atributos e o gate original de scores passaram. O
checkpoint original conservou SHA-256
`908dd5a063c4d8d1ebd708c7c7da76ca5e19c1858f15298807d9a235b18a00cd`.
O manifesto corretivo foi identificado por
`87b42e291fc7d3f8b21f2c7048b08aaf9142cd6c06d151c9489d5b65330eb519`
e o recibo final por
`ec1a8281753e4be5c0cd5ebf8d413b3c9ba9b4c79388b9abcff0e6e590e54665`.
Esses hashes foram reportados pelo executor do autor; os arquivos nativos
não foram transferidos para inspeção independente do assistente.

Em relação ao estado original, um score mudou, com diferença máxima
0,015877364027437224. Esse campo compara versões persistidas; não é uma falha
de paridade restante. Não houve mudança de elegibilidade, status, alertas
adicionados/removidos ou transação disparadora. Os 1.453 alertas foram mantidos.

| Indicador nos 15 dias | Fila causal | Top-100 retrospectivo |
| --- | ---: | ---: |
| Alertas | 1.453 | 1.500 |
| Clientes-dia fraudulentos capturados | 768 | 800 |
| Clientes-dia fraudulentos perdidos | 358 | 326 |
| Precisão agregada | 52,86% | 53,33% |
| Recall agregado | 68,21% | 71,05% |

A fila usou 96,87% da capacidade; sobraram 47 vagas e 9 dias esgotaram o
orçamento. Houve 91 clientes-dia elegíveis suprimidos pelo limite, sem implicar
que todos fossem fraudulentos. O ranking transacional obteve AP 0,578783 e
ROC AUC 0,876322 em 1.256 transações fraudulentas. Há 1.126 clientes-dia
fraudulentos; as métricas não usam o mesmo denominador.

A precisão agregada da fila é 768/1.453 e o recall agregado é 768/1.126. As
médias diárias são, respectivamente, 52,8433% e 68,6395%; não confundir com
as taxas agregadas ou com a precisão diária @100 retrospectiva de 53,33%.
Captura considera fraude em qualquer momento do cliente-dia, inclusive depois
do alerta, e não equivale a prevenção ou recuperação financeira.

As duas invocações registradas confirmaram primeiro 5.000 e depois 556.824 eventos.
Seus nomes constam no resultado, mas o conteúdo dos arquivos de duração, CPU,
RSS e latência não foi fornecido; não há custo ou throughput nativo consolidado
nesta atualização. A publicação `c819d65` e a [CI aprovada](https://github.com/wanderson42/fraud-detection-mlops/actions/runs/38487732538)
foram conferidas separadamente; a saída de terminal não comprova
independentemente o SHA do checkout usado na execução.

Este marco encerra o incidente técnico neste ensaio e consolida a qualidade
descritiva da fila. Pacing 1x, emissão programada, backlog, transporte, SLA e
recuperação cronometrada continuam pendentes. Não execute novamente o replay
para atualizar esta documentação; preserve entradas, checkpoints e recibos.

### Verificação posterior ao fechamento documental

Os scripts do pacote corretivo original conferem também os hashes dos documentos
incluídos naquela entrega. Após esta atualização documental, uma auditoria do
resultado existente deve usar a CLI nativa abaixo, que verifica os artefatos
operacionais e a implementação preservada, sem depender dos hashes antigos dos
documentos. Não é necessário executar essa auditoria para aplicar a atualização.

Na raiz do projeto, com o ambiente e os artefatos originais preservados:

```bash
poetry run python -m fraud_detection_mlops.streaming.replay.numerical_corrective_replay verify \
  --input data/replay/native-input-numerical-v1 \
  --manifest-sha256 87b42e291fc7d3f8b21f2c7048b08aaf9142cd6c06d151c9489d5b65330eb519 \
  --output data/replay/native-september-numerical-v1 \
  --receipt-digest ec1a8281753e4be5c0cd5ebf8d413b3c9ba9b4c79388b9abcff0e6e590e54665
```

O comando é somente de verificação: lê os eventos e checkpoints existentes e
recompõe a avaliação, sem nova inferência, preparo, refit ou acesso a Silver.
