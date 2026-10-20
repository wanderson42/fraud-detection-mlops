"""Write aggregate feature health or exercise a generated, no-fit monitoring demo."""

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from scripts.monitoring import feature_health as health
from scripts.monitoring.collector import snapshot

from fraud_detection_mlops.artifacts import write_json
from fraud_detection_mlops.storage.managed_paths import managed_destination

ROOT = Path(__file__).resolve().parents[2]


def generated_rows(amount=10.0, *, days=0):
    types, _ = health.contract()
    rows = []
    for index in range(health.WINDOW):
        stamp = datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None) + timedelta(
            days=days, seconds=index
        )
        features = {name: 0 if dtype.startswith("int") else 0.0 for name, dtype in types.items()}
        features.update(TX_AMOUNT=amount, TX_HOUR=stamp.hour, TX_WEEKDAY=stamp.isoweekday())
        request = {
            "schema_version": 1,
            "feature_contract_version": "gold_v1",
            "transaction_id": days * 1000 + index,
            "customer_id": 0,
            "terminal_id": 0,
            "tx_datetime": stamp.isoformat(),
            "feature_as_of": stamp.isoformat(),
            "features": features,
        }
        rows.append((health.clock_ns(stamp.isoformat()), json.dumps(request)))
    return rows


def demo():
    reference, current = generated_rows(), generated_rows(days=7)
    shifted = generated_rows(1000.0, days=7)
    missing, lagged = list(current), list(current)
    request = json.loads(missing[0][1])
    del request["features"]["TX_AMOUNT"]
    missing[0] = missing[0][0], json.dumps(request)
    request = json.loads(lagged[0][1])
    request["feature_as_of"] = (
        datetime.fromisoformat(request["tx_datetime"]) - timedelta(seconds=60)
    ).isoformat()
    lagged[0] = lagged[0][0], json.dumps(request)
    scenarios = {
        name: health.compare(reference, rows, nonoverlapping=True)
        for name, rows in {
            "unchanged": current,
            "amount_shift": shifted,
            "missing_feature": missing,
            "feature_clock_lag": lagged,
        }.items()
    }
    expected = (
        scenarios["unchanged"]["features"]["TX_AMOUNT"]["jensen_shannon"] == 0
        and scenarios["amount_shift"]["features"]["TX_AMOUNT"]["jensen_shannon"] == 1
        and scenarios["missing_feature"]["current"]["missing_values"]["TX_AMOUNT"] == 1
        and scenarios["feature_clock_lag"]["current"]["max_feature_lag_seconds"] == 60
    )
    if not expected:
        raise ValueError("Generated monitoring demonstration failed")
    return {
        "schema": "fraud_feature_monitoring_demo_v1",
        "status": "success",
        "scope": "generated_feature_windows_not_native_drift_or_model_quality",
        "scenarios": scenarios,
        "fit_executed": False,
        "native_state_read": False,
        "training_triggered": False,
        "production_promotion": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["demo", "snapshot"])
    parser.add_argument("--state", type=Path, default=ROOT / "data/replay/paced-native-v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        destination = managed_destination(
            args.output
            or ROOT
            / "data/reports/feature-monitoring"
            / (args.action + "-" + uuid4().hex + ".json"),
            "reports",
        )
        if destination.exists():
            raise ValueError("Output exists; preserve the previous report")
        if args.action == "demo":
            result = demo()
        else:
            data = snapshot(args.state)
            result = {
                "schema": "fraud_feature_monitoring_snapshot_v1",
                "status": data["feature_health"]["status"],
                "snapshot_timestamp": data["snapshot_timestamp"],
                "source_scope": data["source_scope"],
                "state_directory": str(args.state.absolute()),
                "feature_health": data["feature_health"],
                "fit_executed": False,
                "features_recomputed": False,
                "training_triggered": False,
                "production_promotion": False,
            }
        write_json(destination, result, overwrite=False)
        print(json.dumps({"status": result["status"], "report": str(destination)}, indent=2))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(1, "Feature monitoring stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
