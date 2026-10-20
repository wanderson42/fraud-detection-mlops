"""Operate the isolated KinD foundation; never adopt Compose or historical volumes."""

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from uuid import uuid4

from fraud_detection_mlops.storage.managed_paths import managed_destination

ROOT = Path(__file__).resolve().parents[2]
CLUSTER = "fraud-platform"
CONTEXT = "kind-" + CLUSTER
NAMESPACE = "fraud-platform"
RELEASE = "fraud-platform"
SECRET = "fraud-platform-credentials"
CHART = ROOT / "infra/helm/fraud-platform"
COMPONENTS = ("postgres", "rustfs")
LABELS = {"app.kubernetes.io/part-of": "fraud-platform", "fraud.foundation": "v1"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def workspace():
    return managed_destination(ROOT / "data/storage/kind-platform", "storage")


def private_json(path, value):
    """Write a new private file; refuse symlinks and overwrites."""
    managed_destination(path, "storage")
    data = (json.dumps(value, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)


@contextmanager
def exclusive():
    work = workspace()
    require(work.is_dir(), "Run init first")
    path = managed_destination(work / "operation.lock", "storage")
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another foundation operation is running") from exc
        yield


def run(args, *, data=None, timeout=60, check=True):
    work = workspace()
    caches = {
        "HELM_CACHE_HOME": "helm/cache",
        "HELM_CONFIG_HOME": "helm/config",
        "HELM_DATA_HOME": "helm/data",
        "KUBECACHEDIR": "kube-cache",
    }
    completed = subprocess.run(
        [str(arg) for arg in args],
        cwd=ROOT,
        env={
            **os.environ,
            "KIND_EXPERIMENTAL_PROVIDER": "docker",
            **{
                name: str(managed_destination(work / relative, "storage"))
                for name, relative in caches.items()
            },
        },
        input=data,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    require(
        not check or completed.returncode == 0,
        f"{args[0]} command failed (exit {completed.returncode}); output withheld. Inspect status; preserve resources.",
    )
    return completed


def kube(*args, data=None, check=True, timeout=60):
    return run(
        [
            "kubectl",
            "--kubeconfig",
            managed_destination(workspace() / "kubeconfig", "storage"),
            "--cache-dir",
            managed_destination(workspace() / "kube-cache", "storage"),
            "--context",
            CONTEXT,
            "--namespace",
            NAMESPACE,
            *args,
        ],
        data=data,
        check=check,
        timeout=timeout,
    )


def helm(*args, timeout=60):
    return run(
        [
            "helm",
            *args,
            "--kubeconfig",
            managed_destination(workspace() / "kubeconfig", "storage"),
            "--kube-context",
            CONTEXT,
            "--namespace",
            NAMESPACE,
        ],
        timeout=timeout,
    )


def images():
    return json.loads((ROOT / "infra/kind/images.lock.json").read_text())["images"]


def credentials():
    path = managed_destination(workspace() / "credentials.json", "storage")
    require(path.stat().st_mode & 0o077 == 0, "credentials.json must have mode 600")
    value = json.loads(path.read_text())
    require(
        set(value) == {"postgres-password", "rustfs-access-key", "rustfs-secret-key"},
        "Unexpected credential keys",
    )
    require(
        all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{20,64}", v) for v in value.values()),
        "Invalid local credentials",
    )
    return value


def initialize():
    work = workspace()
    if work.exists():
        if (work / "credentials.json").is_file():
            credentials()
            return {"status": "already_initialized", "workspace": str(work)}
        require(
            all(p.name in {"helm", "kube-cache"} for p in work.iterdir()),
            "Incomplete workspace; preserve and inspect it",
        )
    else:
        work.mkdir(parents=True, mode=0o700)
    work.chmod(0o700)
    private_json(
        work / "credentials.json",
        {
            "postgres-password": secrets.token_hex(24),
            "rustfs-access-key": secrets.token_hex(12),
            "rustfs-secret-key": secrets.token_hex(24),
        },
    )
    return {"status": "initialized", "workspace": str(work)}


def validate():
    run(["helm", "lint", "--strict", str(CHART), "--kube-version", "1.35.8"])
    run(
        [
            "helm",
            "template",
            RELEASE,
            str(CHART),
            "--namespace",
            NAMESPACE,
            "--kube-version",
            "1.35.8",
        ]
    )
    return {"status": "templates_validated", "native_cluster_validated": False}


def versions():
    client = json.loads(run(["kubectl", "version", "--client", "-o", "json"]).stdout)[
        "clientVersion"
    ]
    require(
        client["major"] == "1" and int(client["minor"].rstrip("+")) in (34, 35, 36),
        "kubectl must be within one minor of Kubernetes 1.35",
    )
    helm_version = run(["helm", "version", "--short"]).stdout.strip()
    require(helm_version.startswith("v4.3."), "This first increment targets Helm 4.3.x")
    return {
        "kind": run(["kind", "version"]).stdout.strip(),
        "kubectl": client["gitVersion"],
        "helm": helm_version,
        "docker": run(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip(),
    }


def owner():
    path = managed_destination(workspace() / "owner.json", "storage")
    require(path.is_file(), "No ownership receipt; do not adopt an existing cluster")
    saved = json.loads(path.read_text())
    container = json.loads(run(["docker", "inspect", CLUSTER + "-control-plane"]).stdout)
    require(len(container) == 1, "Expected one dedicated control plane")
    info = container[0]
    require(
        info["Id"] == saved["container_id"], "Control-plane identity changed; inspect before reuse"
    )
    require(
        info["Config"].get("Labels", {}).get("io.x-k8s.kind.cluster") == CLUSTER,
        "Wrong KinD owner label",
    )
    node = json.loads(kube("get", "node", CLUSTER + "-control-plane", "-o", "json").stdout)
    require(
        node["status"]["nodeInfo"]["kubeletVersion"] == "v1.35.8",
        "Unexpected Kubernetes node version",
    )
    require(node["metadata"]["uid"] == saved["node_uid"], "Kubernetes node identity changed")
    return saved


def create():
    credentials()
    info = versions()
    existing = run(["kind", "get", "clusters"]).stdout.split()
    if CLUSTER in existing:
        owner()
        return {"status": "cluster_already_owned", "versions": info}
    require(
        not (workspace() / "owner.json").exists(),
        "Previous ownership exists; refuse automatic cluster replacement",
    )
    path = managed_destination(workspace() / "kubeconfig", "storage")
    require(not path.exists(), "kubeconfig already exists; inspect incomplete creation")
    run(
        [
            "kind",
            "create",
            "cluster",
            "--name",
            CLUSTER,
            "--config",
            ROOT / "infra/kind/cluster.yaml",
            "--kubeconfig",
            path,
            "--wait",
            "180s",
        ],
        timeout=300,
    )
    os.chmod(path, 0o600)
    container = json.loads(run(["docker", "inspect", CLUSTER + "-control-plane"]).stdout)[0]
    node = json.loads(kube("get", "node", CLUSTER + "-control-plane", "-o", "json").stdout)
    require(
        node["status"]["nodeInfo"]["kubeletVersion"] == "v1.35.8", "Unexpected new node version"
    )
    saved = {
        "schema": "fraud_kind_owner_v1",
        "container_id": container["Id"],
        "node_uid": node["metadata"]["uid"],
        "versions": info,
        "node_image": images()["node"],
    }
    private_json(workspace() / "owner.json", saved)
    owner()
    return {"status": "cluster_created", "versions": info, "global_kubeconfig_modified": False}


def deploy():
    validate()
    owner()
    ns = json.loads(
        kube("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json").stdout or "null"
    )
    if ns is None:
        kube(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": NAMESPACE, "labels": LABELS},
                }
            ),
        )
    else:
        require(
            all(ns["metadata"].get("labels", {}).get(k) == v for k, v in LABELS.items()),
            "Namespace belongs to another flow",
        )
    value = credentials()
    encoded = {key: base64.b64encode(v.encode()).decode() for key, v in value.items()}
    current = json.loads(
        kube("get", "secret", SECRET, "--ignore-not-found", "-o", "json").stdout or "null"
    )
    if current is None:
        kube(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": SECRET, "labels": LABELS},
                    "type": "Opaque",
                    "data": encoded,
                }
            ),
        )
    else:
        require(
            current.get("data") == encoded
            and all(current["metadata"].get("labels", {}).get(k) == v for k, v in LABELS.items()),
            "Secret differs; automatic credential rotation refused",
        )
    helm("upgrade", "--install", RELEASE, CHART, "--wait", "--timeout", "5m", timeout=360)
    result = inventory()
    return {
        "status": "foundation_ready",
        **result,
        "historical_sources_mounted": False,
        "primary_storage_migrated": False,
    }


