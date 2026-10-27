"""Operate MLflow on the owned KinD foundation without changing historical routing."""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import source_compatibility

RELEASE = "fraud-tracking"
SECRET = "fraud-kind-tracking-credentials"
DATABASE = ROLE = "mlflow_kind"
SCOPE = "synthetic_kind_MLflow_PostgreSQL_S3_proxy_and_model_reload_not_historical_migration"
OWNER_ANNOTATION = "fraud.tracking/owner-id"


def work():
    return f.managed_destination(f.workspace() / "tracking", "storage")


def chart():
    return f.ROOT / "infra/helm/fraud-tracking"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins():
    names = [
        "infra/kind/tracking/Dockerfile",
        "infra/kind/tracking/Dockerfile.dockerignore",
        "infra/tracking/requirements.txt",
        "scripts/platform/kind_tracking_storage.py",
    ]
    names += [p.relative_to(f.ROOT).as_posix() for p in sorted(chart().rglob("*")) if p.is_file()]
    return {name: sha(f.ROOT / name) for name in names}


def credentials():
    path = work() / "credentials.json"
    f.managed_destination(path, "storage")
    f.require(path.stat().st_mode & 0o077 == 0, "Tracking credentials must have mode 600")
    value = json.loads(path.read_text())
    f.require(set(value) == {"owner_id", "password"}, "Unexpected tracking credential keys")
    f.require(bool(re.fullmatch(r"[a-f0-9]{32}", value["owner_id"])), "Invalid tracking owner")
    f.require(bool(re.fullmatch(r"[a-f0-9]{48}", value["password"])), "Invalid tracking password")
    return value


def initialize():
    f.owner()
    if not work().exists():
        work().mkdir(mode=0o700)
    f.require(work().stat().st_mode & 0o077 == 0, "Tracking workspace must be private")
    path = work() / "credentials.json"
    if not path.exists():
        f.require(not any(work().iterdir()), "Incomplete tracking workspace; preserve for review")
        f.private_json(path, {"owner_id": uuid4().hex, "password": secrets.token_hex(24)})
    credentials()
    return {"status": "tracking_initialized", "workspace": str(work())}


def build():
    owner = f.owner()
    initialize()
    path = work() / "image.json"
    if path.exists():
        image_record()
        return {"status": "image_already_loaded", "record": str(path)}
    tag = "fraud-kind-tracking:" + uuid4().hex
    print("Building the locked tracking runtime...", flush=True)
    f.run(
        ["docker", "build", "--file", "infra/kind/tracking/Dockerfile", "--tag", tag, "."],
        timeout=900,
    )
    image = json.loads(f.run(["docker", "image", "inspect", tag]).stdout)[0]
    f.run(["kind", "load", "docker-image", "--name", f.CLUSTER, tag], timeout=300)
    loaded = json.loads(
        f.run(
            [
                "docker",
                "exec",
                f.CLUSTER + "-control-plane",
                "crictl",
                "--runtime-endpoint",
                "unix:///run/containerd/containerd.sock",
                "inspecti",
                tag,
            ]
        ).stdout
    )["status"]
    f.require(
        tag in {t.removeprefix("docker.io/library/") for t in loaded.get("repoTags", [])},
        "Loaded image tag differs",
    )
    record = {
        "schema": "fraud_kind_tracking_image_v1",
        "image": tag,
        "docker_image_id": image["Id"],
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        "source_pins": pins(),
        "node_uid": owner["node_uid"],
        "registry_published": False,
    }
    f.private_json(path, record)
    return {"status": "image_built_and_loaded", "image": tag, "record": str(path)}


