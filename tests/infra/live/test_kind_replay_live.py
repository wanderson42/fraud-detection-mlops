"""Opt-in actual pod termination and SQLite transaction recovery on a new rehearsal PVC."""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_REPLAY_LIVE") != "1", reason="Opt-in KinD replay interruption"
)
def test_open_transaction_recovers_after_pod_recreation_on_same_pvc():
    import json

    from scripts.platform import kind_foundation as f
    from scripts.platform.kind_replay import rehearse

    with f.exclusive():
        result = rehearse()
    assert result["status"] == "success"
    assert result["interrupted_pod"]["uid"] != result["resumed_pod"]["uid"]
    assert result["before"] == result["after"]
    assert result["worker"]["all_schema_and_table_rows_equal"]
    assert result["worker"]["inflight_transaction_rollback_validated"]
    assert result["temporary_compute_removed"] and result["checkpoint_pvc_retained"]
    print(json.dumps(result, indent=2))
