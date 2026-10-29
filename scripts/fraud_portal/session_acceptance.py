"""Accept a reconciled session without claiming the older pause/resume rehearsal."""

import json
from pathlib import Path

from scripts.fraud_portal import data
from scripts.product_flow import controller as flow

BASE_FILES = ("input.json", "events.sqlite", "checkpoint.sqlite", "session.json")


def document(path):
    path = data.checked_path(path)
    data.require(
        path.is_file() and path.stat().st_size <= 2 * 1024 * 1024,
        "Recibo ausente ou excessivo",
    )
    return json.loads(path.read_bytes())


def acceptance_path(folder):
    """Select deterministically a receipt bound to the current checkpoint bytes."""
    checkpoint_sha = data.sha(data.checked_path(folder / "checkpoint.sqlite"))
    for path in sorted(folder.glob("verification-timing-*.json")):
        checked = document(path)
        if checked.get("checkpoint_sha256") == checkpoint_sha:
            return path
    raise data.PortalError(
        "Sessão sem verificação correspondente ao checkpoint; execute session-verify"
    )


def source_files(folder):
    """Hash actual session/verification files; never synthesize rehearsal.json."""
    folder = data.checked_path(folder)
    for name in BASE_FILES:
        path = data.checked_path(folder / name)
        data.require(path.is_file(), "Fonte da sessão ausente: " + name)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            data.require(
                not sidecar.exists() and not sidecar.is_symlink(),
                "Pare os writers da sessão; sidecar SQLite presente",
            )
    receipt = acceptance_path(folder)
    return {name: data.sha(folder / name) for name in (*BASE_FILES, receipt.name)}


def snapshot(flow_id, value):
    folder = flow.work(flow_id)
    before = source_files(folder)
    config = document(folder / "session.json")
    data.require(
        config.get("schema") == "fraud_product_session_v1"
        and isinstance(config.get("session_id"), str)
        and flow_id == "session-" + config["session_id"]
        and value["flow_id"] == flow_id
        and config.get("input_sha256") == before["input.json"],
        "Sessão, fluxo ou input divergente",
    )
    # The authoritative verifier reconstructs every event, causal request, HTTP
    # receipt, triage and feedback in memory. It never fits, scores or rewrites
    # the accepted checkpoint. A matching filename/hash alone is insufficient.
    actual = flow.verify_ledger(folder, value)
    receipt = document(acceptance_path(folder))
    data.require(
        actual.get("schema") == "fraud_product_flow_verification_v1"
        and actual.get("status") == "product_flow_reconciled"
        and actual.get("causal_requests_and_triage_rebuilt") is True
        and actual.get("source_model_scores_reused") is False
        and actual.get("fit_executed") is False
        and actual.get("champion_modified") is False,
        "Reconciliação completa da sessão obrigatória",
    )
    data.require(
        all(receipt.get(key) == expected for key, expected in actual.items()),
        "Recibo da sessão diverge da reconciliação independente",
    )
    data.require(
        before == source_files(folder) and flow.read(flow_id) == value,
        "Fonte alterada durante a verificação da sessão",
    )
    return actual