def image_record():
    value = json.loads(f.managed_destination(work() / "image.json", "storage").read_text())
    f.require(value.get("schema") == "fraud_kind_tracking_image_v1", "Invalid image receipt")
    try:
        source_compatibility.accept(value["source_pins"], pins(), f.ROOT)
    except ValueError as exc:
        raise ValueError(
            "Image sources changed; preserve the old image receipt for review"
        ) from exc
    f.require(value["node_uid"] == f.owner()["node_uid"], "Image belongs to another node")
    f.require(
        bool(re.fullmatch(r"fraud-kind-tracking:[a-f0-9]{32}", value["image"])),
        "Invalid local image tag",
    )
    f.require(bool(value["loaded_identities"]), "Missing loaded image identities")
    return value


def labels():
    return {"app.kubernetes.io/part-of": "fraud-platform", "app.kubernetes.io/instance": RELEASE}


def ensure_secret():
    own = credentials()
    foundation = f.credentials()
    content = {
        "backend-uri": f"postgresql+psycopg://{ROLE}:{own['password']}@fraud-platform-postgres:5432/{DATABASE}",
        "rustfs-access-key": foundation["rustfs-access-key"],
        "rustfs-secret-key": foundation["rustfs-secret-key"],
    }
    encoded = {key: base64.b64encode(v.encode()).decode() for key, v in content.items()}
    existing = json.loads(
        f.kube("get", "secret", SECRET, "--ignore-not-found", "-o", "json").stdout or "null"
    )
    if existing is not None:
        f.require(
            existing["metadata"].get("annotations", {}).get(OWNER_ANNOTATION) == own["owner_id"]
            and existing.get("data") == encoded,
            "Tracking Secret differs; do not adopt or rotate automatically",
        )
        return
    f.kube(
        "create",
        "-f",
        "-",
        data=json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": SECRET,
                    "labels": labels(),
                    "annotations": {OWNER_ANNOTATION: own["owner_id"]},
                },
                "type": "Opaque",
                "data": encoded,
            }
        ),
    )


def sql(text):
    script = 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    return f.kube(
        "exec",
        "-i",
        "statefulset/fraud-platform-postgres",
        "--",
        "sh",
        "-ec",
        script,
        data=text,
        timeout=60,
    ).stdout.strip()


def prepare_database():
    value = credentials()
    marker = "fraud-kind-tracking:" + value["owner_id"]
    existing = json.loads(
        sql("""
SELECT json_build_object(
 'role', (SELECT json_build_object('comment', shobj_description(oid,'pg_authid'),
 'superuser',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,
 'bypassrls',rolbypassrls,'login',rolcanlogin) FROM pg_roles WHERE rolname='mlflow_kind'),
 'database', (SELECT json_build_object('comment',shobj_description(oid,'pg_database'),
 'owner',pg_get_userbyid(datdba)) FROM pg_database WHERE datname='mlflow_kind'));
""")
    )
    role = existing["role"]
    if role:
        f.require(
            role["comment"] == marker
            and role["login"]
            and not any(
                role[k]
                for k in ("superuser", "createdb", "createrole", "replication", "bypassrls")
            ),
            "Existing role is not the restricted tracking role; preserve it",
        )
    else:
        f.require(existing["database"] is None, "Existing database without owned role")
        sql(
            f"BEGIN; CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{value['password']}'; COMMENT ON ROLE {ROLE} IS '{marker}'; COMMIT;\n"
        )
    db = existing["database"]
    if db:
        f.require(
            db == {"comment": marker, "owner": ROLE},
            "Existing database is not owned by this tracking flow",
        )
    else:
        # Separate psql input lines keep CREATE DATABASE outside transaction blocks.
        sql(
            f"CREATE DATABASE {DATABASE} OWNER {ROLE};\nCOMMENT ON DATABASE {DATABASE} IS '{marker}';\nREVOKE ALL ON DATABASE {DATABASE} FROM PUBLIC;\n"
        )


