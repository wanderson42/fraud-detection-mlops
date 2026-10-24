"""Shared case investigation, with explicit page/snapshot context and explanation origin."""

import json

import pandas as pd
from scripts.fraud_portal import charts, data
import streamlit as st


def show(value, *, key_prefix="", context_label="janela"):
    cases = value["cases"]
    (st.subheader if context_label == "página" else st.title)("Investigar caso")
    ids = [c["request"]["transaction_id"] for c in data.select(cases)]
    current = st.session_state.get("investigated_id", ids[0])
    selected = st.selectbox(
        "Transação",
        ids,
        index=ids.index(current) if current in ids else 0,
        key=key_prefix + "detail-selection",
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
    if case.get("explanation_origin"):
        st.caption(
            "Explicação do conjunto verificado: requisição, resposta, features, pesos e release conferidos exatamente. Não foi recalculada na interface."
        )
    if context_label == "página":
        st.caption(
            "Relações abaixo abrangem somente os casos desta página filtrada; não representam todo o histórico do cliente."
        )
    explanation = case["SHAP_explanation"]
    if explanation and explanation.get("output", "raw_log_odds") != "raw_log_odds":
        st.info(
            "Explicação recebida em unidade ainda sem visualização compatível. O contexto do caso continua disponível."
        )
        explanation = None
    if explanation:
        st.plotly_chart(
            charts.waterfall(explanation),
            theme=None,
            width="stretch",
            key=key_prefix + f"shap-{selected}",
            config={"displaylogo": False, "scrollZoom": False},
        )
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
            st.markdown(f"**{title} · {len(related)} outras decisões nesta {context_label}**")
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
                st.caption(f"Não há outras decisões relacionadas nesta {context_label}.")
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
        key=key_prefix + "case-export",
    )
