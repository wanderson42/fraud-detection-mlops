"""Window support, quality findings, time semantics and bounded metric labels."""

import json
from pathlib import Path
import subprocess
import sys

import pytest
from scripts.monitoring import feature_health as health
from scripts.monitoring.feature_monitoring import demo, generated_rows


def mutate(rows, field, value):
    rows = list(rows)
    request = json.loads(rows[0][1])
    request["features"][field] = value
    rows[0] = rows[0][0], json.dumps(request)
    return rows


def test_generated_demo_has_closed_form_expected_outcomes():
    value = demo()
    scenarios = value["scenarios"]
    assert value["status"] == "success" and not value["fit_executed"]
    assert scenarios["unchanged"]["comparison_ready"]
    assert all(x["jensen_shannon"] == 0 for x in scenarios["unchanged"]["features"].values())
    assert scenarios["amount_shift"]["features"]["TX_AMOUNT"]["jensen_shannon"] == 1
    assert scenarios["missing_feature"]["status"] == "invalid_data"
    assert scenarios["feature_clock_lag"]["current"]["max_feature_lag_seconds"] == 60
    assert not any(
        x["training_triggered"] or x["production_promotion"] for x in scenarios.values()
    )


def test_no_support_or_overlapping_windows_are_not_zero_drift():
    rows = generated_rows()
    for ref, now, separate in [(rows[:2], rows, True), (rows, rows, False), ([], [], False)]:
        value = health.compare(ref, now, nonoverlapping=separate)
        assert value["status"] == "insufficient_support" and not value["comparison_ready"]
        assert all(x["jensen_shannon"] is None for x in value["features"].values())
        assert "fraud_feature_jensen_shannon{feature=" not in health.exposition(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("TX_AMOUNT", None),
        ("TX_AMOUNT", float("nan")),
        ("TX_AMOUNT", float("inf")),
        ("TX_AMOUNT", 10**1000),
        ("TX_AMOUNT", True),
        ("TX_AMOUNT", -1.0),
        ("CUSTOMER_TX_COUNT_7D", 1.5),
        ("CUSTOMER_TX_COUNT_7D", 2**63),
        ("TX_HOUR", 24),
        ("TX_WEEKDAY", 0),
        ("CUSTOMER_AMOUNT_RATIO_VALID_7D", 2),
        ("TERMINAL_KNOWN_FRAUD_RATE_7D", 1.1),
    ],
)
def test_bad_values_count_and_suppress_comparison(field, value):
    rows = generated_rows()
    result = health.compare(rows, mutate(rows, field, value), nonoverlapping=True)
    assert result["status"] == "invalid_data"
    assert result["current"]["invalid_values"][field] == 1
    assert result["features"]["TX_AMOUNT"]["jensen_shannon"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("payload", ["not json", "[]", "null", "{}", '"string"'])
def test_malformed_requests_are_quality_findings(payload):
    value = health.profile([(0, payload)])
    assert value["schema_or_clock_invalid_rows"] == value["invalid_clock_rows"] == 1
    assert all(n == 1 for n in value["missing_values"].values())


def test_missing_and_unexpected_names_do_not_inject_metric_labels():
    rows = generated_rows(days=7)
    request = json.loads(rows[0][1])
    del request["features"]["TX_AMOUNT"]
    request["features"]['foreign"label\\n'] = 3
    rows[0] = rows[0][0], json.dumps(request)
    result = health.compare(generated_rows(), rows, nonoverlapping=True)
    text = health.exposition(result)
    assert 'fraud_feature_missing_fraction{feature="TX_AMOUNT"} 0.001' in text
    assert "foreign" not in text and "customer_id" not in text and "transaction_id" not in text
    assert len(text.splitlines()) < 100
    assert text.count("# TYPE fraud_feature_missing_fraction gauge") == 1


def test_nanosecond_source_clocks_not_host_age():
    assert health.clock_ns("1970-01-01T00:00:00.000000001") == 1
    assert health.clock_ns("1970-01-01T00:00:00.123456789") == 123456789
    assert health.clock_ns("2018-09-01T00:00:00") == 1535760000 * 10**9
    for value in [
        None,
        "2020-01-01T00:00:00Z",
        "2020-01-01T00:00:00+03:00",
        "2020-02-31T00:00:00",
    ]:
        with pytest.raises(ValueError):
            health.clock_ns(value)


def test_future_clock_and_feature_coherence_are_explicit():
    rows = generated_rows()
    request = json.loads(rows[0][1])
    request["feature_as_of"] = "2020-01-01T00:00:01"
    rows[0] = rows[0][0], json.dumps(request)
    result = health.profile(rows)
    assert result["future_clock_rows"] == result["invalid_clock_rows"] == 1
    result = health.profile(mutate(generated_rows(), "TERMINAL_KNOWN_FRAUD_COUNT_7D", 1))
    assert result["incoherent_rows"] == 1


def test_feature_check_agrees_with_shared_gold_validator():
    from fraud_detection_mlops.features.feature_validation import validate_feature_record

    for field, value in [("CUSTOMER_AMOUNT_RATIO_1D", 1.0), ("TERMINAL_KNOWN_FRAUD_COUNT_1D", 1)]:
        rows = mutate(generated_rows(), field, value)
        with pytest.raises(ValueError):
            validate_feature_record(json.loads(rows[0][1])["features"])
        assert health.profile(rows)["incoherent_rows"] == 1
    validate_feature_record(json.loads(generated_rows()[0][1])["features"])


def test_exporter_feature_code_runs_without_site_packages():
    root = Path(health.__file__).resolve().parents[2]
    code = "import sys; sys.path.insert(0, sys.argv[1]); from scripts.monitoring.feature_health import contract; assert len(contract()[0]) == 19"
    subprocess.run(
        [sys.executable, "-S", "-c", code, str(root)], check=True, capture_output=True, text=True
    )


def test_versioned_protocol_matches_comparison_policy():
    protocol = json.loads((health.ROOT / "references/feature_monitoring_v1.json").read_text())
    assert protocol["window_rows"] == health.WINDOW
    assert protocol["minimum_decisions"] == 2 * health.WINDOW
    assert protocol["bins"] == health.BOUNDS
    assert protocol["initial_descriptive_threshold"] == health.THRESHOLD
    assert protocol["feature_contract_sha256"] == health.contract()[1]
    assert not protocol["training_triggered"] and not protocol["production_promotion"]
