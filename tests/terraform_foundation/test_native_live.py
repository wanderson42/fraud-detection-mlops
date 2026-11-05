"""Explicit pod interruption of only the already applied isolated IaC foundation."""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_LOCAL_IAC_LIVE") != "1",
    reason="Opt-in isolated Terraform readbacks and pod recreation",
)
def test_isolated_foundation_persistence_and_second_plan():
    from scripts.platform.terraform_foundation import controller as c
    from scripts.platform.terraform_foundation import native
    from scripts.platform.terraform_foundation import policy as p

    p.require(os.environ.get("FRAUD_IAC_NO_OTHER_WRITERS") == "1", "Declare no other writers")
    os.umask(0o077)
    with c.locked() as work:
        item = c.load(work)
        result = native.verify(work, item, recreate=True)
    print(result["status"], "private receipts:", work)
    assert result["pod_recreation_validated"] and result["pvc_identities_preserved"]
    assert result["second_plan_no_changes"] and result["accepted_session_preserved"]
