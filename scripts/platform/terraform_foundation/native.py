"""Own namespace SQL/S3 marker readbacks; pod recreation is explicit opt-in."""

import json
import re
import time
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform.terraform_foundation import controller as c
from scripts.platform.terraform_foundation import policy as p


def inventory():
    rows = {}
    for role in ("postgres", "rustfs"):
        name = p.RELEASE + "-" + role
        c.kube("rollout", "status", "statefulset/" + name, "--timeout=600s")
        sts = json.loads(c.kube("get", "statefulset", name, "-o", "json").stdout)
        pvc = json.loads(c.kube("get", "pvc", name + "-data", "-o", "json").stdout)
        pod = json.loads(c.kube("get", "pod", name + "-0", "-o", "json").stdout)
        p.require(pvc["status"]["phase"] == "Bound", "PVC not Bound")
        image = pod["spec"]["containers"][0]["image"]
        p.require(image == f.images()[role], "Image differs from the pinned foundation lock")
        for resource in (sts, pvc):
            annotations = resource["metadata"].get("annotations", {})
            p.require(
                annotations.get("meta.helm.sh/release-name") == p.RELEASE
                and annotations.get("meta.helm.sh/release-namespace") == p.NAMESPACE,
                "Child resource Helm owner differs",
            )
        p.require(
            image == sts["spec"]["template"]["spec"]["containers"][0]["image"], "Image differs"
        )
        p.require(
            "@sha256:" in image and pod["status"]["containerStatuses"][0]["ready"], "Pod not ready"
        )
        rows[role] = {
            "statefulset_uid": sts["metadata"]["uid"],
            "pvc_uid": pvc["metadata"]["uid"],
            "volume": pvc["spec"]["volumeName"],
            "pod_uid": pod["metadata"]["uid"],
            "image": image,
            "image_id": pod["status"]["containerStatuses"][0]["imageID"],
        }
    return rows


def sql(identity, *, write):
    p.require(bool(re.fullmatch(r"[a-f0-9]{32}", identity)), "Invalid SQL marker ID")
    query = ""
    if write:
        query = (
            "CREATE TABLE IF NOT EXISTS public.iac_probe (probe_id text PRIMARY KEY); "
            f"INSERT INTO public.iac_probe VALUES ('{identity}') ON CONFLICT DO NOTHING; "
        )
    query += f"SELECT probe_id FROM public.iac_probe WHERE probe_id='{identity}';"
    script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -A -t '
        '-v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"'
    )
    done = c.kube(
        "exec", "statefulset/" + p.RELEASE + "-postgres", "--", "sh", "-ec", script, "probe", query
    )
    p.require(identity in done.stdout.splitlines(), "SQL marker differs")


def s3(identity, *, write):
    # Reuse the reviewed hardened probe image/spec without changing global foundation names.
    pod = f.probe_pod(identity, write=write)
    pod["metadata"]["name"] = "iac-probe-" + uuid4().hex
    pod["metadata"]["labels"] = {"fraud.iac/owner-id": identity}
    container = pod["spec"]["containers"][0]
    container["command"][-1] = container["command"][-1].replace(
        "http://fraud-platform-rustfs:9000", "http://" + p.RELEASE + "-rustfs:9000"
    )
    for entry in container["env"]:
        if "valueFrom" in entry:
            entry["valueFrom"]["secretKeyRef"]["name"] = p.SECRET
    if write:
        create = 'aws --cli-connect-timeout 5 --cli-read-timeout 15 --endpoint-url "$endpoint" s3api create-bucket --bucket "$PROBE_BUCKET" >/dev/null;'
        head = 'aws --cli-connect-timeout 5 --cli-read-timeout 15 --endpoint-url "$endpoint" s3api head-bucket --bucket "$PROBE_BUCKET" >/dev/null 2>&1'
        p.require(create in container["command"][-1], "Probe producer contract changed")
        container["command"][-1] = container["command"][-1].replace(
            create, "if ! " + head + "; then " + create + " fi;"
        )
    name = pod["metadata"]["name"]
    created = False
    try:
        c.kube("create", "-f", "-", data=json.dumps(pod))
        created = True
        c.kube("wait", "--for=jsonpath={.status.phase}=Succeeded", "pod/" + name, "--timeout=240s")
        p.require(
            json.loads(c.kube("logs", name).stdout) == {"verified": True}, "S3 marker differs"
        )
    finally:
        if created:
            c.kube("delete", "pod", name, "--wait=true", "--timeout=60s")


