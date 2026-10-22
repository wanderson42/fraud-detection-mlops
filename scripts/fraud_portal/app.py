"""Analyst product surface: overview, prioritized queue, and identity-bound investigation."""

import json
import math
import os
from pathlib import Path

import pandas as pd
from scripts.fraud_portal import charts, data, theme
from scripts.fraud_portal.performance import view as performance_view
import streamlit as st

st.set_page_config(page_title="Fraud Ops · Investigação", page_icon="🔎", layout="wide")
st.markdown(theme.CSS, unsafe_allow_html=True)


def load():
    directory = os.environ.get("FRAUD_PORTAL_BUNDLE", "")
    expected = os.environ.get("FRAUD_PORTAL_MANIFEST_SHA256", "")
    data.require(
        bool(directory) and bool(expected),
        "Abra pelo comando make portal-open, após preparar o snapshot verificado.",
    )
    return data.load_bundle(Path(directory), expected)


def open_case(transaction):
    st.session_state["investigated_id"] = transaction
    st.session_state["detail-selection"] = transaction
    st.session_state["navigation"] = "Investigar caso"


def plot(figure, key):
    st.plotly_chart(
        figure,
        theme=None,
        width="stretch",
        key=key,
        config={"displaylogo": False, "scrollZoom": False},
    )


def overview(value):
    cases = value["cases"]
    totals = data.summary(cases)
    st.title("Visão da operação")
    st.write("Entenda a janela disponível e siga para os casos que merecem investigação.")
    columns = st.columns(4)
    for column, label, number, help_text in zip(
        columns,
        ["Decisões registradas", "Alertas gerados", "Feedback disponível", "Casos com explicação"],
        [
            totals["decisions"],
            totals["alerts"],
            f"{totals['feedback']}/{totals['decisions']}",
            f"{totals['explanations']}/{totals['decisions']}",
        ],
        [
            "Total de decisões desta janela, incluindo não alertadas.",
            "Alertas efetivamente gerados pela política por cliente/dia.",
            "Feedback posterior já disponível na fonte preparada.",
            "Cobertura de explicações SHAP previamente verificadas.",
        ],
        strict=True,
    ):
        column.metric(label, number, help=help_text)
    left, right = st.columns(2)
    with left, st.container(border=True, key="outcomes-card"):
        plot(charts.outcomes(cases), "outcomes")
    with right, st.container(border=True, key="scores-card"):
        plot(charts.scores(cases, value["alert_policy"]["score_threshold"]), "scores")
    st.subheader("Próximos casos para investigar")
    highlighted = data.select(cases, statuses=("alert",))[:3]
    if not highlighted:
        st.info(
            "Não há alertas gerados nesta janela. A fila permite consultar as demais decisões."
        )
    for column, case in zip(st.columns(max(1, len(highlighted))), highlighted):
        with (
            column,
            st.container(border=True, key=f"priority-case-{case['request']['transaction_id']}"),
        ):
            tid = case["request"]["transaction_id"]
            st.markdown(
                f'<p class="portal-case-label">Alerta para revisão</p>'
                f'<p class="portal-case-title">Transação {tid}</p>',
                unsafe_allow_html=True,
            )
            st.write(
                f"Cliente {case['request']['customer_id']} · score {case['response']['score']:.4f}"
            )
            st.markdown(
                '<span class="portal-status">'
                + ("SHAP disponível" if case["SHAP_explanation"] else "Contexto disponível")
                + "</span>",
                unsafe_allow_html=True,
            )
            st.button(
                "Investigar",
                key=f"highlight-{tid}",
                on_click=open_case,
                args=(tid,),
                type="primary",
                width="stretch",
            )
    st.caption(
        "Os indicadores descrevem este conjunto de decisões. Não são métricas de qualidade global do modelo."
    )


