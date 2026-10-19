"""Read-only native parity and local timing assessment, using the preserved corrective oracle."""

import argparse
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from scripts.monitoring.collector import snapshot
from scripts.monitoring.telemetry import readonly
from scripts.replay.paced_triage_replay import operations_identity

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.native_replay_dataset import stream_events
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.evaluation.native_triage_assessment import (
    ledger_frame,
    triage_quality,
)
from fraud_detection_mlops.streaming.replay import numerical_corrective_replay as correction
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock


def assess(directory, root=PROJECT_ROOT):
    code = operations_identity(root)
    directory = directory.absolute()
    with job_lock(directory):
        metadata = json.loads(correction.regular_file(directory / "metadata.json").read_text())
        if metadata["code"] != code or metadata["source_scope"] != (
            "materialized_native_events_operational_reuse_not_new_quality_confirmation"
        ):
            raise ValueError("Assessment requires the committed native operational identity")
        receipt = json.loads(
            correction.regular_file(
                root / "data/replay/native-numerical-input-receipt.json"
            ).read_text()
        )
        inputs = Path(receipt["input_path"])
        manifest = correction.verify_prepared(inputs, receipt["manifest_sha256"], root)
        if (
            metadata["input_sha256"] != receipt["manifest_sha256"]
            or metadata["policy"] != manifest["policy"]
        ):
            raise ValueError("Assessment input or policy differs")
        data = snapshot(directory)
        if not data["complete"] or data["counts"]["decisions"] != manifest["scored_rows"]:
            raise ValueError("Complete native checkpoint required before assessment")
        state = directory / "checkpoint.sqlite"
        with closing(readonly(state)) as con:
            for event in stream_events(Path(manifest["original_input"]) / "events.sqlite"):
                saved = con.execute(
                    "SELECT payload FROM events WHERE event_id=?", (event.event_id,)
                ).fetchone()
                if saved is None or json.loads(saved[0]) != event.model_dump():
                    raise ValueError("Source event identity differs")
        oracle = pd.read_parquet(inputs / "oracle.parquet")
        policy = CustomerDayAlertConfig.model_validate(manifest["policy"])
        frame = ledger_frame(state, oracle, policy, manifest["protocol"])
        actual = correction.saved_decisions(state)
        ordered = oracle.sort_values("TRANSACTION_ID")
        if [r[0] for r in actual] != ordered.TRANSACTION_ID.tolist():
            raise ValueError("Exact feature coverage differs")
        for name in FEATURE_COLUMNS:
            if not np.array_equal([r[1][name] for r in actual], ordered[name].to_numpy()):
                raise ValueError(f"Exact deterministic feature parity failed: {name}")
        with closing(readonly(directory / "telemetry.sqlite")) as con:
            sessions = con.execute("SELECT state FROM sessions").fetchall()
        if any(row[0] == "running" for row in sessions):
            raise ValueError("Stop the writer before assessment")
        timing = {
            name: {
                "count": len(values),
                "p50_seconds": float(np.quantile(values, 0.5)),
                "p95_seconds": float(np.quantile(values, 0.95)),
                "max_seconds": max(values),
            }
            for name, values in data["durations"].items()
            if values
        }
        eligible = (
            metadata["speed"] == 1
            and len(sessions) == 1
            and not data["unmeasured_committed_events"]
            and bool(timing)
        )
        report = {
            "status": "success",
            "counts": data["counts"],
            "code": code,
            "metadata_sha256": sha256(directory / "metadata.json"),
            "input_manifest_sha256": receipt["manifest_sha256"],
            "checkpoint_sha256": sha256(state),
            "telemetry_sha256": sha256(directory / "telemetry.sqlite"),
            "feature_parity_exact": True,
            "score_and_causal_decision_parity": True,
            "score_parity_atol": manifest["protocol"]["parity"]["scores_atol"],
            "score_parity_rtol": manifest["protocol"]["parity"]["scores_rtol"],
            "quality": triage_quality(
                frame, policy, manifest["protocol"]["windows"]["scored_replay"]
            ),
            "timing": timing,
            "speed": metadata["speed"],
            "sessions": len(sessions),
            "unmeasured_committed_events": data["unmeasured_committed_events"],
            "measurement_scope": metadata["measurement_scope"],
            "continuous_1x_lab_target_evaluable": eligible,
            "continuous_1x_p95_at_most_one_second": (
                timing["scheduled_to_commit"]["p95_seconds"] <= 1 if eligible else None
            ),
            "sla_validated": False,
            "new_independent_quality_confirmation": False,
            "source_Silver_reopened": False,
            "production_promotion": False,
        }
        final = directory / "assessment"
        if final.exists():
            saved_receipt = json.loads(correction.regular_file(final / "receipt.json").read_text())
            if saved_receipt != {
                "result_sha256": sha256(correction.regular_file(final / "result.json"))
            }:
                raise ValueError("Saved assessment receipt differs")
            if json.loads(correction.regular_file(final / "result.json").read_text()) != report:
                raise ValueError("Saved assessment differs; preserve evidence")
            status = "reused"
        else:
            with TemporaryDirectory(prefix=".assessment-", dir=directory) as temporary:
                stage = Path(temporary) / "assessment"
                stage.mkdir()
                write_json(stage / "result.json", report, overwrite=False)
                write_json(
                    stage / "receipt.json",
                    {"result_sha256": sha256(stage / "result.json")},
                    overwrite=False,
                )
                stage.rename(final)
            status = "success"
        return {
            "status": status,
            "result_path": str(final / "result.json"),
            "receipt_sha256": sha256(final / "receipt.json"),
            **{
                k: report[k]
                for k in (
                    "counts",
                    "timing",
                    "continuous_1x_lab_target_evaluable",
                    "continuous_1x_p95_at_most_one_second",
                    "sla_validated",
                )
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    print(json.dumps(assess(args.state, args.project_root), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
