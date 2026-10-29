"""Bind bounded SHAP reports to accepted HTTP decisions and the current champion release."""

import argparse
from contextlib import closing
from importlib.metadata import version
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
from scipy.special import expit
from scripts.case_explanations import hgb_shap, report
from scripts.operational_round import policy as p
from scripts.platform import kind_foundation as f
from scripts.platform.serving_recovery import resolve_active
from scripts.product_flow import controller as flow
from scripts.product_flow.http_scoring import checked_response, digest

from fraud_detection_mlops.data.datasets.native_replay_dataset import readonly_connection
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.serving.model_release import ReleaseManifest, load_release
from fraud_detection_mlops.serving.request_schema import ScoreRequest
from fraud_detection_mlops.storage.managed_paths import managed_destination
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock


def work(key):
    return managed_destination(
        f.ROOT / "data/reports/case-explanations" / p.round_id(key), "reports"
    )


def pins():
    paths = list((f.ROOT / "scripts/case_explanations").glob("*.py"))
    paths.append(f.ROOT / "fraud_detection_mlops/evaluation/validation_diagnostics.py")
    paths.append(f.ROOT / "scripts/fraud_portal/session_acceptance.py")
    return {v.relative_to(f.ROOT).as_posix(): p.sha(v) for v in paths}


def committed():
    values = pins()
    for name, expected in values.items():
        result = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        import hashlib

        p.require(
            result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == expected,
            "Commit reviewed case explanation code before run: " + name,
        )
    return values


def source_files(folder):
    legacy = folder / "rehearsal.json"
    if not legacy.exists() and not legacy.is_symlink():
        from scripts.fraud_portal import session_acceptance

        return session_acceptance.source_files(folder)
    names = ("input.json", "events.sqlite", "checkpoint.sqlite", "rehearsal.json")
    for name in names:
        path = managed_destination(folder / name, "storage")
        p.require(path.is_file(), "Use the accepted completed product flow: missing " + name)
        p.require(not Path(str(path) + "-wal").exists(), "Stop product writers; WAL is present")
    return {name: p.sha(folder / name) for name in names}


def snapshot(key, limit):
    """Read accepted cases; sessions also undergo in-memory ledger reconciliation."""
    p.require(type(limit) is int and 1 <= limit <= 20, "Choose 1..20 cases")
    value, folder = flow.read(key), flow.work(key)
    before = source_files(folder)
    if "rehearsal.json" in before:
        acceptance = json.loads((folder / "rehearsal.json").read_bytes())
        p.require(
            acceptance["status"] == "product_rehearsal_passed"
            and acceptance["checkpoint_sha256"] == before["checkpoint.sqlite"]
            and acceptance["flow_id"] == key
            and acceptance["model_sha256"] == value["release_manifest"]["model_sha256"]
            and acceptance["serving_release_id"]
            == value["release_manifest"]["serving_release_id"],
            "Accepted product checkpoint or release differs",
        )
    else:
        from scripts.fraud_portal import session_acceptance

        session_acceptance.snapshot(key, value)
    cases = flow.cases(key, limit)["cases"]
    p.require(bool(cases), "The accepted flow contains no cases")
    manifest = ReleaseManifest.model_validate(value["release_manifest"])
    with job_lock(folder), closing(readonly_connection(folder / "checkpoint.sqlite")) as conn:
        conn.execute("BEGIN")
        saved = json.loads(
            conn.execute("SELECT payload FROM identity WHERE singleton=1").fetchone()[0]
        )
        scorer = flow.StoredScorer(conn, manifest)
        with flow.replay(":memory:", value, scorer) as empty:
            p.require(saved == empty.identity, "Checkpoint identity binding differs")
        for case in cases:
            request = ScoreRequest.model_validate(case["request"])
            response = checked_response(case["response"], request, manifest)
            row = conn.execute(
                "SELECT request_sha256,response_sha256 FROM http_exchanges WHERE transaction_id=?",
                (request.transaction_id,),
            ).fetchone()
            p.require(
                row == (digest(request.model_dump()), digest(response.model_dump())),
                "Case request/response differs from its HTTP exchange",
            )
        conn.execute("COMMIT")
    p.require(source_files(folder) == before, "Product source changed while reading cases")
    return value, cases, before


def compute(runtime, cases):
    explanations = hgb_shap.explain(runtime, cases)
    result = []
    for case, explanation in zip(cases, explanations, strict=True):
        result.append(
            {
                **case,
                "SHAP_explanation": {
                    **explanation,
                    "request_sha256": digest(case["request"]),
                    "response_sha256": digest(case["response"]),
                    "model_sha256": runtime.manifest.model_sha256,
                    "serving_release_id": runtime.manifest.serving_release_id,
                },
            }
        )
    return result