def queue(value):
    cases = value["cases"]
    st.title("Fila de investigação")
    st.write("Filtre as decisões sem alterar a triagem registrada pelo sistema.")
    statuses = sorted({c["triage"]["status"] for c in cases})
    customers = sorted({c["request"]["customer_id"] for c in cases})
    a, b, c = st.columns([2, 1, 1])
    with a:
        status = st.multiselect(
            "Encaminhamento",
            statuses,
            default=["alert"] if "alert" in statuses else statuses,
            format_func=data.status_name,
        )
    with b:
        customer = st.selectbox(
            "Cliente",
            [None, *customers],
            format_func=lambda x: "Todos os clientes" if x is None else str(x),
        )
    with c:
        transaction = st.text_input("ID da transação", placeholder="Ex.: 1610254")
    dates = sorted({c["request"]["tx_datetime"][:10] for c in cases})
    a, b = st.columns([1, 2])
    with a:
        day = st.selectbox("Dia da transação", ["Todos os dias", *dates])
    with b:
        minimum = st.slider(
            "Score mínimo para exibição",
            0.0,
            1.0,
            0.0,
            0.01,
            help="Filtro visual; não modifica o limiar ou as decisões da política.",
        )
    try:
        filtered = data.select(
            cases,
            statuses=status,
            customer=customer,
            transaction=transaction,
            minimum_score=minimum,
        )
    except data.PortalError as exc:
        st.info(str(exc))
        return
    if not status:
        filtered = []
    if day != "Todos os dias":
        filtered = [c for c in filtered if c["request"]["tx_datetime"].startswith(day)]
    st.caption(f"{len(filtered)} decisões no filtro · ordenadas por alerta, score e ID")
    if not filtered:
        st.info(
            "Nenhuma decisão corresponde aos filtros. Ajuste encaminhamento, cliente, ID ou score."
        )
        return
    page = st.number_input(
        "Página", min_value=1, max_value=max(1, math.ceil(len(filtered) / 25)), step=1
    )
    visible = filtered[(page - 1) * 25 : page * 25]
    rows = [
        {
            "Transação": c["request"]["transaction_id"],
            "Cliente": c["request"]["customer_id"],
            "Instante": c["request"]["tx_datetime"],
            "Valor": c["request"]["features"]["TX_AMOUNT"],
            "Score": c["response"]["score"],
            "Encaminhamento": data.status_name(c["triage"]["status"]),
            "Explicação": "Disponível" if c["SHAP_explanation"] else "Não preparada",
        }
        for c in visible
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Score": st.column_config.NumberColumn(format="%.4f"),
            "Valor": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    selected = st.selectbox(
        "Escolha uma transação para investigar",
        [c["request"]["transaction_id"] for c in visible],
        key="queue-selection",
    )
    st.button("Abrir investigação", on_click=open_case, args=(selected,), type="primary")
    st.caption("Valor expresso na unidade do dataset. A moeda não foi informada na fonte.")


def investigate(value):
    cases = value["cases"]
    st.title("Investigar caso")
    ids = [c["request"]["transaction_id"] for c in data.select(cases)]
    current = st.session_state.get("investigated_id", ids[0])
    selected = st.selectbox(
        "Transação", ids, index=ids.index(current) if current in ids else 0, key="detail-selection"
    )
    case = next(c for c in cases if c["request"]["transaction_id"] == selected)
    request, response, triage = case["request"], case["response"], case["triage"]
    st.caption(
        f"Cliente {request['customer_id']} · Terminal {request['terminal_id']} · {request['tx_datetime']}"
    )
    a, b, c = st.columns(3)
    a.metric(
        "Score de priorização",
        f"{response['score']:.6f}",
        help="Ranking não calibrado: este número não é probabilidade de fraude.",
    )
    b.metric("Encaminhamento", data.status_name(triage["status"]))
    c.metric(
        "Valor da transação",
        f"{request['features']['TX_AMOUNT']:.2f}",
        help="Unidade do dataset; moeda não informada.",
    )
    st.write(
        f"Limiar registrado: **{value['alert_policy']['score_threshold']:.2f}** · "
        f"Orçamento diário: **{value['alert_policy']['daily_customer_budget']} clientes** · "
        f"Utilização registrada nesta decisão: **{triage.get('daily_budget_used', '—')}**"
    )
    feedback = case["transaction_feedback"]
    st.info(
        "Feedback posterior: "
        + (
            "ainda não disponível"
            if feedback is None
            else "fraude indicada na transação"
            if feedback
            else "transação sem fraude no feedback"
        )
    )
    st.caption("O feedback é posterior à decisão e não participa da explicação SHAP.")
    explanation = case["SHAP_explanation"]
    if explanation and explanation.get("output", "raw_log_odds") != "raw_log_odds":
        st.info(
            "Explicação recebida em unidade ainda sem visualização compatível. O contexto do caso continua disponível."
        )
        explanation = None
    if explanation:
        plot(charts.waterfall(explanation), f"shap-{selected}")
        st.caption(
            "Laranja aumenta a margem; azul diminui. Contribuições em log-odds, não em pontos percentuais. O gráfico agrega as features restantes sem perder a soma."
        )
        st.write(
            f"**Reconstrução:** base {explanation['base_log_odds']:.6f} + contribuições = "
            f"{explanation['raw_log_odds']:.6f} log-odds → score {explanation['reconstructed_score']:.6f}."
        )
    else:
        st.info(
            "A explicação SHAP ainda não foi preparada para esta transação. O contexto e a decisão estão disponíveis; nenhuma contribuição foi estimada pela interface."
        )
    with st.expander("Contexto histórico e decisões relacionadas", expanded=False):
        features = request["features"]
        metrics = st.columns(3)
        metrics[0].metric("Transações do cliente · 7 dias", features["CUSTOMER_TX_COUNT_7D"])
        metrics[1].metric(
            "Valor médio do cliente · 7 dias", f"{features['CUSTOMER_AVG_AMOUNT_7D']:.2f}"
        )
        ratio = (
            f"{features['CUSTOMER_AMOUNT_RATIO_7D']:.2f}×"
            if features["CUSTOMER_AMOUNT_RATIO_VALID_7D"]
            else "Sem histórico válido"
        )
        metrics[2].metric("Valor / média histórica", ratio)
        st.caption(
            "Sinais registrados na requisição original, disponíveis no instante da decisão."
        )
        for field, title in [("customer_id", "Mesmo cliente"), ("terminal_id", "Mesmo terminal")]:
            related = [
                c
                for c in cases
                if c["request"][field] == request[field]
                and c["request"]["transaction_id"] != selected
            ]
            st.markdown(f"**{title} · {len(related)} outras decisões na janela**")
            if related:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Transação": c["request"]["transaction_id"],
                                "Instante": c["request"]["tx_datetime"],
                                "Score": c["response"]["score"],
                                "Encaminhamento": data.status_name(c["triage"]["status"]),
                            }
                            for c in related
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption("Não há outras decisões relacionadas nesta janela.")
    with st.expander("Features efetivamente enviadas ao modelo", expanded=False):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Feature": data.feature_label(name),
                        "Identificador": name,
                        "Valor enviado": float(v),
                        **(
                            {"SHAP (log-odds)": explanation["contributions"][name]}
                            if explanation
                            else {}
                        ),
                    }
                    for name, v in request["features"].items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with st.expander("Identidade e rastreabilidade"):
        st.write("Modelo", response["model_id"])
        st.code(response["model_sha256"], language=None)
        st.write("Release", response["serving_release_id"])
        st.write("Contrato de features", response["feature_contract_version"])
        st.write("Features válidas no instante", request["feature_as_of"])
        if case["feedback_available_ns"] is not None:
            st.write(
                "Feedback disponível a partir de", str(pd.Timestamp(case["feedback_available_ns"]))
            )
    st.download_button(
        "Exportar este caso (JSON)",
        json.dumps(case, ensure_ascii=False, indent=2),
        file_name=f"caso-{selected}.json",
        mime="application/json",
    )