def stable(inventory_value):
    return {
        k: {name: v for name, v in row.items() if name not in {"pod_uid", "image_id"}}
        for k, row in inventory_value.items()
    }


def verify(work, item, *, recreate):
    state = c.state_resources(work)
    p.require(set(state) == p.ADDRESSES, "All three Terraform resources required")
    before = inventory()
    identity = item["owner_id"]
    marker = work / "marker.json"
    if recreate:
        if not marker.exists():
            p.private(marker, {"owner_id": identity, "status": "intent_before_marker_writes"})
        p.require(p.read(marker)["owner_id"] == identity, "Marker ownership differs")
        sql(identity, write=True)
        # Bucket creation is idempotent for the same owned account, key and marker.
        s3(identity, write=True)
        for role in ("postgres", "rustfs"):
            name = p.RELEASE + "-" + role + "-0"
            pod = json.loads(c.kube("get", "pod", name, "-o", "json").stdout)
            p.require(
                pod["metadata"]["uid"] == before[role]["pod_uid"], "Pod changed before deletion"
            )
            # Delete with UID precondition, never a broad selector or the accepted namespace.
            c.kube(
                "delete",
                "--raw",
                "/api/v1/namespaces/" + p.NAMESPACE + "/pods/" + name,
                "-f",
                "-",
                data=json.dumps(
                    {
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "preconditions": {"uid": pod["metadata"]["uid"]},
                    }
                ),
            )
            deadline = time.monotonic() + 600
            while True:
                current = json.loads(
                    c.kube("get", "pod", name, "--ignore-not-found", "-o", "json").stdout or "{}"
                )
                ready = current.get("status", {}).get("containerStatuses", [])
                if (
                    current.get("metadata", {}).get("uid") not in (None, pod["metadata"]["uid"])
                    and ready
                    and all(x["ready"] for x in ready)
                ):
                    break
                p.require(time.monotonic() < deadline, "Replacement pod did not become ready")
                time.sleep(2)
    else:
        p.require(
            marker.exists() and p.read(marker)["owner_id"] == identity,
            "Run explicit marker/pod rehearsal first",
        )
    after = inventory()
    p.require(stable(before) == stable(after), "Owned data resources changed")
    if recreate:
        p.require(
            all(before[k]["pod_uid"] != after[k]["pod_uid"] for k in before), "Pods not recreated"
        )
    sql(identity, write=False)
    s3(identity, write=False)
    planned = c.plan(work)
    p.require(planned["counts"] == {"create": 0, "no-op": 3}, "Second plan not empty")
    p.require(
        c.accepted(item["session_id"]) == item["accepted_before"], "Accepted session changed"
    )
    result = {
        "schema": "fraud_local_iac_native_v1",
        "status": "local_iac_foundation_verified",
        "namespace": p.NAMESPACE,
        "owner_id": identity,
        "pod_recreation_validated": recreate,
        "sql_readback_verified": True,
        "s3_readback_verified": True,
        "pvc_identities_preserved": True,
        "second_plan_no_changes": True,
        "accepted_session_preserved": True,
        "fit_executed": False,
        "new_session_inferences": False,
        "cloud_validated": False,
        "backup_restore_validated": False,
        "before": before,
        "after": after,
        "noop_plan_sha256": planned["plan_sha256"],
    }
    p.private(work / ("verification-" + uuid4().hex + ".json"), result)
    return result
