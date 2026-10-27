"""Use real Helm schema/rendering, including the author's rejected runtime tag."""

import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.platform import runtime_maintenance as m


@pytest.fixture
def plan(tmp_path, monkeypatch):
    from fraud_detection_mlops.storage import managed_paths

    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path)
    folder = tmp_path / "storage/chart-proof"
    folder.mkdir(parents=True)
    runtime = m.runtime_tag()
    images = {
        "runtime": {"image": runtime},
        "artifacts": {"image": "fraud-kind-serving-artifacts:" + "0" * 32},
        "gateway": {"image": "fraud-kind-serving-gateway:" + "0" * 32},
    }
    value = {
        "owner_id": "a" * 32,
        "images": images,
        "slot_images": {
            s: {k: copy.deepcopy(images[k]) for k in ("runtime", "artifacts")}
            for s in ("blue", "green")
        },
        "releases": {s: {"manifest_sha256": "b" * 64} for s in ("blue", "green")},
        "receipt_directory": str(folder),
    }
    return folder, value


@pytest.fixture
def real_helm(monkeypatch):
    executable = shutil.which("helm")
    if executable is None:
        pytest.skip("Helm CLI required for actual schema validation")
    calls = []

    def run(args, **kwargs):
        assert args[:3] == ["helm", "template", "fraud-detector"]
        calls.append(args)
        return subprocess.run(
            [executable, *map(str, args[1:])],
            text=True,
            capture_output=True,
            timeout=kwargs["timeout"],
            check=False,
        )

    monkeypatch.setattr(m.f, "run", run)
    return calls


def test_generated_runtime_tag_is_accepted_by_real_chart_for_both_slots(plan, real_helm):
    folder, value = plan
    for slot in ("blue", "green"):
        proof = m.validate_chart(folder, value, slot, "prepare")
        assert proof["status"] == "runtime_chart_validated"
        assert proof["values_sha256"]
    assert len(real_helm) == 2


def test_original_maintenance_prefix_is_rejected_before_any_upgrade(plan, real_helm):
    folder, value = plan
    invalid = "fraud-runtime-maintenance:a8d00785027e477181d56842786bb1c8"
    value["images"]["runtime"]["image"] = invalid
    for slot in ("blue", "green"):
        value["slot_images"][slot]["runtime"]["image"] = invalid
    with pytest.raises(ValueError, match="Runtime chart preflight failed") as error:
        m.validate_chart(folder, value, "blue", "prepare")
    assert "fraud-kind-serving-runtime" in str(error.value)
    assert len(real_helm) == 1 and real_helm[0][1] == "template"


def test_preflight_values_equal_actual_deployment_apply_values(plan, real_helm, monkeypatch):
    folder, value = plan
    captured = []
    monkeypatch.setattr(m.deployment.old, "owned", lambda _: True)

    def helm(*args, **_):
        assert args[:3] == ("upgrade", "--install", "fraud-detector")
        captured.append(json.loads(Path(args[args.index("--values") + 1]).read_bytes()))

    monkeypatch.setattr(m.f, "helm", helm)
    monkeypatch.setattr(m.f, "kube", lambda *args, **kwargs: None)
    for slot in ("blue", "green"):
        m.validate_chart(folder, value, slot, "apply")
        m.deployment.apply(value, slot, both=False)
        assert captured[-1] == m.chart_values(value, slot)
    assert len(real_helm) == len(captured) == 2


def test_preflight_refuses_to_overwrite_changed_saved_values(plan, real_helm):
    folder, value = plan
    m.validate_chart(folder, value, "blue", "prepare")
    saved = next(folder.glob("runtime-helm-prepare-*.json"))
    saved.write_text('{"foreign":"keep"}\n')
    with pytest.raises(ValueError, match="Saved chart validation"):
        m.validate_chart(folder, value, "blue", "prepare")
    assert saved.read_text() == '{"foreign":"keep"}\n'
    assert len(real_helm) == 1
