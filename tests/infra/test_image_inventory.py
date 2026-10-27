"""Inventory separates Docker/CRI and never turns missing consumers into deletion permission."""

import json
from types import SimpleNamespace

import pytest
from scripts.platform import image_inventory as audit


def fixture_document():
    return {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {"name": "fraud-api", "uid": "deployment"},
                "spec": {
                    "replicas": 0,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "api",
                                    "image": "fraud-api:kept",
                                    "env": [{"name": "PASSWORD", "value": "secret"}],
                                }
                            ]
                        }
                    },
                },
            },
            {
                "kind": "Pod",
                "metadata": {
                    "name": "api-pod",
                    "uid": "pod",
                    "annotations": {"private": "secret"},
                },
                "spec": {
                    "containers": [{"name": "api", "image": "fraud-api:kept"}],
                    "initContainers": [{"name": "seed", "image": "carrier:old"}],
                },
                "status": {
                    "containerStatuses": [{"name": "api", "imageID": "containerd://sha256:api"}]
                },
            },
            {
                "kind": "CronJob",
                "metadata": {"name": "job", "uid": "cron"},
                "spec": {
                    "jobTemplate": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [{"name": "job", "image": "future-job:kept"}]
                                }
                            }
                        }
                    }
                },
            },
        ]
    }


def test_workload_projection_preserves_zero_replica_and_cronjob_consumers_without_secrets():
    rows = audit.consumers(fixture_document())
    assert len(rows) == 4
    assert {row["kind"] for row in rows} == {"Deployment", "Pod", "CronJob"}
    assert "secret" not in json.dumps(rows)


def test_collection_deduplicates_aliases_separates_accounting_and_observes_changes(monkeypatch):
    calls, kube_calls = [], []
    monkeypatch.setattr(audit, "source_inventory", lambda: {"Dockerfile": "unchanged"})
    monkeypatch.setattr(audit, "locked_images", lambda: {"upstream@sha256:lock"})

    def runner(args, **kwargs):
        calls.append(args)
        if args[0] == "kubectl":
            assert args[args.index("--context") + 1] == "kind-fraud-platform"
            document = fixture_document()
            if kube_calls:
                document["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
                    "changed:tag"
                )
            kube_calls.append(True)
            return json.dumps(document)
        if args[:3] == ["docker", "container", "inspect"]:
            return json.dumps(
                [
                    {
                        "Image": "sha256:node",
                        "Config": {
                            "Labels": {"io.x-k8s.kind.cluster": "fraud-platform"},
                            "Env": ["secret"],
                        },
                    }
                ]
            )
        if args[:2] == ["docker", "exec"]:
            assert args[2] == audit.NODE and args[3] == "crictl"
            return json.dumps(
                {
                    "images": [
                        {
                            "id": "sha256:api",
                            "size": "100",
                            "repoTags": ["docker.io/library/fraud-api:kept"],
                        },
                        {"id": "sha256:api", "size": "100", "repoTags": ["fraud-api:another"]},
                        {
                            "id": "sha256:retained",
                            "size": "200",
                            "repoTags": ["fraud-job:retained"],
                        },
                    ]
                }
            )
        if args[:3] == ["docker", "image", "ls"]:
            assert args[-1] == "reference=fraud-*"
            return "sha256:host\nsha256:host\n"
        if args[:3] == ["docker", "image", "inspect"]:
            if args[-1] == "upstream@sha256:lock":
                return None
            return json.dumps(
                [
                    {
                        "Id": "sha256:node" if args[-1] == "sha256:node" else "sha256:host",
                        "Size": 900,
                        "Config": {"Env": ["secret"]},
                        "RepoTags": ["fraud-api:kept"],
                        "RootFS": {"Layers": ["sha256:shared"]},
                    }
                ]
            )
        if args[:3] == ["docker", "system", "df"]:
            return '{"Type":"Images","Size":"5GB","Reclaimable":"3GB","private":"secret"}\n'
        assert args == ["git", "rev-parse", "HEAD"]
        return "a" * 40 + "\n"

    report = audit.collect(runner)
    assert len(report["CRI_images"]) == 2
    image = next(row for row in report["CRI_images"] if row["id"] == "sha256:api")
    assert image["size_bytes"] == 100 and len(image["aliases"]) == 2
    assert {v["kind"] for v in image["consumers"]} == {"Deployment", "Pod"}
    retained = next(row for row in report["CRI_images"] if row["id"] == "sha256:retained")
    assert retained["reference_scope"] == "no_current_reference_not_deletion_authorization"
    assert len(report["Docker_images"]) == 2
    assert report["Docker_references_unavailable_locally"] == ["upstream@sha256:lock"]
    assert report["consumer_projection_unchanged_during_collection"] is False
    assert report["consumers_after"]
    assert "secret" not in json.dumps(report)
    assert report["deployment_modified"] is report["images_deleted"] is False
    assert not any(
        word in call
        for call in calls
        for word in ("run", "pull", "prune", "rm", "apply", "delete")
    )


def test_foreign_node_is_rejected_before_cri_exec(monkeypatch):
    calls = []
    monkeypatch.setattr(audit, "source_inventory", dict)

    def runner(args, **kwargs):
        calls.append(args)
        return json.dumps(
            fixture_document()
            if args[0] == "kubectl"
            else [{"Config": {"Labels": {"io.x-k8s.kind.cluster": "energy"}}}]
        )

    with pytest.raises(ValueError, match="does not belong"):
        audit.collect(runner)
    assert len(calls) == 2


def test_runner_withholds_unreviewed_error_output(monkeypatch):
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="secret"),
    )
    assert audit.command(["docker", "image", "inspect", "missing"], optional=True) is None
    with pytest.raises(ValueError, match=r"docker inventory command failed \(exit 1\)") as exc:
        audit.command(["docker", "exec", "node", "crictl"])
    assert "secret" not in str(exc.value)


def test_source_inventory_matches_archive_and_includes_context_and_dependency_inputs():
    values = audit.source_inventory()
    for name in (
        "infra/kind/round/Dockerfile",
        "infra/kind/round/Dockerfile.dockerignore",
        "infra/tracking/requirements.txt",
        "poetry.lock",
    ):
        assert len(values[name]) == 64
    assert len(values) > 30
