"""Plotly views with semantic colors, explicit scope and additive SHAP waterfall."""

from collections import Counter

import plotly.graph_objects as go
from scripts.fraud_portal.data import feature_label, status_name

TEXT = "#E7EEF8"
BLUE = "#60A5FA"
ORANGE = "#F3A64B"
GRAY = "#8493A3"


def styled(figure, title, height=340):
    figure.update_layout(
        title={"text": title, "font": {"size": 17, "color": TEXT}},
        template="plotly_dark",
        font={"family": "Arial, sans-serif", "color": TEXT, "size": 12},
        margin={"l": 12, "r": 24, "t": 64, "b": 40},
        height=height,
        paper_bgcolor="#142238",
        plot_bgcolor="#142238",
        hoverlabel={"bgcolor": "#20344F", "font_color": "#F1F5F9"},
        showlegend=False,
    )
    figure.update_xaxes(
        tickfont={"color": TEXT},
        title_font={"color": TEXT},
        gridcolor="#2A3D57",
        zerolinecolor="#3D536E",
        linecolor="#3D536E",
    )
    figure.update_yaxes(
        tickfont={"color": TEXT},
        title_font={"color": TEXT},
        gridcolor="#2A3D57",
        zerolinecolor="#3D536E",
        linecolor="#3D536E",
    )
    figure.update_annotations(font_color=TEXT)
    return figure


def outcomes(cases):
    counts = Counter(c["triage"]["status"] for c in cases)
    keys = sorted(counts, key=lambda s: (-counts[s], s))
    fig = go.Figure(
        go.Bar(
            x=[status_name(k) for k in keys],
            y=[counts[k] for k in keys],
            marker_color=[ORANGE if k == "alert" else BLUE for k in keys],
            text=[counts[k] for k in keys],
            textposition="outside",
            textfont={"color": TEXT},
            cliponaxis=False,
        )
    )
    fig.update_yaxes(title="Decisões", rangemode="tozero")
    return styled(fig, "Encaminhamento das decisões")


def scores(cases, threshold):
    counts = [0] * 20
    for case in cases:
        counts[min(int(case["response"]["score"] * 20), 19)] += 1
    fig = go.Figure(
        go.Bar(
            x=[(i + 0.5) / 20 for i in range(20)],
            y=counts,
            width=0.046,
            marker_color=BLUE,
            hovertemplate="Score próximo de %{x:.3f}<br>%{y} decisões<extra></extra>",
        )
    )
    fig.add_vline(
        x=threshold,
        line_dash="dash",
        line_color=ORANGE,
        annotation_text="Limiar da política",
        annotation_position="top left",
    )
    fig.update_xaxes(title="Score de priorização", range=[0, 1])
    fig.update_yaxes(title="Decisões", rangemode="tozero")
    return styled(fig, "Distribuição dos scores na janela")


def waterfall(explanation):
    pairs = sorted(explanation["contributions"].items(), key=lambda v: (-abs(v[1]), v[0]))
    main, rest = pairs[:8], pairs[8:]
    labels = [
        "Base do modelo",
        *[feature_label(n) for n, _ in main],
        "Demais features",
        "Margem final",
    ]
    values = [explanation["base_log_odds"], *[v for _, v in main], sum(v for _, v in rest), 0]
    fig = go.Figure(
        go.Waterfall(
            orientation="h",
            y=labels,
            x=values,
            measure=["absolute", *(["relative"] * 9), "total"],
            increasing={"marker": {"color": ORANGE}},
            decreasing={"marker": {"color": BLUE}},
            totals={"marker": {"color": TEXT}},
            connector={"line": {"color": "#58708E"}},
            text=[f"{v:+.3f}" for v in values[:-1]] + [f"{explanation['raw_log_odds']:.3f}"],
            textposition="outside",
            customdata=[*values[:-1], explanation["raw_log_odds"]],
            hovertemplate="%{y}<br>%{customdata:.6f} log-odds<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed", automargin=True)
    fig.update_xaxes(title="Contribuição para a margem (log-odds)", zerolinecolor=GRAY)
    return styled(fig, "Como o modelo chegou a este score", 560)