def storage_pod(action, record=None):
    f.require(action in {"prepare", "verify"}, "Invalid storage action")
    own = credentials()
    container = {
        "name": "storage",
        "image": image_record()["image"],
        "imagePullPolicy": "Never",
        "command": ["python", "/app/kind_tracking_storage.py", action],
        "env": [
            {"name": "TRACKING_OWNER_ID", "value": own["owner_id"]},
            {"name": "AWS_DEFAULT_REGION", "value": "us-east-1"},
            {"name": "AWS_EC2_METADATA_DISABLED", "value": "true"},
            {"name": "AWS_CONFIG_FILE", "value": "/dev/null"},
            {"name": "AWS_SHARED_CREDENTIALS_FILE", "value": "/dev/null"},
        ],
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
    if record is not None:
        container["command"].append(
            json.dumps({k: record[k] for k in ["model_artifact_uri", "model_sha256"]})
        )
    for name, key in [
        ("AWS_ACCESS_KEY_ID", "rustfs-access-key"),
        ("AWS_SECRET_ACCESS_KEY", "rustfs-secret-key"),
    ]:
        container["env"].append(
            {"name": name, "valueFrom": {"secretKeyRef": {"name": SECRET, "key": key}}}
        )
    name = "kind-tracking-storage-" + uuid4().hex
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": labels()},
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 120,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "fsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
        },
    }
    created = False
    try:
        f.kube("create", "-f", "-", data=json.dumps(pod))
        created = True
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            observed = json.loads(f.kube("get", "pod", name, "-o", "json").stdout)
            phase = observed.get("status", {}).get("phase")
            if phase in {"Succeeded", "Failed"}:
                raw = f.kube("logs", name, check=False).stdout
                result = json.loads(raw or "{}")
                f.require(
                    phase == "Succeeded"
                    and result.get("status") in {"bucket_owned", "s3_model_verified"},
                    "Storage worker failed: " + str(result.get("error_type", phase)),
                )
                return result
            time.sleep(1)
        raise ValueError("Storage worker deadline exceeded; inspect namespace events")
    finally:
        if created:
            f.kube("delete", "pod", name, "--wait=false", check=False)


def validate():
    f.run(["helm", "lint", "--strict", chart(), "--kube-version", "1.35.8"])
    f.run(
        [
            "helm",
            "template",
            RELEASE,
            chart(),
            "--namespace",
            f.NAMESPACE,
            "--kube-version",
            "1.35.8",
        ]
    )
    return {"status": "tracking_templates_validated", "native_cluster_validated": False}


def up():
    f.owner()
    before = f.inventory()
    own = credentials()
    image = image_record()
    validate()
    for kind in ("deployment", "service"):
        existing = json.loads(
            f.kube("get", kind, RELEASE, "--ignore-not-found", "-o", "json").stdout or "null"
        )
        if existing:
            f.require(
                existing["metadata"].get("annotations", {}).get(OWNER_ANNOTATION)
                == own["owner_id"],
                "Existing tracking resource belongs to another flow",
            )
    ensure_secret()
    prepare_database()
    storage_pod("prepare")
    values = {"image": image["image"], "ownerId": own["owner_id"]}
    # Values contain identities only; credentials live in the separately owned Secret.
    name = work() / ("values-" + uuid4().hex + ".json")
    f.private_json(name, values)
    f.helm(
        "upgrade",
        "--install",
        RELEASE,
        chart(),
        "--values",
        name,
        "--wait",
        "--timeout",
        "5m",
        timeout=360,
    )
    result = inventory()
    f.require(
        before == f.inventory(), "Foundation identities changed during tracking installation"
    )
    return {"status": "kind_tracking_ready", **result, "primary_storage_migrated": False}


def image_digest(value):
    match = re.search(r"sha256:[a-f0-9]{64}$", value)
    f.require(match is not None, "Image identity has no SHA-256")
    return match.group()


