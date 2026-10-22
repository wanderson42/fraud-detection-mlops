"""Evaluation evidence for risk teams; the view consumes only versioned JSON aggregates."""

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from scripts.fraud_portal import charts
from scripts.fraud_portal.performance import reader
import streamlit as st


def percentage(value):
    return "Não definido" if value is None else f"{100 * value:.2f}%"


def chart(candidate, kind, prevalence):
    curve = candidate["curves"][kind]
    figure = go.Figure(
        go.Scatter(
            x=curve["x"],
            y=curve["y"],
            mode="lines",
            name=candidate["model_id"],
            line={"color": charts.BLUE, "width": 3},
        )
    )
    if kind == "precision_recall":
        figure.add_hline(
            y=prevalence,
            line_dash="dash",
            line_color=charts.ORANGE,
            annotation_text="Prevalência na validação",
        )
        figure.update_xaxes(title="Recall de transações fraudulentas", range=[0, 1])
        figure.update_yaxes(title="Precisão de transações sinalizadas", range=[0, 1.05])
        title = "Precisão e recall do ranking"
    else:
        figure.add_shape(
            type="line", x0=0, y0=0, x1=1, y1=1, line={"color": charts.ORANGE, "dash": "dash"}
        )
        figure.update_xaxes(title="Taxa de falsos positivos", range=[0, 1])
        figure.update_yaxes(title="Taxa de verdadeiros positivos", range=[0, 1.05])
        title = "Curva ROC · complemento ao ranking"
    return charts.styled(figure, title, 380)


