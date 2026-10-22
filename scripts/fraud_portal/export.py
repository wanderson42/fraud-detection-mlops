"""Project-environment adapter: verify accepted sources, export every scored decision once."""

from contextlib import closing
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.case_explanations import controller as explanations
from scripts.fraud_portal import data
from scripts.platform import kind_foundation as f
from scripts.product_flow import controller as flow
from scripts.product_flow.http_scoring import checked_response, digest

from fraud_detection_mlops.data.datasets.native_replay_dataset import readonly_connection
from fraud_detection_mlops.serving.model_release import ReleaseManifest
from fraud_detection_mlops.serving.request_schema import ScoreRequest
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock


def prepare(root, key, flow_id, explanation_id, explanation_sha, no_other_writers):
    data.require(no_other_writers, "Declare PRODUCT_NO_OTHER_WRITERS=1")
    source, value = flow.work(flow_id), flow.read(flow_id)
    if explanation_id is None:
        data.require(explanation_sha is None, "Não informe digest sem relatório")
        # Snapshot verification checks the accepted checkpoint without invoking an explainer.
        explanations.snapshot(flow_id, 1)
        explained, report_result = {}, {"case_count": 0}
    else:
        report_result = explanations.verify(explanation_id, explanation_sha)
        report_dir = explanations.work(explanation_id)
        report_manifest = json.loads((report_dir / "manifest.json").read_bytes())
        data.require(report_manifest["flow_id"] == flow_id, "Relatório pertence a outro fluxo")
        explained = {
            c["request"]["transaction_id"]: c
            for c in json.loads((report_dir / "cases.json").read_bytes())
        }
    before = explanations.source_files(source)
    manifest = ReleaseManifest.model_validate(value["release_manifest"])
    cases = []
    with (
        flow.target(value),
        job_lock(source),
        closing(readonly_connection(source / "checkpoint.sqlite")) as conn,
    ):
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT d.request,d.response,a.payload,f.available_ns,f.fraud_label,h.request_sha256,h.response_sha256 "
            "FROM decisions d JOIN alert_decisions a USING(transaction_id) "
            "JOIN http_exchanges h USING(transaction_id) LEFT JOIN feedback f USING(transaction_id) "
            "ORDER BY d.transaction_id LIMIT 10001"
        ).fetchall()
        data.require(
            0 < len(rows) <= 10000 and len(rows) == value["capture"]["counts"]["decisions"],
            "Fila incompleta ou acima do limite; nenhuma truncagem silenciosa",
        )
        for req, res, triage, available, feedback, req_sha, res_sha in rows:
            request = ScoreRequest.model_validate(json.loads(req))
            response = checked_response(json.loads(res), request, manifest)
            data.require(
                (digest(request.model_dump()), digest(response.model_dump()))
                == (req_sha, res_sha),
                "Recibo HTTP divergente",
            )
            case = {
                "request": request.model_dump(),
                "response": response.model_dump(),
                "triage": json.loads(triage),
                "feedback_available_ns": available,
                "transaction_feedback": feedback,
                "SHAP_explanation": None,
            }
            if request.transaction_id in explained:
                supplied = explained[request.transaction_id]
                data.require(
                    all(case[n] == supplied[n] for n in case if n != "SHAP_explanation"),
                    "Explicação não corresponde à decisão do ledger",
                )
                case["SHAP_explanation"] = supplied["SHAP_explanation"]
            cases.append(case)
        conn.execute("COMMIT")
    data.require(
        before == explanations.source_files(source) and flow.read(flow_id) == value,
        "Fonte alterada durante a preparação do portal",
    )
    data.require(
        sum(bool(c["SHAP_explanation"]) for c in cases) == report_result["case_count"],
        "Cobertura SHAP divergente",
    )
    payload = {
        "schema": "fraud_investigation_snapshot_v1",
        "flow_id": flow_id,
        "explanation_id": explanation_id,
        "source_explanation_manifest_sha256": explanation_sha,
        "model_sha256": manifest.model_sha256,
        "serving_release_id": manifest.serving_release_id,
        "model_id": manifest.model_id,
        "registry_version_at_capture": value["active"].get("version", ""),
        "source_files": before,
        "alert_policy": value["alert_policy"],
        "cases": cases,
    }
    directory = f.managed_destination(root / "data/reports/fraud-portal" / key, "reports")
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        stored = json.loads(data.checked_path(directory / "manifest.json").read_bytes())
        existing, _ = data.load_bundle(directory, data.sha(directory / "manifest.json"))
        data.require(existing == payload, "Snapshot existente difere; preserve e revise a fonte")
        exported = stored
    else:
        with TemporaryDirectory(prefix=".portal-", dir=directory.parent) as temp:
            stage = Path(temp) / "snapshot"
            stage.mkdir(mode=0o700)
            (stage / "snapshot.json").write_bytes(data.encoded(payload))
            exported = {
                "schema": "fraud_investigation_portal_v1",
                "portal_id": key,
                "created_at": datetime.now(UTC).isoformat(),
                "snapshot_sha256": data.sha(stage / "snapshot.json"),
                "model_sha256": manifest.model_sha256,
                "serving_release_id": manifest.serving_release_id,
                "case_count": len(cases),
                "source_explanation_manifest_sha256": explanation_sha,
                "source_files_unchanged": True,
                "current_champion_checked_at_capture": True,
                "training_executed": False,
                "champion_modified": False,
            }
            (stage / "manifest.json").write_bytes(data.encoded(exported))
            os.rename(stage, directory)
    receipt = {
        "schema": "fraud_portal_access_v1",
        "portal_id": key,
        "directory": str(directory),
        "manifest_sha256": data.sha(directory / "manifest.json"),
    }
    access = f.managed_destination(
        root / "data/storage/kind-platform/portal" / (key + ".json"), "storage"
    )
    access.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if access.exists():
        data.require(
            json.loads(access.read_bytes()) == receipt, "Configuração do portal divergente"
        )
    else:
        f.private_json(access, receipt)
    return {
        "status": "portal_snapshot_prepared",
        **receipt,
        "summary": data.summary(cases),
        "source_files_unchanged": True,
        "champion_modified": False,
    }