def main():
    try:
        value, manifest = load()
    except (data.PortalError, OSError, KeyError, ValueError) as exc:
        st.title("Portal de investigação")
        st.error("Não foi possível abrir um conjunto de decisões verificado.")
        st.caption(str(exc))
        st.stop()
    with st.sidebar:
        st.markdown("### FRAUD OPS")
        st.caption("Contexto para decidir. Evidência para investigar.")
        navigation = st.radio(
            "Navegação",
            ["Visão geral", "Fila de investigação", "Investigar caso", "Desempenho do modelo"],
            key="navigation",
        )
        st.divider()
        st.markdown("**Modelo neste snapshot**")
        st.caption(value.get("model_id", value["cases"][0]["response"]["model_id"]))
        st.caption(
            f"Registry v{value.get('registry_version_at_capture', '—')} · identidade na captura"
        )
        st.markdown("**Conjunto verificado**")
        st.caption(
            f"{len(value['cases'])} decisões · {data.summary(value['cases'])['explanations']} explicações"
        )
        st.caption("Modo consulta · decisões e explicações verificadas.")
        st.caption("Operação → Grafana\n\nModelos → MLflow")
    st.markdown(
        '<p class="portal-kicker">INTELIGÊNCIA PARA INVESTIGAÇÃO DE FRAUDE</p>',
        unsafe_allow_html=True,
    )
    dates = sorted(c["request"]["tx_datetime"] for c in value["cases"])
    st.caption(
        f"Janela de decisões: {dates[0]} → {dates[-1]} · Dados preparados em {manifest['created_at'][:19]} UTC"
    )
    if navigation == "Visão geral":
        overview(value)
    elif navigation == "Fila de investigação":
        queue(value)
    elif navigation == "Investigar caso":
        investigate(value)
    else:
        performance_view.show(value, os.environ["FRAUD_PORTAL_MANIFEST_SHA256"])
    st.divider()
    st.caption(
        "Apoio à investigação: interprete o score e suas contribuições com o contexto da transação e o feedback disponível."
    )


main()
