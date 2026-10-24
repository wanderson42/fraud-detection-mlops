"""Auto-refresh session within the single portal, using the accepted read-only reader."""

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3

import pandas as pd
import plotly.graph_objects as go
from scripts.fraud_portal import case_view, charts, data, session_link
import streamlit as st


@st.fragment(run_every="2s")
def show(snapshot):
    st.title("Sessão atual · investigação")
    st.caption(
        "Fonte: checkpoint da sessão vinculada. O produtor envia transações; o analista consulta decisões já persistidas."
    )
    try:
        only_alerts = st.checkbox("Exibir somente alertas", value=True)
        page = int(st.number_input("Página da fila", min_value=1, value=1, step=1))
        page_size = st.selectbox("Casos por página", [25, 50, 100], index=0)
        value = session_link.page(
            Path(os.environ["FRAUD_PORTAL_SESSION_LINK"]),
            os.environ["FRAUD_PORTAL_SESSION_LINK_SHA256"],
            page=page,
            page_size=page_size,
            only_alerts=only_alerts,
        )
        st.caption(
            f"Sessão {value['session_id']} · modelo {value['model_id']} · pesos {value['model_sha256'][:12]} · release {value['serving_release_id'][:12]}"
        )
        if snapshot and any(
            snapshot[name] != value[name] for name in ("model_sha256", "serving_release_id")
        ):
            st.warning(
                "A sessão usa outros pesos ou release. A página de desempenho preserva a avaliação do modelo identificado nas evidências; seus resultados não são atribuídos ao modelo desta sessão."
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
        status_message = states.get(producer["status"], producer["status"])
        if producer["status"] == "failed":
            st.error(status_message)
        elif producer["status"] == "stale":
            st.warning(status_message)
        else:
            st.info(status_message)
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
            cases = session_link.explainable(
                selected, snapshot, os.environ.get("FRAUD_PORTAL_MANIFEST_SHA256", "")
            )
            case_view.show(
                {"cases": cases, "alert_policy": value["alert_policy"]},
                key_prefix="session-" + value["session_id"] + "-",
                context_label="página",
            )
        st.caption(
            "A avaliação completa permanece em Desempenho do modelo, com dataset, período e modelo identificados. Os indicadores desta sessão não substituem essa avaliação."
        )
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        st.error(f"Sessão indisponível: {exc}")
