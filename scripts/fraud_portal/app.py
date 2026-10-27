"""Analyst product surface: overview, prioritized queue, and identity-bound investigation."""

import math
import os
from pathlib import Path

import pandas as pd
from scripts.fraud_portal import case_view, charts, data, session_view, theme
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
    case_view.show(value)


def main():
    snapshot_error = None
    try:
        value, manifest = load()
    except (data.PortalError, OSError, KeyError, ValueError) as exc:
        value, manifest, snapshot_error = None, None, str(exc)
    configured = bool(os.environ.get("FRAUD_PORTAL_SESSION_LINK"))
    pages = ["Visão geral", "Fila de investigação", "Investigar caso", "Desempenho do modelo"]
    if configured:
        pages.insert(0, "Sessão atual")
    with st.sidebar:
        st.markdown("### FRAUD OPS")
        st.caption("Contexto para decidir. Evidência para investigar.")
        navigation = st.radio("Navegação", pages, key="navigation")
        st.divider()
        if configured:
            st.markdown("**Sessão atual**")
            st.caption("Atualização automática · fonte identificada no painel")
        st.markdown("**Evidências verificadas**")
        if value:
            st.caption(value.get("model_id", value["cases"][0]["response"]["model_id"]))
            st.caption(
                f"Registry v{value.get('registry_version_at_capture', '—')} · identidade na captura"
            )
            st.caption(
                f"{len(value['cases'])} decisões · {data.summary(value['cases'])['explanations']} explicações"
            )
        else:
            st.caption("Conjunto de evidências indisponível")
        st.caption("Modo consulta · sem ingestão ou alteração de decisões.")
        st.caption("Operação → Grafana\n\nModelos → MLflow")
    st.markdown(
        theme.header(
            "Sessão vinculada" if navigation == "Sessão atual" else "Evidências verificadas"
        ),
        unsafe_allow_html=True,
    )
    if navigation == "Sessão atual":
        session_view.show(value)
    elif value is None:
        st.title("Portal de investigação")
        st.error("Não foi possível abrir um conjunto de decisões verificado.")
        st.caption(snapshot_error)
    else:
        dates = sorted(c["request"]["tx_datetime"] for c in value["cases"])
        st.caption(
            f"Fonte: evidências verificadas · {len(value['cases'])} decisões · janela {dates[0]} → {dates[-1]} · preparado em {manifest['created_at'][:19]} UTC"
        )
        if configured:
            st.caption(
                "Esta página preserva o conjunto verificado; não representa a sessão corrente."
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