def run(flow_id, key, limit, no_other_writers):
    p.require(no_other_writers, "Declare no other product, serving or Registry writers")
    sources = committed()
    output = work(key)
    p.require(
        not output.exists(), "Report already exists; verify with its printed manifest SHA-256"
    )
    value, cases, original = snapshot(flow_id, limit)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".case-shap-", dir=output.parent) as temporary:
        stage = Path(temporary) / "report"
        stage.mkdir(mode=0o700)
        with flow.target(value):
            active = resolve_active(value["active"])
            slot = active["slot"]
            release = active["plan"]["releases"][slot]
            directory = managed_destination(Path(active["paths"][slot]), "storage")
            runtime = load_release(directory, release["manifest_sha256"])
            p.require(
                runtime.manifest.model_dump() == value["release_manifest"],
                "Local release differs from the recorded HTTP champion",
            )
            explained = compute(runtime, cases)
            manifest = {
                "schema": "fraud_case_explanations_v1",
                "status": "verified",
                "explanation_id": key,
                "flow_id": flow_id,
                "requested_limit": limit,
                "selection": "alerts_first_score_descending_transaction_id_no_label_selection",
                "model_sha256": runtime.manifest.model_sha256,
                "serving_release_id": runtime.manifest.serving_release_id,
                "release_manifest_sha256": release["manifest_sha256"],
                "release_manifest": runtime.manifest.model_dump(),
                "source_files": original,
                "source_pins": sources,
                "versions": {
                    n: version(n) for n in ("shap", "numpy", "scipy", "scikit-learn", "pandas")
                },
                "transaction_ids": [c["request"]["transaction_id"] for c in explained],
                "feature_order": FEATURE_COLUMNS,
                "method_reference": "fraud_detection_mlops/evaluation/validation_diagnostics.py:explain_sample",
                "adapter_reason": "same_raw_path_dependent_method_with_explicit_label_free_case_selection",
                "additivity_rtol": 1e-7,
                "additivity_atol": 1e-7,
                "score_rtol": 1e-12,
                "score_atol": 1e-12,
                "model_training_executed": False,
                "features_recomputed": False,
                "champion_modified": False,
                "current_champion_checked_before_and_after": True,
                "global_quality_estimated": False,
            }
            report.write(stage, explained, manifest)
        p.require(
            flow.read(flow_id) == value and source_files(flow.work(flow_id)) == original,
            "Product flow changed during explanation",
        )
        p.require(pins() == sources, "Explanation source changed during computation")
        manifest["files"] = {v.name: p.sha(v) for v in sorted(stage.iterdir())}
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, allow_nan=False) + "\n"
        )
        p.require(not output.exists(), "Another report owns the destination")
        os.rename(stage, output)
    return {
        "status": "case_explanations_published",
        "directory": str(output),
        "report": str(output / "index.html"),
        "manifest_sha256": p.sha(output / "manifest.json"),
        "case_count": len(cases),
        "model_sha256": manifest["model_sha256"],
        "serving_release_id": manifest["serving_release_id"],
        "champion_modified": False,
    }


def verify(key, expected):
    """Offline verification against the operator's external report digest; no model load."""
    folder = work(key)
    managed_destination(folder / "manifest.json", "reports")
    p.require(
        isinstance(expected, str)
        and len(expected) == 64
        and p.sha(folder / "manifest.json") == expected,
        "Pin the exact report manifest SHA-256",
    )
    manifest = json.loads((folder / "manifest.json").read_bytes())
    p.require(
        manifest["schema"] == "fraud_case_explanations_v1"
        and manifest["explanation_id"] == key
        and manifest["source_pins"] == pins(),
        "Report schema, identity or implementation differs",
    )
    value, original_cases, hashes = snapshot(manifest["flow_id"], manifest["requested_limit"])
    p.require(
        hashes == manifest["source_files"]
        and value["release_manifest"] == manifest["release_manifest"],
        "Report source ledger or release changed",
    )
    expected_files = {
        "index.html",
        "cases.json",
        *[f"case-{tid}.svg" for tid in manifest["transaction_ids"]],
    }
    p.require(
        set(manifest["files"]) == expected_files
        and {v.name for v in folder.iterdir()} == {*expected_files, "manifest.json"},
        "Unexpected report files",
    )
    for name, expected_hash in manifest["files"].items():
        path = managed_destination(folder / name, "reports")
        p.require(
            path.is_file() and p.sha(path) == expected_hash, "Report artifact changed: " + name
        )
    cases = json.loads((folder / "cases.json").read_bytes())
    p.require(len(cases) == len(original_cases), "Report case count differs")
    for case, original in zip(cases, original_cases, strict=True):
        explanation = case["SHAP_explanation"]
        p.require(
            {k: v for k, v in case.items() if k != "SHAP_explanation"}
            == {k: v for k, v in original.items() if k != "SHAP_explanation"},
            "Report case differs from the recorded decision",
        )
        contributions = explanation["contributions"]
        margin = explanation["base_log_odds"] + sum(contributions.values())
        p.require(
            explanation["feature_order"] == FEATURE_COLUMNS
            and list(contributions) == FEATURE_COLUMNS
            and np.isfinite([*contributions.values(), margin, explanation["raw_log_odds"]]).all()
            and np.isclose(margin, explanation["raw_log_odds"], rtol=1e-7, atol=1e-7)
            and np.isclose(expit(margin), case["response"]["score"], rtol=1e-7, atol=1e-7)
            and explanation["model_sha256"] == case["response"]["model_sha256"]
            and explanation["serving_release_id"] == case["response"]["serving_release_id"]
            and explanation["request_sha256"] == digest(case["request"])
            and explanation["response_sha256"] == digest(case["response"]),
            "Explanation arithmetic or identity differs",
        )
    p.require(
        [c["request"]["transaction_id"] for c in cases] == manifest["transaction_ids"],
        "Report case order differs",
    )
    return {
        "status": "case_explanations_verified",
        "case_count": len(cases),
        "model_sha256": manifest["model_sha256"],
        "manifest_sha256": expected,
        "report": str(folder / "index.html"),
        "model_loaded": False,
        "champion_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "verify", "cases"])
    parser.add_argument("--flow-id", default="champion-http-v1")
    parser.add_argument("--explanation-id", default="champion-shap-v1")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "run":
            with f.exclusive():
                result = run(args.flow_id, args.explanation_id, args.limit, args.no_other_writers)
        else:
            result = verify(args.explanation_id, args.expected_manifest_sha256)
            if args.action == "cases":
                result["cases"] = json.loads(
                    (work(args.explanation_id) / "cases.json").read_bytes()
                )
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f"Case explanations stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
