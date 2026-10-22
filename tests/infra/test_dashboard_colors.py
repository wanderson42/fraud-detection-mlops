"""Assert semantic colors in shipped JSON, including strict threshold boundaries."""

from copy import deepcopy
import json
import math
from pathlib import Path

import pytest
from scripts.platform import dashboard_colors as colors

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "infra/helm/fraud-observability/files/dashboards"


def panel(name, identity):
    dashboard = json.loads((DASHBOARDS / name).read_text())
    return next(p for p in dashboard["panels"] if p["id"] == identity)


def display(p, value):
    defaults = p["fieldConfig"]["defaults"]
    for mapping in defaults["mappings"]:
        if mapping["type"] == "special":
            match = mapping["options"]["match"]
            if (value is None and match == "null") or (
                isinstance(value, float) and math.isnan(value) and match == "nan"
            ):
                return mapping["options"]["result"]
        if mapping["type"] == "value" and str(value) in mapping["options"]:
            return mapping["options"][str(value)]
    if defaults["color"]["mode"] == "fixed":
        return {"color": defaults["color"]["fixedColor"], "value": value}
    chosen = None
    for step in defaults["thresholds"]["steps"]:
        if step["value"] is None or value >= step["value"]:
            chosen = step["color"]
    return {"color": chosen, "value": value}


@pytest.mark.parametrize(
    "name,identity",
    [
        ("triage-quality.json", 4),
        ("triage-quality.json", 5),
        ("triage-quality.json", 6),
        ("triage-quality.json", 8),
        ("triage-quality.json", 14),
        ("replay-operations.json", 3),
        ("replay-operations.json", 4),
        ("replay-operations.json", 8),
        ("kind-overview.json", 2),
    ],
)
def test_descriptive_values_never_become_good_or_bad_by_magnitude(name, identity):
    p = panel(name, identity)
    for number in [0, 0.01, 0.8, 80, 1000, 556824]:
        assert display(p, number)["color"] == "blue"
    assert display(p, None)["color"] == "gray"
    assert "Sem dados" in display(p, None)["text"]


def test_collector_and_session_are_not_confused():
    collector = panel("replay-operations.json", 2)
    assert display(collector, 0) == {"text": "Inválido", "color": "red"}
    assert display(collector, 1) == {"text": "Válido", "color": "green"}
    session = panel("replay-operations.json", 13)
    assert display(session, 0) == {"text": "Parada", "color": "gray"}
    assert display(session, 1) == {"text": "Em execução", "color": "blue"}
    comparison = panel("feature-health.json", 3)
    assert display(comparison, 0)["text"] == "Sem comparação"
    assert display(comparison, 0)["color"] == "gray"


def test_feature_shift_is_investigation_strictly_above_point_one():
    p = panel("feature-health.json", 8)
    assert display(p, 0.1)["color"] == "blue"
    assert display(p, math.nextafter(0.1, math.inf))["color"] == "yellow"
    assert display(p, 1)["color"] == "yellow"
    assert display(p, None)["color"] == "gray"
    assert "não" in p["description"].lower()


@pytest.mark.parametrize("identity", [5, 7, 10])
def test_positive_contract_violations_are_red_not_arbitrary_eighty(identity):
    p = panel("feature-health.json", identity)
    assert display(p, 0)["color"] == "green"
    assert display(p, 1)["color"] == "red"
    assert display(p, 80)["color"] == "red"
    assert display(p, None)["color"] == "gray"


def test_missing_fraction_and_timing_gap_use_different_severity():
    missing = panel("feature-health.json", 6)
    assert display(missing, 0)["color"] == "green"
    assert display(missing, 1 / 1000)["color"] == "red"
    gap = panel("replay-operations.json", 7)
    assert display(gap, 0)["color"] == "green"
    assert display(gap, 1)["color"] == "yellow"
    lag = panel("feature-health.json", 9)
    assert display(lag, 1e-9)["color"] == "yellow"


def test_all_numeric_panels_have_explicit_color_missing_and_text_explanation():
    assert colors.validate()["status"] == "dashboard_color_policy_validated"
    for path in DASHBOARDS.glob("*.json"):
        for p in json.loads(path.read_text())["panels"]:
            if p["type"] == "text":
                continue
            assert "80" not in str(p["fieldConfig"]["defaults"]["thresholds"])
            assert p["description"]
            assert display(p, None)["color"] == "gray"
            assert display(p, math.nan)["color"] == "gray"


def test_native_color_verification_tolerates_added_defaults_but_rejects_wrong_colors():
    expected = json.loads((DASHBOARDS / "feature-health.json").read_text())
    observed = deepcopy(expected)
    observed["panels"][1]["fieldConfig"]["defaults"]["extraGrafanaDefault"] = True
    observed["panels"][1]["fieldConfig"]["defaults"]["mappings"][0]["options"]["0"]["index"] = 0
    colors.verify_provisioned(expected, observed)
    observed["panels"][1]["fieldConfig"]["defaults"]["mappings"][0]["options"]["1"]["color"] = (
        "red"
    )
    with pytest.raises(ValueError, match="color policy differs"):
        colors.verify_provisioned(expected, observed)


def test_forged_legacy_profile_cannot_authorize_reuse(tmp_path, monkeypatch):
    from scripts.platform import kind_observability as k

    profile = json.loads((k.f.ROOT / k.LEGACY_PROFILE).read_text())
    saved = profile["source_pins"]
    image_runtime = k.runtime_pins()
    forged = tmp_path / k.LEGACY_PROFILE
    forged.parent.mkdir(parents=True)
    forged.write_text(json.dumps({**profile, "accepted_host_controller_sha256": ["foreign"]}))
    monkeypatch.setattr(k.f, "ROOT", tmp_path)
    monkeypatch.setattr(k, "runtime_pins", lambda: image_runtime)
    with pytest.raises(ValueError, match="Legacy image profile differs"):
        k.verify_image_sources(saved)
