"""Shared trainer process protocol and read-only node image lookup boundaries."""

import json
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from scripts.model_updates import training as update_training
from scripts.operational_round import controller as round_controller
from scripts.operational_round import training as round_training
from scripts.platform import container_images, kind_observability, kind_replay

from fraud_detection_mlops.features.feature_schema import FEATURE_DTYPES


@pytest.mark.parametrize("module", [round_training, update_training])
@pytest.mark.parametrize(
    "outcome,exit_code", [("qualified", 0), ("rejected", 2), ("exception", 1)]
)
def test_trainer_process_protocol_preserves_marker_stderr_and_exit(
    module, outcome, exit_code, tmp_path, monkeypatch, capsys
):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "input.json").write_text('{"input": 1}')
    plan = tmp_path / "plan.json"
    plan.write_text('{"plan": 2}')
    monkeypatch.setattr(sys, "argv", ["training", "--plan", str(plan), "--work", str(folder)])

    def execute(actual_plan, inputs, work):
        assert actual_plan == {"plan": 2} and inputs == {"input": 1} and work == folder
        print("diagnostic belongs to stderr")
        if outcome == "exception":
            raise ValueError("x" * 3000)
        return {"status": outcome}

    monkeypatch.setattr(module, "execute", execute)
    if exit_code:
        with pytest.raises(SystemExit) as stopped:
            module.main()
        assert stopped.value.code == exit_code
    else:
        module.main()
    captured = capsys.readouterr()
    assert "diagnostic belongs to stderr" in captured.err
    lines = captured.out.splitlines()
    assert len(lines) == 1 and lines[0].startswith(module.MARKER)
    value = json.loads(lines[0][len(module.MARKER) :])
    assert value["status"] == ("failed" if outcome == "exception" else outcome)
    if outcome == "exception":
        assert value["error_type"] == "ValueError" and len(value["reason"]) == 2000


def test_both_trainers_share_request_with_contract_types_and_original_event_clock():
    features = {name: 0.0 for name in FEATURE_DTYPES}
    features.update(TX_AMOUNT=12.5, TX_HOUR=9, TX_WEEKDAY=2)
    metadata = pd.DataFrame(
        [
            {
                "TX_DATETIME": pd.Timestamp("2018-04-03T09:00:00"),
                "TRANSACTION_ID": 7,
                "CUSTOMER_ID": 8,
                "TERMINAL_ID": 9,
            }
        ]
    )
    assert update_training.request_for is round_training.request_for
    request = update_training.request_for(pd.DataFrame([features]), metadata)
    assert (
        request["transaction_id"] == 7
        and request["customer_id"] == 8
        and request["terminal_id"] == 9
    )
    assert request["tx_datetime"] == request["feature_as_of"] == "2018-04-03T09:00:00"
    assert request["features"]["TX_AMOUNT"] == 12.5
    assert all(
        type(request["features"][n]) is (int if dtype.startswith("int") else float)
        for n, dtype in FEATURE_DTYPES.items()
    )


def test_all_lookup_aliases_use_the_same_readonly_owned_node_request(monkeypatch):
    expected = {"id": "sha256:abc", "repoTags": ["locked:v1"]}
    calls = []
    monkeypatch.setattr(
        container_images.f,
        "run",
        lambda args: (
            calls.append(args) or SimpleNamespace(stdout=json.dumps({"status": expected}))
        ),
    )
    assert (
        round_controller.cri
        is kind_replay.loaded
        is kind_observability.cri
        is container_images.inspect_image
    )
    assert round_controller.cri("locked:v1") == expected
    assert calls == [
        [
            "docker",
            "exec",
            "fraud-platform-control-plane",
            "crictl",
            "--runtime-endpoint",
            "unix:///run/containerd/containerd.sock",
            "inspecti",
            "locked:v1",
        ]
    ]
