"""Real Helm and GNU Make preserve the active slot and require explicit approval."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest
from scripts.model_updates import controller as u
from scripts.operational_round import controller as c
import yaml

ROOT = Path(__file__).resolve().parents[2]


def render(chart, values, tmp_path):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("Real Helm required")
    f = tmp_path / (Path(chart).name + ".json")
    f.write_text(json.dumps(values))
    subprocess.run(
        [helm, "lint", "--strict", str(ROOT / chart), "--values", str(f)],
        check=True,
        capture_output=True,
    )
    raw = subprocess.check_output(
        [
            helm,
            "template",
            "fraud-detector",
            str(ROOT / chart),
            "--namespace",
            "fraud-platform",
            "--values",
            str(f),
        ],
        text=True,
    )
    return {d["kind"] + "/" + d["metadata"]["name"]: d for d in yaml.safe_load_all(raw)}


def test_stage_chart_does_not_change_active_pod_template(tmp_path):
    values = yaml.safe_load((ROOT / "infra/helm/fraud-serving/values.yaml").read_text())
    values.update(greenEnabled=True, greenReplicas=0)
    before = render("infra/helm/fraud-serving", values, tmp_path)
    values.update(
        blueReplicas=1,
        slotImages={
            s: {k: values["images"][k] for k in ["runtime", "artifacts"]}
            for s in ["blue", "green"]
        },
    )
    values["slotImages"]["green"]["artifacts"] = "fraud-kind-serving-artifacts:" + "a" * 32
    values["greenReplicas"] = 1
    after = render("infra/helm/fraud-model-updates", values, tmp_path)
    assert before["Deployment/fraud-detector-blue"] == after["Deployment/fraud-detector-blue"]
    assert (
        before["Deployment/fraud-detector-gateway"] == after["Deployment/fraud-detector-gateway"]
    )
    assert before["Service/fraud-detector-active"] == after["Service/fraud-detector-active"]
    assert (
        before["Deployment/fraud-detector-green"]["spec"]["template"]
        != after["Deployment/fraud-detector-green"]["spec"]["template"]
    )
    values["activeSlot"] = "green"
    values["blueReplicas"] = 0
    released = render("infra/helm/fraud-model-updates", values, tmp_path)
    assert released["Deployment/fraud-detector-blue"]["spec"]["replicas"] == 0
    assert (
        released["Service/fraud-detector-active"]["spec"]["selector"][
            "app.kubernetes.io/component"
        ]
        == "green"
    )


def test_make_release_requires_exact_digest_and_writer_declaration(dispatch):
    for args in ({}, {"UPDATE_NO_OTHER_WRITERS": "1"}, {"UPDATE_APPROVAL_SHA256": "a" * 64}):
        result, calls = dispatch("update-release", **args)
        assert result.returncode != 0 and not calls
    result, calls = dispatch(
        "update-release", UPDATE_APPROVAL_SHA256="a" * 64, UPDATE_NO_OTHER_WRITERS="1"
    )
    assert result.returncode == 0, result.stderr
    assert calls[0][-3:] == ["--approval-sha256", "a" * 64, "--no-other-writers"]


def test_current_update_keeps_original_scientific_source_unchanged(monkeypatch):
    transition = json.loads((ROOT / "references/model_update_compatibility_v1.json").read_bytes())
    # This bridge authorizes only the exact first adoption, before later doc closures.
    # Keep real current scientific pins, but reconstruct that reviewed documentary state.
    adopted = c.pins()
    original = dict(adopted)
    for name, hashes in transition["allowed_changes"].items():
        original[name] = hashes["before"]
        adopted[name] = hashes["after"]
    assert not {"README.md", "references/persistence_catalogue_v1.json"} & u.pins().keys()
    monkeypatch.setattr(c, "pins", lambda: adopted)
    base = {"source_pins": original, "node_uid": "node", "tracking_deployment_uid": "tracking"}
    monkeypatch.setattr(u.f, "owner", lambda: {"node_uid": "node"})
    monkeypatch.setattr(u.kind_tracking, "inventory", lambda: {"deployment_uid": "tracking"})
    u.legacy_compatible(base)
    changed = copy.deepcopy(base)
    changed["source_pins"]["references/gold_contract_v1.json"] = "f" * 64
    with pytest.raises(ValueError, match="Scientific"):
        u.legacy_compatible(changed)


def test_update_training_dependencies_are_locked_and_include_optional_search():
    import re

    lock = {p["name"]: p for p in tomllib.loads((ROOT / "poetry.lock").read_text())["package"]}
    raw = (ROOT / "infra/kind/model-updates/requirements.txt").read_text()
    names = []
    for m in re.finditer(
        r"^([\w.-]+)==([^\s]+)(.*?)(?=^[\w.-]+==|\Z)", raw, re.MULTILINE | re.DOTALL
    ):
        name, version, hashes = m.groups()
        names.append(name)
        assert version == lock[name]["version"]
        assert set(re.findall(r"--hash=(sha256:[a-f0-9]+)", hashes)) == {
            v["hash"] for v in lock[name]["files"]
        }
    assert {"optuna", "colorlog", "tqdm"}.issubset(names)
