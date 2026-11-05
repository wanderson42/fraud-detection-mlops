"""Guard plans, secrets, stale state and partial ownership without contacting services."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.platform.terraform_foundation import controller as c
from scripts.platform.terraform_foundation import policy as p


@pytest.fixture
def inputs():
    return {
        "owner_id": "a" * 32,
        "kubeconfig": "/private/kubeconfig",
        "credentials": {
            k: "b" * 48 for k in ("postgres-password", "rustfs-access-key", "rustfs-secret-key")
        },
    }


@pytest.fixture
def plan(inputs):
    meta = {"name": p.NAMESPACE, "annotations": {p.ANNOTATION: inputs["owner_id"]}}
    values = {
        "kubernetes_namespace_v1.foundation": {"metadata": [meta]},
        "kubernetes_secret_v1.credentials": {
            "metadata": [{**meta, "name": p.SECRET, "namespace": p.NAMESPACE}],
            "data": inputs["credentials"],
        },
        "helm_release.foundation": {
            "name": p.RELEASE,
            "namespace": p.NAMESPACE,
            "chart": "/work/chart",
            "version": "0.1.0",
            "description": "fraud-iac:" + inputs["owner_id"],
            "values": [f'"existingSecret": "{p.SECRET}"\n'],
        },
    }
    return {
        "format_version": "1.2",
        "complete": True,
        "resource_changes": [
            {"address": name, "mode": "managed", "change": {"actions": ["create"], "after": value}}
            for name, value in values.items()
        ],
    }


def test_create_then_noop_plan_accepted_without_disclosing_credentials(plan, inputs):
    assert p.plan_policy(plan, inputs, Path("/work/chart")) == {"create": 3, "no-op": 0}
    for row in plan["resource_changes"]:
        row["change"]["actions"] = ["no-op"]
    result = p.plan_policy(plan, inputs, Path("/work/chart"))
    assert result == {"create": 0, "no-op": 3}
    assert inputs["credentials"]["postgres-password"] not in json.dumps(result)


@pytest.mark.parametrize("actions", [["delete"], ["update"], ["delete", "create"], ["read"]])
def test_no_update_delete_replacement_or_refresh_adoption(plan, inputs, actions):
    plan["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(ValueError):
        p.plan_policy(plan, inputs, Path("/work/chart"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("errored", True),
        ("complete", False),
        ("deferred_changes", [1]),
        ("resource_drift", [1]),
        ("output_changes", {"password": {"after": "secret"}}),
        ("format_version", "2.0"),
    ],
)
def test_ambiguous_plan_or_outputs_rejected(plan, inputs, key, value):
    plan[key] = value
    with pytest.raises(ValueError):
        p.plan_policy(plan, inputs, Path("/work/chart"))


def test_foreign_resource_and_duplicate_address_rejected(plan, inputs):
    original = deepcopy(plan)
    plan["resource_changes"].append({"address": "kubernetes_namespace_v1.accepted"})
    with pytest.raises(ValueError):
        p.plan_policy(plan, inputs, Path("/work/chart"))
    original["resource_changes"].append(original["resource_changes"][0])
    with pytest.raises(ValueError):
        p.plan_policy(original, inputs, Path("/work/chart"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "fraud-platform"),
        ("namespace", "fraud-platform"),
        ("chart", "/other/chart"),
        ("description", "other-owner"),
        ("values", ['"existingSecret": "accepted-secret"\n']),
        ("version", "9.0.0"),
    ],
)
def test_helm_cannot_adopt_accepted_release_or_other_inputs(plan, inputs, field, value):
    plan["resource_changes"][2]["change"]["after"][field] = value
    with pytest.raises(ValueError):
        p.plan_policy(plan, inputs, Path("/work/chart"))


def test_import_and_secret_rotation_rejected(plan, inputs):
    plan["resource_changes"][0]["change"]["importing"] = {"id": "existing"}
    with pytest.raises(ValueError):
        p.plan_policy(plan, inputs, Path("/work/chart"))
    del plan["resource_changes"][0]["change"]["importing"]
    plan["resource_changes"][1]["change"]["after"]["data"] = {"password": "changed"}
    with pytest.raises(ValueError):
        p.plan_policy(plan, inputs, Path("/work/chart"))


def test_private_publication_refuses_overwrite_symlink_and_public_file(tmp_path):
    target = tmp_path / "owner.json"
    p.private(target, {"owner": "a"})
    assert p.read(target) == {"owner": "a"}
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        p.private(target, {"owner": "b"})
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    with pytest.raises(ValueError):
        p.read(alias)
    with pytest.raises(ValueError):
        p.private(alias, {})
    target.chmod(0o644)
    with pytest.raises(ValueError):
        p.read(target)


def test_existing_namespace_without_state_never_adopted(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "resources", lambda: {"metadata": {"name": p.NAMESPACE}})
    with pytest.raises(ValueError, match="do not adopt"):
        c.ownership(tmp_path, {"owner_id": "a" * 32})


def test_unknown_state_resource_never_adopted(tmp_path):
    p.private(
        tmp_path / "root/terraform.tfstate",
        {
            "resources": [
                {"mode": "managed", "type": "aws_instance", "name": "foreign", "instances": [{}]}
            ]
        },
    )
    with pytest.raises(ValueError):
        c.state_resources(tmp_path)


def test_namespace_uid_replacement_refused(tmp_path, monkeypatch):
    p.private(
        tmp_path / "root/terraform.tfstate",
        {
            "resources": [
                {
                    "mode": "managed",
                    "type": "kubernetes_namespace_v1",
                    "name": "foundation",
                    "instances": [{"attributes": {"metadata": [{"uid": "before"}]}}],
                }
            ]
        },
    )
    monkeypatch.setattr(
        c,
        "resources",
        lambda: {"metadata": {"uid": "replacement", "annotations": {p.ANNOTATION: "a" * 32}}},
    )
    with pytest.raises(ValueError, match="UID"):
        c.ownership(tmp_path, {"owner_id": "a" * 32})


def test_stale_reviewed_plan_refused_before_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "load", lambda work: {})
    (tmp_path / "root").mkdir()
    path = tmp_path / "root" / ("plan-" + "a" * 32)
    path.write_bytes(b"exact-reviewed-plan")
    p.private(tmp_path / "owner.json", {})
    p.private(
        tmp_path / ("plan-" + "a" * 32 + ".json"),
        {
            "plan_sha256": p.sha(path),
            "owner_sha256": p.sha(tmp_path / "owner.json"),
            "state_sha256": None,
        },
    )
    p.private(tmp_path / "root/terraform.tfstate", {"serial": 2})
    monkeypatch.setattr(c, "tf", lambda *a, **k: pytest.fail("must refuse before Terraform"))
    with pytest.raises(ValueError, match="Stale"):
        c.apply(tmp_path, "a" * 32, p.sha(path))


def test_wrong_review_digest_refused_before_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "load", lambda work: {})
    (tmp_path / "root").mkdir()
    path = tmp_path / "root" / ("plan-" + "a" * 32)
    path.write_bytes(b"changed-plan")
    p.private(tmp_path / ("plan-" + "a" * 32 + ".json"), {"plan_sha256": "0" * 64})
    with pytest.raises(ValueError, match="digest"):
        c.apply(tmp_path, "a" * 32, "0" * 64)


def test_subprocess_errors_and_inherited_tf_arguments_not_exposed(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "workspace", lambda: tmp_path)
    seen = {}
    monkeypatch.setenv("TF_CLI_ARGS_apply", "-destroy -auto-approve")

    def fake(args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=1, stdout="private-password", stderr="private-token")

    monkeypatch.setattr(c.subprocess, "run", fake)
    with pytest.raises(ValueError) as error:
        c.command(["terraform", "apply"])
    assert "private-password" not in str(error.value)
    assert "TF_CLI_ARGS_apply" not in seen["env"]


def test_command_kubectl_always_binds_private_context_and_own_namespace(monkeypatch):
    seen = []

    def fake(args, **kwargs):
        seen.extend(str(x) for x in args)
        return SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr(c.f, "run", fake)
    c.kube("get", "pods")
    assert seen[seen.index("--namespace") + 1] == p.NAMESPACE
    assert seen[seen.index("--context") + 1] == "kind-fraud-platform"
    assert "--kubeconfig" in seen


def test_native_marker_verification_preserves_namespace_and_accepted_state(tmp_path, monkeypatch):
    from scripts.platform.terraform_foundation import native as n

    marker = {"owner_id": "a" * 32}
    p.private(tmp_path / "marker.json", marker)
    item = {**marker, "session_id": "accepted", "accepted_before": {"hash": "same"}}
    inv = {
        "postgres": {"pod_uid": "p1", "pvc_uid": "v1"},
        "rustfs": {"pod_uid": "p2", "pvc_uid": "v2"},
    }
    monkeypatch.setattr(c, "state_resources", lambda work: dict.fromkeys(p.ADDRESSES))
    monkeypatch.setattr(n, "inventory", lambda: inv)
    calls = []
    monkeypatch.setattr(n, "sql", lambda identity, write: calls.append(("sql", write)))
    monkeypatch.setattr(n, "s3", lambda identity, write: calls.append(("s3", write)))
    monkeypatch.setattr(
        c, "plan", lambda work: {"counts": {"create": 0, "no-op": 3}, "plan_sha256": "f" * 64}
    )
    monkeypatch.setattr(c, "accepted", lambda key: {"hash": "changed"})
    with pytest.raises(ValueError, match="session changed"):
        n.verify(tmp_path, item, recreate=False)
    assert not list(tmp_path.glob("verification-*.json"))
    monkeypatch.setattr(c, "accepted", lambda key: {"hash": "same"})
    result = n.verify(tmp_path, item, recreate=False)
    assert result["second_plan_no_changes"] and result["accepted_session_preserved"]
    assert not result["cloud_validated"] and not result["pod_recreation_validated"]
    assert calls == [("sql", False), ("s3", False)] * 2


@pytest.fixture
def initialized(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(c, "capacity", lambda: {"available": True})
    monkeypatch.setattr(c, "accepted", lambda key: {"binding": key, "hash": "accepted"})
    monkeypatch.setattr(c, "resources", dict)
    monkeypatch.setattr(c.f, "owner", lambda: {"node_uid": "same-cluster"})
    monkeypatch.setattr(c.f, "managed_destination", lambda path, area: path)
    c.initialize(work, "accepted-session")
    return work


def test_reinitialization_reuses_credentials_and_detects_other_session(initialized):
    before = (initialized / "root/private.auto.tfvars.json").read_bytes()
    assert c.initialize(initialized, "accepted-session")["status"] == "iac_already_initialized"
    assert (initialized / "root/private.auto.tfvars.json").read_bytes() == before
    with pytest.raises(ValueError, match="session"):
        c.initialize(initialized, "other-session")


@pytest.mark.parametrize("mutation", ["extra", "changed", "symlink"])
def test_private_configuration_cannot_change_after_initialization(initialized, mutation, tmp_path):
    path = initialized / "root/main.tf"
    if mutation == "extra":
        (initialized / "root/extra.tf").write_text('resource "aws_instance" "x" {}')
    elif mutation == "changed":
        path.write_text(path.read_text() + "\n# changed")
    else:
        external = tmp_path / "external.tf"
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
    with pytest.raises(ValueError):
        c.load(initialized)


def test_cluster_replaced_after_initialization_is_refused(initialized, monkeypatch):
    monkeypatch.setattr(c.f, "owner", lambda: {"node_uid": "other-cluster"})
    with pytest.raises(ValueError, match="Cluster"):
        c.load(initialized)


def test_partial_initialization_not_overwritten(tmp_path, monkeypatch):
    (tmp_path / "root").mkdir()
    monkeypatch.setattr(c, "resources", dict)
    with pytest.raises(ValueError, match="Partial"):
        c.initialize(tmp_path, "session")
    assert not (tmp_path / "owner.json").exists()


def test_s3_probe_uses_only_new_namespace_endpoint_and_secret(monkeypatch):
    from scripts.platform.terraform_foundation import native as n

    sent = []

    def fake(*args, data=None, **kwargs):
        if args[0] == "create":
            sent.append(json.loads(data))
        return SimpleNamespace(stdout='{"verified":true}')

    monkeypatch.setattr(c, "kube", fake)
    n.s3("a" * 32, write=True)
    container = sent[0]["spec"]["containers"][0]
    assert "http://fraud-iac-foundation-rustfs:9000" in container["command"][-1]
    assert "http://fraud-platform-rustfs:9000" not in container["command"][-1]
    assert "head-bucket" in container["command"][-1]
    assert all(
        x["valueFrom"]["secretKeyRef"]["name"] == p.SECRET
        for x in container["env"]
        if "valueFrom" in x
    )
    assert not sent[0]["spec"]["automountServiceAccountToken"]