def inventory():
    result = {"pods": {}, "pvcs": {}}
    for service in COMPONENTS:
        name = RELEASE + "-" + service
        sts = json.loads(kube("get", "statefulset", name, "-o", "json").stdout)
        require(
            sts["metadata"].get("labels", {}).get("app.kubernetes.io/instance") == RELEASE,
            "Unexpected StatefulSet owner",
        )
        pod = json.loads(kube("get", "pod", name + "-0", "-o", "json").stdout)
        require(
            any(
                r["uid"] == sts["metadata"]["uid"] and r.get("controller")
                for r in pod["metadata"].get("ownerReferences", [])
            ),
            "Unexpected pod controller",
        )
        require(
            any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in pod["status"].get("conditions", [])
            ),
            "Pod is not ready: " + service,
        )
        require(
            pod["spec"]["containers"][0]["image"] == images()[service], "Unexpected service image"
        )
        pvc = json.loads(kube("get", "pvc", name + "-data", "-o", "json").stdout)
        require(pvc["status"]["phase"] == "Bound", "PVC not bound: " + service)
        result["pods"][service] = {
            "name": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"],
            "image": images()[service],
            "image_id": pod["status"]["containerStatuses"][0]["imageID"],
        }
        result["pvcs"][service] = {
            "name": pvc["metadata"]["name"],
            "uid": pvc["metadata"]["uid"],
            "volume_name": pvc["spec"]["volumeName"],
        }
    return result