def inventory():
    own = credentials()
    image = image_record()
    dep = json.loads(f.kube("get", "deployment", RELEASE, "-o", "json").stdout)
    f.require(
        dep["metadata"].get("annotations", {}).get(OWNER_ANNOTATION) == own["owner_id"],
        "Deployment owner differs",
    )
    pods = json.loads(
        f.kube(
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/instance=" + RELEASE + ",app.kubernetes.io/component=mlflow",
            "-o",
            "json",
        ).stdout
    )["items"]
    f.require(len(pods) == 1, "Expected one MLflow pod; wait for rollout or inspect status")
    pod = pods[0]
    refs = pod["metadata"].get("ownerReferences", [])
    f.require(
        len(refs) == 1 and refs[0].get("controller") and refs[0]["kind"] == "ReplicaSet",
        "Pod controller differs",
    )
    rs = json.loads(f.kube("get", "replicaset", refs[0]["name"], "-o", "json").stdout)
    f.require(
        rs["metadata"]["uid"] == refs[0]["uid"]
        and any(
            r.get("controller") and r["uid"] == dep["metadata"]["uid"]
            for r in rs["metadata"].get("ownerReferences", [])
        ),
        "ReplicaSet owner differs",
    )
    f.require(
        pod["metadata"].get("annotations", {}).get(OWNER_ANNOTATION) == own["owner_id"],
        "Pod owner differs",
    )
    f.require(
        any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in pod.get("status", {}).get("conditions", [])
        ),
        "MLflow pod is not ready",
    )
    f.require(pod["spec"]["containers"][0]["image"] == image["image"], "Pod image tag differs")
    image_id = pod["status"]["containerStatuses"][0]["imageID"]
    f.require(
        image_digest(image_id) in {image_digest(v) for v in image["loaded_identities"]},
        "Pod image identity differs from loaded image",
    )
    return {
        "pod": pod["metadata"]["name"],
        "pod_uid": pod["metadata"]["uid"],
        "deployment_uid": dep["metadata"]["uid"],
        "image": image["image"],
        "image_id": image_id,
    }


@contextmanager
def port_forward():
    args = [
        "kubectl",
        "--kubeconfig",
        str(f.workspace() / "kubeconfig"),
        "--cache-dir",
        str(f.workspace() / "kube-cache"),
        "--context",
        f.CONTEXT,
        "--namespace",
        f.NAMESPACE,
        "port-forward",
        "--address",
        "127.0.0.1",
        "service/" + RELEASE,
        ":5000",
    ]
    with subprocess.Popen(
        args, cwd=f.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 30
                buffer = b""
                uri = None
                while time.monotonic() < deadline and process.poll() is None:
                    for key, _ in selector.select(timeout=0.5):
                        buffer = (buffer + os.read(key.fileobj.fileno(), 4096))[-8192:]
                        match = re.search(
                            rb"Forwarding from 127\.0\.0\.1:([0-9]+) -> 5000", buffer
                        )
                        if match:
                            uri = "http://127.0.0.1:" + match[1].decode()
                            break
                    if uri:
                        break
                f.require(
                    uri is not None, "Loopback port-forward failed; inspect tracking pod/service"
                )
                yield uri
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def client_environment(directory):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AWS_", "PG", "MLFLOW_"))
        and k.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "PYTHONPATH"}
    }
    env.update(
        HOME=str(directory),
        TMPDIR=str(directory),
        XDG_CACHE_HOME=str(directory / "cache"),
        PYTHONPATH=str(f.ROOT),
        MLFLOW_DISABLE_TELEMETRY="true",
        MLFLOW_DISABLE_AGENT_HINT="1",
    )
    return env


def client(action, uri, record=None):
    with TemporaryDirectory(prefix="client-", dir=work()) as temporary:
        directory = Path(temporary)
        # Execute an exact source copy outside the Git tree. MLflow source-context
        # discovery stays confined to this disposable client, with explicit source pins in the receipt.
        script = directory / "client.py"
        script.write_bytes((f.ROOT / "scripts/platform/kind_tracking_client.py").read_bytes())
        script.chmod(0o600)
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=directory,
            env=client_environment(directory),
            input=json.dumps({"action": action, "uri": uri, "record": record}),
            text=True,
            capture_output=True,
            timeout=150,
            check=False,
        )
        try:
            result = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Tracking client did not return strict JSON") from exc
        f.require(
            completed.returncode == 0 and result.get("status") == "success",
            "Tracking client failed: "
            + str(result.get("error_type", "unknown"))
            + ": "
            + str(result.get("reason", "inspect tracking status")),
        )
        return result


