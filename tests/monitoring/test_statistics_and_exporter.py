"""No immature outcomes; aggregate-only metrics and explicit collection failure."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from scripts.monitoring.local_stack import compose_args, initialize
from scripts.monitoring.monitoring_exporter import Monitor, server
from scripts.monitoring.statistics import (
    DAY_NS,
    average_precision,
    jensen_shannon,
    mature_quality,
    score_counts,
)
from scripts.replay.paced_triage_replay import execute
from sklearn.metrics import average_precision_score

from fraud_detection_mlops.artifacts import sha256


def test_whole_cohort_needs_day_close_and_all_observed_labels():
    rows = [(0, 7, 0.8, 1, 1), (DAY_NS - 1, 8, 0.7, 0, 0)]
    assert mature_quality(rows, 8 * DAY_NS - 1, False)["mature_days"] == 0
    q = mature_quality(rows, 8 * DAY_NS, False)
    assert q["mature_days"] == 1 and q["precision"] == 1 and q["recall"] == 1
    assert mature_quality([rows[0], (*rows[1][:-1], None)], 9 * DAY_NS, False)["mature_days"] == 0
    assert mature_quality(rows, 7 * DAY_NS, True)["mature_days"] == 1
    assert mature_quality([(*rows[1][:-1], None)], 9 * DAY_NS, True)["mature_days"] == 0


def test_customer_date_capture_includes_fraud_later_than_alert():
    rows = [(0, 7, 0.9, 1, 0), (1, 7, 0.8, 0, 1), (DAY_NS, 7, 0.9, 1, 0)]
    q = mature_quality(rows, 20 * DAY_NS, False)
    assert q["mature_fraud_customer_days"] == 1 and q["captured_customer_days"] == 1
    assert q["mature_alerts"] == 2 and q["precision"] == 0.5 and q["recall"] == 1
    assert mature_quality([], 0, False)["precision"] is None
    assert mature_quality([(0, 1, 0.9, 0, 0)], 20 * DAY_NS, True)["average_precision"] is None


@pytest.mark.parametrize(
    "rows", [[(0.5, 1), (0.5, 0), (0.9, 1), (0.1, 0)], [(0.1, 0), (0.1, 1)], [(1.0, 1), (0.0, 0)]]
)
def test_ap_matches_independent_sklearn_with_ties(rows):
    assert average_precision(rows) == pytest.approx(
        average_precision_score([r[1] for r in rows], [r[0] for r in rows])
    )


def test_distribution_zero_support_and_disjoint_support():
    assert jensen_shannon([1, 0], [0, 1]) == 1
    assert jensen_shannon([1, 1], [1, 1]) == 0
    assert jensen_shannon([0, 0], [1, 1]) is None
    assert score_counts([0.0, 0.1, 1.0]) == [1, 1, 0, 0, 0, 0, 0, 0, 0, 1]
    with pytest.raises(ValueError):
        score_counts([float("nan")])


def test_exporter_reads_coherent_state_without_mutation_and_serves_failure(setup_job):
    s = setup_job
    execute(s["factory"], s["output"], s["metadata"], s["runtime"], s["policy"])
    before = {p.name: sha256(p) for p in s["output"].glob("*.sqlite")}
    monitor = Monitor(s["output"], 0.1)
    monitor.refresh()
    http = server(monitor, port=0)
    worker = threading.Thread(target=http.serve_forever)
    worker.start()
    address = f"http://127.0.0.1:{http.server_port}"
    try:
        with urlopen(address + "/readyz") as response:
            assert response.status == 200
        with urlopen(address + "/metrics") as response:
            metrics = response.read().decode()
            assert "fraud_quality_mature_dates 5" in metrics
            assert "customer_id" not in metrics and "transaction_id" not in metrics
        with urlopen(address + "/summary") as response:
            summary = json.load(response)
            assert "durations" not in summary and summary["counts"]["events"] == 20
        assert {p.name: sha256(p) for p in s["output"].glob("*.sqlite")} == before
        identity = s["output"] / "metadata.json"
        value = json.loads(identity.read_text())
        value["input_sha256"] = "0" * 64
        identity.write_text(json.dumps(value))
        monitor.refresh()
        with pytest.raises(HTTPError) as error:
            urlopen(address + "/readyz")
        assert error.value.code == 503
        with urlopen(address + "/metrics") as response:
            metrics = response.read().decode()
            assert (
                "fraud_monitor_collector_up 0" in metrics
                and "fraud_replay_events_total" not in metrics
            )
    finally:
        http.shutdown()
        worker.join()
        http.server_close()


def test_identity_mismatch_and_missing_source_are_not_a_zero_fraud_panel(setup_job):
    monitor = Monitor(setup_job["output"], 1)
    monitor.refresh()
    assert monitor.response("/readyz")[0] == 503
    assert monitor.response("/summary")[0] == 503
    assert monitor.errors == 1
    assert monitor.response("/healthz")[0] == 200


def test_local_credentials_are_unique_private_and_not_overwritten(tmp_path):
    env = tmp_path / ".env"
    result = initialize(tmp_path / "state", env)
    assert result["state_directory"] == str(tmp_path / "state")
    assert env.stat().st_mode & 0o777 == 0o600
    before = env.read_bytes()
    with pytest.raises(ValueError):
        initialize(tmp_path / "other", env)
    assert env.read_bytes() == before
    assert "--volumes" not in compose_args("down", True) and "-v" not in compose_args("down", True)
    assert "--profile" in compose_args("up", True)
