"""Read-only live session page, independent of the algorithm serving scores."""

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3

import pandas as pd
import plotly.graph_objects as go
from scripts.fraud_portal import charts, data, theme
from scripts.product_session import reader
import streamlit as st

st.set_page_config(page_title="Fraud Ops · Sessão", page_icon="🔎", layout="wide")
st.markdown(theme.CSS, unsafe_allow_html=True)
st.title("Investigação · sessão operacional")
st.caption(
    "Transações chegam pelo produtor. A fila acompanha decisões já persistidas pela API; o analista não solicita novas transações."
)


@st.fragment(run_every="2s")
def live():
    try:
        only_alerts = st.checkbox("Exibir somente alertas", value=True)
        page = int(st.number_input("Página da fila", min_value=1, value=1, step=1))
        page_size = st.selectbox("Casos por página", [25, 50, 100], index=0)
        value = reader.read(
            Path(os.environ["FRAUD_SESSION_DIRECTORY"]),
            os.environ["FRAUD_SESSION_SHA256"],
            page=page,
            page_size=page_size,
            only_alerts=only_alerts,
        )
        st.caption(
            f"Sessão {value['session_id']} · modelo {value['model_id']} · pesos {value['model_sha256'][:12]} · release {value['serving_release_id'][:12]}"
        )
        producer = value["producer"]
        states = {
            "prepared": "Preparada",
            "warming_up": "Preparando contexto causal",
            "running": "Recebendo transações",
            "paused": "Pausada",
            "awaiting_feedback": "Chegada concluída · aguardando feedback posterior",
            "draining_feedback": "Avançando relógio para feedback posterior",
            "complete": "Concluída",
            "failed": "Interrompida por falha",
            "stale": "Produtor sem atualização recente",
        }
        st.info(states.get(producer["status"], producer["status"]))
        clock = value["last_event_order"][0]
        event_time = (
            datetime.fromtimestamp(clock / 1e9, tz=UTC).isoformat()
            if clock is not None
            else "ainda não iniciado"
        )
        st.caption(
            f"Relógio dos dados: {event_time} · atualização do produtor: {producer['age_seconds']:.1f}s atrás"
            if producer["age_seconds"] is not None
            else f"Relógio dos dados: {event_time}"
        )
        totals = value["totals"]
        for col, label, number in zip(
            st.columns(4),
            ["Decisões", "Alertas", "Clientes", "Feedback de decisões"],
            [
                f"{totals['decisions']}/{value['expected_decisions']}",
                totals["alerts"],
                totals["customers"],
                totals["feedback"],
            ],
            strict=True,
        ):
            col.metric(label, number)
        st.progress(totals["decisions"] / value["expected_decisions"])
        st.caption(
            "Replay com tempo comprimido de eventos históricos: o intervalo entre chamadas não representa o intervalo real entre transações. Feedback permanece indisponível até seu instante no relógio dos dados."
        )
        if not totals["decisions"]:
            st.info(
                "Aguardando a primeira decisão. O contexto temporal é reconstruído antes da pontuação."
            )
            return
        left, right = st.columns(2)
        with left:
            outcomes = value["outcomes"]
            keys = sorted(outcomes, key=lambda key: (-outcomes[key], key))
            fig = go.Figure(
                go.Bar(
                    x=[data.status_name(k) for k in keys],
                    y=[outcomes[k] for k in keys],
                    marker_color=[charts.ORANGE if k == "alert" else charts.BLUE for k in keys],
                )
            )
            st.plotly_chart(
                charts.styled(fig, "Encaminhamento de todas as decisões"),
                theme=None,
                width="stretch",
                key="session-outcomes",
            )
        with right:
            fig = go.Figure(
                go.Bar(
                    x=[(i + 0.5) / 20 for i in range(20)],
                    y=value["score_histogram"],
                    marker_color=charts.BLUE,
                )
            )
            fig.add_vline(
                x=value["alert_policy"]["score_threshold"],
                line_color=charts.ORANGE,
                line_dash="dash",
            )
            fig.update_xaxes(title="Score de priorização", range=[0, 1])
            st.plotly_chart(
                charts.styled(fig, "Distribuição global dos scores"),
                theme=None,
                width="stretch",
                key="session-scores",
            )
        selected = value["cases"]
        paging = value["pagination"]
        st.caption(
            f"Página {paging['page']}/{paging['pages']} · {paging['matching_decisions']} decisões no filtro · gráficos e contadores cobrem toda a sessão"
        )
        st.subheader("Fila priorizada")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Transação": c["request"]["transaction_id"],
                        "Cliente": c["request"]["customer_id"],
                        "Instante": c["request"]["tx_datetime"],
                        "Score": c["response"]["score"],
                        "Triagem": data.status_name(c["triage"]["status"]),
                        "Feedback posterior": "Indisponível"
                        if c["transaction_feedback"] is None
                        else str(c["transaction_feedback"]),
                    }
                    for c in selected
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        if selected:
            tid = st.selectbox(
                "Investigar transação", [c["request"]["transaction_id"] for c in selected]
            )
            case = next(c for c in selected if c["request"]["transaction_id"] == tid)
            st.json(
                {
                    "transação": case["request"],
                    "resposta_API": case["response"],
                    "triagem": case["triage"],
                    "feedback_disponível_ns": case["feedback_available_ns"],
                }
            )
            st.caption(
                "SHAP ainda não preparado para esta decisão. Explicações de outros snapshots não são reaproveitadas automaticamente."
            )
        st.caption(
            "Esta janela operacional não recalcula desempenho global. A validação completa e as explicações já verificadas continuam no portal de evidências."
        )
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        st.error(f"Sessão indisponível: {exc}")


live()
