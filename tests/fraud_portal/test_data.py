"""Snapshot integrity, honest summaries and additive presentation arithmetic."""

import copy
import json

import pytest
from scripts.fraud_portal import data


def test_full_queue_summary_and_filters_do_not_confuse_SHAP_coverage_with_all_decisions(bundle):
    directory, pin, _ = bundle
    value, _ = data.load_bundle(directory, pin)
    assert data.summary(value["cases"]) == {
        "decisions": 3,
        "customers": 3,
        "alerts": 2,
        "feedback": 2,
        "explanations": 1,
    }
    assert len(data.select(value["cases"], statuses=("alert",))) == 2
    assert len(data.select(value["cases"], minimum_score=0.8)) == 1
    assert data.select(value["cases"], customer=12)[0]["request"]["transaction_id"] == 102
    assert not data.select(value["cases"], transaction="999")
    with pytest.raises(ValueError):
        data.select(value["cases"], transaction="101 OR 1=1")


@pytest.mark.parametrize("change", ["pin", "bytes", "link", "identity", "duplicate"])
def test_bad_snapshots_are_rejected(bundle, change):
    directory, pin, value = bundle
    if change == "pin":
        pin = "f" * 64
    elif change == "bytes":
        (directory / "snapshot.json").write_text("bad")
    elif change == "link":
        file = directory / "snapshot.json"
        file.rename(directory / "original.json")
        file.symlink_to(directory / "original.json")
    else:
        if change == "identity":
            value["cases"][0]["response"]["model_sha256"] = "f" * 64
        else:
            value["cases"][1] = copy.deepcopy(value["cases"][0])
        (directory / "snapshot.json").write_bytes(data.encoded(value))
        manifest = json.loads((directory / "manifest.json").read_bytes())
        manifest["snapshot_sha256"] = data.sha(directory / "snapshot.json")
        (directory / "manifest.json").write_bytes(data.encoded(manifest))
        pin = data.sha(directory / "manifest.json")
    with pytest.raises((ValueError, OSError)):
        data.load_bundle(directory, pin)


def test_waterfall_preserves_all_contributions_and_uses_directional_colors(bundle):
    pytest.importorskip("plotly")
    from scripts.fraud_portal import charts

    _, _, value = bundle
    explanation = value["cases"][0]["SHAP_explanation"]
    trace = charts.waterfall(explanation).data[0]
    assert sum(trace.x[:-1]) == pytest.approx(explanation["raw_log_odds"])
    assert trace.increasing.marker.color == charts.ORANGE
    assert trace.decreasing.marker.color == charts.BLUE


def test_score_distribution_is_preaggregated_for_lightweight_client_payload(bundle):
    pytest.importorskip("plotly")
    from scripts.fraud_portal import charts

    trace = charts.scores(bundle[2]["cases"], 0.5).data[0]
    assert len(trace.x) == 20 and sum(trace.y) == 3


def test_chart_text_axes_and_hover_use_the_same_dark_palette(bundle):
    pytest.importorskip("plotly")
    from scripts.fraud_portal import charts, theme

    figures = [
        charts.outcomes(bundle[2]["cases"]),
        charts.scores(bundle[2]["cases"], 0.5),
        charts.waterfall(bundle[2]["cases"][0]["SHAP_explanation"]),
    ]
    for figure in figures:
        assert figure.layout.paper_bgcolor == theme.PALETTE["secondaryBackgroundColor"]
        assert figure.layout.font.color == theme.PALETTE["textColor"]
        assert figure.layout.xaxis.tickfont.color == theme.PALETTE["textColor"]
        assert figure.layout.yaxis.tickfont.color == theme.PALETTE["textColor"]


def test_dark_theme_text_and_directional_accents_have_readable_contrast():
    from scripts.fraud_portal import theme

    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, [0.2126, 0.7152, 0.0722], strict=True))

    def ratio(first, second):
        light, dark = sorted([luminance(first), luminance(second)], reverse=True)
        return (light + 0.05) / (dark + 0.05)

    p = theme.PALETTE
    for background in [p["backgroundColor"], p["secondaryBackgroundColor"]]:
        assert ratio(p["textColor"], background) >= 7
        assert ratio(p["primaryColor"], background) >= 4.5
    assert ratio(p["sidebar.textColor"], p["sidebar.backgroundColor"]) >= 7
    assert ratio("#F3A64B", p["secondaryBackgroundColor"]) >= 4.5