def status():
    """Show service state even when readiness fails, without credentials or pod env."""
    owner()
    items = json.loads(kube("get", "pods,pvc,svc", "-o", "json").stdout)["items"]
    return {
        "status": "inspected",
        "resources": [
            {
                "kind": item["kind"],
                "name": item["metadata"]["name"],
                "phase": item.get("status", {}).get("phase"),
                "conditions": item.get("status", {}).get("conditions", []),
                "container_states": [
                    value.get("state", {})
                    for value in item.get("status", {}).get("containerStatuses", [])
                ],
            }
            for item in items
        ],
    }


def postgres_probe(identity, *, write):
    require(re.fullmatch(r"[a-f0-9]{32}", identity), "Invalid probe identity")
    sql = (
        (
            "CREATE TABLE IF NOT EXISTS public.platform_probe (probe_id text PRIMARY KEY); "
            f"INSERT INTO public.platform_probe VALUES ('{identity}'); "
        )
        if write
        else ""
    )
    sql += f"SELECT probe_id FROM public.platform_probe WHERE probe_id='{identity}';"
    script = 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"'
    output = kube(
        "exec", "statefulset/" + RELEASE + "-postgres", "--", "sh", "-ec", script, "probe", sql
    ).stdout
    require(identity in output.splitlines(), "PostgreSQL marker readback differs")