def rehearse():
    f.owner()
    identity = uuid4().hex
    path = work() / ("rehearsal-" + identity + ".json")
    result = {
        "schema": "fraud_kind_tracking_rehearsal_v1",
        "status": "failed",
        "scope": SCOPE,
        "report": str(path),
        "phase": "inventory",
        "historical_sources_mounted": False,
        "historical_runs_migrated": False,
        "primary_storage_migrated": False,
        "production_promotion": False,
        "backup_restore_validated": False,
        "controller_sha256": sha(Path(__file__)),
        "client_sha256": sha(f.ROOT / "scripts/platform/kind_tracking_client.py"),
    }
    try:
        result["foundation_before"] = f.inventory()
        result["before"] = inventory()
        result["phase"] = "publish_and_independent_reload"
        with port_forward() as uri:
            record = client("write", uri)
            independent = client("read", uri, record)
        result["phase"] = "direct_s3_readback"
        result["s3"] = storage_pod("verify", record)
        result["phase"] = "recreate_mlflow_pod"
        # UID precondition protects a concurrently replaced pod with the same name.
        delete = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": result["before"]["pod_uid"]},
        }
        f.kube(
            "delete",
            "--raw",
            f"/api/v1/namespaces/{f.NAMESPACE}/pods/{result['before']['pod']}",
            "-f",
            "-",
            data=json.dumps(delete),
            timeout=60,
        )
        f.kube("rollout", "status", "deployment/" + RELEASE, "--timeout=180s", timeout=195)
        # Wait for a replacement UID and readiness, rather than accepting the old rollout status.
        deadline = time.monotonic() + 180
        while True:
            try:
                after = inventory()
                f.require(after["pod_uid"] != result["before"]["pod_uid"], "Old pod still present")
                break
            except ValueError:
                f.require(
                    time.monotonic() < deadline, "Replacement MLflow pod did not become ready"
                )
                time.sleep(1)
        result["after"] = after
        result["phase"] = "reload_after_recreation"
        with port_forward() as uri:
            recovered = client("read", uri, record)
        result["foundation_after"] = f.inventory()
        f.require(
            result["foundation_before"] == result["foundation_after"],
            "Foundation changed during MLflow rehearsal",
        )
        f.require(
            result["before"]["deployment_uid"] == after["deployment_uid"],
            "Deployment identity changed",
        )
        result.update(
            status="success",
            phase="complete",
            run_id=record["run_id"],
            model_uri=record["model_uri"],
            model_artifact_uri=record["model_artifact_uri"],
            model_sha256=record["model_sha256"],
            independent_http_reload_validated=True,
            model_reload_after_pod_recreation_validated=True,
            metric_history_rows=recovered["metric_history_rows"],
            max_absolute_score_error=max(
                independent["max_absolute_score_error"], recovered["max_absolute_score_error"]
            ),
            client_aws_and_database_environment_removed=True,
            client_workspaces_removed=True,
            synthetic_fixture_fitted=True,
            historical_model_refit=False,
            foundation_identities_preserved=True,
        )
        return result
    except Exception as exc:
        # Errors may contain runtime connection details; record only the type and phase.
        result["error_type"] = type(exc).__name__
        raise
    finally:
        f.private_json(path, result)
        print("Relatório: " + str(path), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("validate", "init", "build", "up", "status", "rehearse")
    )
    args = parser.parse_args()
    try:
        with f.exclusive():
            if args.action == "status":
                f.owner()
                result = f.status()
            else:
                result = {
                    "validate": validate,
                    "init": initialize,
                    "build": build,
                    "up": up,
                    "rehearse": rehearse,
                }[args.action]()
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        print("Kind tracking stopped: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