def show(snapshot, portal_sha):
    st.title("Desempenho do modelo")
    st.write("Evidências para entender a priorização e a carga de investigação.")
    path, pin = (
        os.environ.get("FRAUD_PORTAL_PERFORMANCE"),
        os.environ.get("FRAUD_PORTAL_PERFORMANCE_SHA256"),
    )
    if not path or not pin:
        st.info(
            "A avaliação deste snapshot ainda não foi preparada. Execute make portal-performance-prepare; os casos continuam disponíveis."
        )
        return
    try:
        report, _ = reader.load(Path(path), pin, portal_sha, snapshot)
    except (OSError, ValueError, KeyError) as exc:
        st.error("A avaliação não corresponde ao conjunto verificado ou foi alterada.")
        st.caption(str(exc))
        return
    active = next(c for c in report["candidates"] if c["is_snapshot_model"])
    measured, dataset = active["metrics"], report["dataset"]
    st.caption(
        f"Modelo: {active['model_id']} · Registry v{report['registry']['version']} · {dataset['rows']:,} transações de validação"
    )
    st.caption(
        f"Validação temporal: {dataset['first_transaction']} → {dataset['last_transaction']} · Rótulos maduros em {report['labels_mature_as_of']}"
    )
    columns = st.columns(4)
    columns[0].metric(
        "Average Precision",
        f"{measured['average_precision']:.3f}",
        help="Resumo da curva precisão–recall; não é acurácia nem percentual de fraudes detectadas.",
    )
    columns[1].metric(
        "Precisão diária @100",
        percentage(measured["daily_customer_precision_at_100"]),
        help="Média diária da precisão dos até 100 clientes de maior score, calculada offline; difere da política de primeira passagem do limiar.",
    )
    columns[2].metric(
        "ROC-AUC",
        f"{measured['roc_auc']:.3f}",
        help="Capacidade de ordenar pares positivos/negativos; complementa as métricas de carga de investigação.",
    )
    columns[3].metric(
        "Fraudes na validação",
        percentage(measured["fraud_rate"]),
        help=f"{measured['fraud_count']:,} transações fraudulentas dentre {measured['rows']:,}.",
    )
    st.info(
        f"Prever todas as transações como legítimas produziria {percentage(1 - measured['fraud_rate'])} de acurácia e recall de fraude zero. O objetivo é concentrar evidências de risco para investigação."
    )
    st.caption(
        "Esta janela temporal foi usada na seleção dos candidatos. A apresentação reutiliza a avaliação aceita; não é uma nova confirmação independente em holdout."
    )
    comparison, curves, policy, provenance = st.tabs(
        ["Comparação", "Curvas", "Política de alertas", "Rastreabilidade"]
    )
    with comparison:
        st.subheader("Candidatos avaliados nos mesmos dados")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Modelo": c["model_id"],
                        "No snapshot": "Sim" if c["is_snapshot_model"] else "Não",
                        "AP": c["metrics"]["average_precision"],
                        "Precisão diária @100": c["metrics"]["daily_customer_precision_at_100"],
                        "ROC-AUC": c["metrics"]["roc_auc"],
                    }
                    for c in report["candidates"]
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "AP": st.column_config.NumberColumn(format="%.4f"),
                "Precisão diária @100": st.column_config.NumberColumn(format="%.4f"),
                "ROC-AUC": st.column_config.NumberColumn(format="%.4f"),
            },
        )
        st.write(
            "A precisão @100 aproxima a pergunta: quantos dos clientes priorizados têm fraude no dia? AP compara a qualidade do ranking ao longo dos limiares. Nenhuma dessas métricas afirma que cada alerta é uma fraude confirmada."
        )
        st.caption(
            "A apresentação segue o modelo identificado no snapshot, inclusive após uma mudança de algoritmo. Não seleciona nem promove candidatos."
        )
    with curves:
        a, b = st.columns(2)
        for column, kind in [(a, "precision_recall"), (b, "roc")]:
            with column, st.container(border=True):
                st.plotly_chart(
                    chart(active, kind, measured["fraud_rate"]),
                    theme=None,
                    width="stretch",
                    key="quality-" + kind,
                )
        st.caption(
            "Até 250 pontos por curva para visualização. AP e ROC-AUC são calculadas com todas as previsões, sem usar a curva reduzida para estimar áreas."
        )
    with policy:
        queue = report["queue_policy"]
        st.subheader("Fila por cliente e dia · simulação na validação")
        st.write(
            f"Primeiro cruzamento do limiar {queue['policy']['score_threshold']:.2f}; até {queue['policy']['daily_customer_budget']} clientes/dia; sem substituir alertas por scores posteriores."
        )
        a, b, c = st.columns(3)
        a.metric("Clientes/dia alertados", queue["alerts"])
        b.metric("Precisão da fila", percentage(queue["precision"]))
        c.metric("Recall de clientes/dia com fraude", percentage(queue["recall"]))
        st.write(
            f"{queue['true_positive']} clientes/dia com fraude alertados · {queue['false_positive']} sem fraude alertados · {queue['false_negative']} com fraude não alertados."
        )
        st.caption(
            "Unidade: cliente/dia, com rótulos posteriores. Reutiliza a política real sobre scores de validação; não é o resultado observado dos 200 casos nem o ranking diário top-100."
        )
        with st.expander("Matriz por transação no limiar isolado"):
            row = active["threshold_transactions"]
            st.dataframe(
                pd.DataFrame(
                    [
                        [row["true_negative"], row["false_positive"]],
                        [row["false_negative"], row["true_positive"]],
                    ],
                    index=["Real: legítima", "Real: fraude"],
                    columns=["Score abaixo do limiar", "Score no/acima do limiar"],
                ),
                width="stretch",
            )
            st.write(
                f"Precisão: {percentage(row['precision'])} · Recall: {percentage(row['recall'])}"
            )
            st.caption(
                "Este diagnóstico por transação não aplica orçamento nem deduplicação por cliente. Não representa a fila operacional."
            )
    with provenance:
        st.write("Run avaliada", report["registry"]["run_id"])
        st.write("Pesos avaliados")
        st.code(report["model_sha256"], language=None)
        st.write("Release do snapshot", report["serving_release_id"])
        st.write("Identidade do dataset", dataset["identity_sha256"])
        st.write("Commit de treinamento", report["source_commit"])
        st.write(
            "Período de treinamento",
            report["training_dataset"]["first_transaction"]
            + " → "
            + report["training_dataset"]["last_transaction"],
        )
        st.caption(
            "Pesos e métricas conferidos via MLflow; conteúdo da validação conciliado com os Inputs nativos. Somente validação pontuada; sem treino, ajuste do gate ou mudança de alias."
        )
    st.divider()
    st.write(
        "O modelo apoia a investigação: combina sinais em escala e ajuda a priorizar a capacidade disponível. Ganhos de custo, tempo ou fraude evitada exigem medição operacional; não são deduzidos dessas métricas."
    )