def probe_pod(identity, *, write):
    require(re.fullmatch(r"[a-f0-9]{32}", identity), "Invalid probe identity")
    name = "foundation-probe-" + uuid4().hex
    script = (
        "set -eu; printf '[default]\\ns3 =\\n    addressing_style = path\\n' "
        '> "$AWS_CONFIG_FILE"; endpoint="http://fraud-platform-rustfs:9000"; '
    )
    if write:
        script += 'aws --cli-connect-timeout 5 --cli-read-timeout 15 --endpoint-url "$endpoint" s3api create-bucket --bucket "$PROBE_BUCKET" >/dev/null; printf %s "$PROBE_ID" > /tmp/probe.txt; aws --cli-connect-timeout 5 --cli-read-timeout 15 --endpoint-url "$endpoint" s3api put-object --bucket "$PROBE_BUCKET" --key probe.txt --body /tmp/probe.txt >/dev/null; '
    script += 'aws --cli-connect-timeout 5 --cli-read-timeout 15 --endpoint-url "$endpoint" s3api get-object --bucket "$PROBE_BUCKET" --key probe.txt /tmp/readback >/dev/null; test "$(cat /tmp/readback)" = "$PROBE_ID"; printf \'{"verified":true}\\n\''
    env = [
        {"name": key, "value": value}
        for key, value in {
            "PROBE_BUCKET": "fraud-kind-probe-" + identity,
            "PROBE_ID": identity,
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": "/tmp/aws-config",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_MAX_ATTEMPTS": "2",
        }.items()
    ]
    env += [
        {"name": name, "valueFrom": {"secretKeyRef": {"name": SECRET, "key": key}}}
        for name, key in [
            ("AWS_ACCESS_KEY_ID", "rustfs-access-key"),
            ("AWS_SECRET_ACCESS_KEY", "rustfs-secret-key"),
        ]
    ]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": LABELS},
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 180,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": images()["s3_probe"],
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sh", "-ec", script],
                    "workingDir": "/tmp",
                    "env": env,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }
            ],
            "volumes": [{"name": "tmp", "emptyDir": {}}],
        },
    }


def s3_probe(identity, *, write):
    pod = probe_pod(identity, write=write)
    name = pod["metadata"]["name"]
    created = False
    try:
        kube("create", "-f", "-", data=json.dumps(pod))
        created = True
        kube(
            "wait",
            "--for=jsonpath={.status.phase}=Succeeded",
            "pod/" + name,
            "--timeout=240s",
            timeout=255,
        )
        require(
            json.loads(kube("logs", name).stdout) == {"verified": True},
            "S3 marker readback differs",
        )
    finally:
        if created:
            kube("delete", "pod", name, "--wait=false", check=False)


def rehearse():
    owner()
    identity = uuid4().hex
    receipt = workspace() / ("foundation-" + identity + ".json")
    result = {
        "schema": "fraud_kind_foundation_rehearsal_v1",
        "status": "failed",
        "scope": "isolated_new_SQL_and_S3_markers_across_pod_recreation_not_backup_restore_or_migration",
        "probe_id": identity,
        "historical_sources_mounted": False,
        "primary_storage_migrated": False,
        "backup_restore_validated": False,
        "cluster_recreation_validated": False,
        "host_loss_recovery_validated": False,
        "production_promotion": False,
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "phase": "initial_inventory",
    }
    try:
        result["before"] = inventory()
        result["phase"] = "write_markers"
        postgres_probe(identity, write=True)
        s3_probe(identity, write=True)
        result["phase"] = "recreate_owned_pods"
        for service in COMPONENTS:
            kube(
                "delete",
                "pod",
                result["before"]["pods"][service]["name"],
                "--wait=true",
                timeout=100,
            )
            kube(
                "rollout",
                "status",
                "statefulset/" + RELEASE + "-" + service,
                "--timeout=180s",
                timeout=195,
            )
            kube(
                "wait",
                "--for=condition=Ready",
                "pod/" + RELEASE + "-" + service + "-0",
                "--timeout=180s",
                timeout=195,
            )
        result["after"] = inventory()
        require(result["before"]["pvcs"] == result["after"]["pvcs"], "PVC or PV identity changed")
        require(
            all(
                result["before"]["pods"][s]["uid"] != result["after"]["pods"][s]["uid"]
                for s in COMPONENTS
            ),
            "Both pods must be recreated",
        )
        result["phase"] = "read_markers"
        postgres_probe(identity, write=False)
        s3_probe(identity, write=False)
        result.update(
            status="success",
            phase="complete",
            sql_readback_verified=True,
            s3_readback_verified=True,
            pod_recreation_validated=True,
            pvc_identity_preserved=True,
        )
    finally:
        private_json(receipt, result)
        print("Relatório: " + str(receipt), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["init", "validate", "create", "up", "status", "rehearse"]
    )
    args = parser.parse_args()
    try:
        if args.action in ("init", "validate"):
            result = initialize() if args.action == "init" else validate()
        else:
            with exclusive():
                if args.action == "create":
                    result = create()
                elif args.action == "up":
                    result = deploy()
                elif args.action == "rehearse":
                    result = rehearse()
                else:
                    result = status()
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, "Foundation stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
