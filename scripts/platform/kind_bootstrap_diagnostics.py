"""Retain a failed dedicated KinD node and collect bounded bootstrap diagnostics."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

COMPONENTS = {"kube-apiserver", "etcd", "kube-controller-manager", "kube-scheduler"}
LIMIT = 24000


def redact(text, values):
    text = str(text)
    for value in values:
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[PRIVATE KEY REDACTED]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"(?i)(https?://)[^\s/@]+@", r"\1[REDACTED]@", text)
    return text[-LIMIT:]


def host_summary(root):
    result = {}
    for name in ("max_user_instances", "max_user_watches", "max_queued_events"):
        path = Path("/proc/sys/fs/inotify") / name
        try:
            result["fs.inotify." + name] = int(path.read_text().strip())
        except OSError, ValueError:
            result["fs.inotify." + name] = None
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                fields[key + "_kib"] = int(value.split()[0])
        result["memory"] = fields
    except OSError, ValueError:
        result["memory"] = None
    try:
        usage = shutil.disk_usage(root)
        result["checkout_filesystem_bytes"] = {"total": usage.total, "free": usage.free}
    except OSError:
        result["checkout_filesystem_bytes"] = None
    result["disk_scope"] = "checkout filesystem; not proof of Docker filesystem capacity"
    return result


def capture(run, args, values, *, timeout=30):
    try:
        completed = run(args, check=False, timeout=timeout)
        return {
            "exit_code": completed.returncode,
            "stdout": redact(completed.stdout, values),
            "stderr": redact(completed.stderr, values),
        }
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"error": redact(type(exc).__name__, values)}


def inspect_node(foundation, run, values):
    """Only read the exact dedicated node after verifying KinD labels."""
    node = foundation.CLUSTER + "-control-plane"
    raw = capture(run, ["docker", "inspect", node], values)
    if raw.get("exit_code") != 0:
        return {"retained_node_present": False, "inspection": raw}
    # Parse the complete result for owner validation; never retain Config.Env.
    try:
        completed = run(["docker", "inspect", node], check=False, timeout=20)
        rows = json.loads(completed.stdout)
        if len(rows) != 1:
            raise ValueError("Expected one node")
        info = rows[0]
    except OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired:
        return {"retained_node_present": True, "error": "Invalid node inspection"}
    labels = info.get("Config", {}).get("Labels", {})
    if (
        labels.get("io.x-k8s.kind.cluster") != foundation.CLUSTER
        or labels.get("io.x-k8s.kind.role") != "control-plane"
    ):
        return {
            "retained_node_present": True,
            "error": "Owner labels differ; no node exec performed",
        }
    result = {
        "retained_node_present": True,
        "node_name": node,
        "container_id": info["Id"],
        "state": {
            key: info.get("State", {}).get(key)
            for key in ("Status", "Running", "ExitCode", "OOMKilled", "Error")
        },
        "limits": {key: info.get("HostConfig", {}).get(key) for key in ("Memory", "PidsLimit")},
        "kubelet": capture(
            run,
            [
                "docker",
                "exec",
                node,
                "journalctl",
                "-u",
                "kubelet",
                "--no-pager",
                "-n",
                "160",
                "-o",
                "short-iso",
            ],
            values,
        ),
        "containerd": capture(
            run,
            [
                "docker",
                "exec",
                node,
                "journalctl",
                "-u",
                "containerd",
                "--no-pager",
                "-n",
                "80",
                "-o",
                "short-iso",
            ],
            values,
        ),
        "components": [],
    }
    runtime = [
        "docker",
        "exec",
        node,
        "crictl",
        "--runtime-endpoint",
        "unix:///run/containerd/containerd.sock",
    ]
    listing = capture(run, [*runtime, "ps", "-a", "-o", "json"], values)
    if listing.get("exit_code") != 0:
        result["container_listing"] = listing
        return result
    try:
        containers = json.loads(listing["stdout"])["containers"]
    except ValueError, KeyError, TypeError:
        result["container_listing_error"] = "Cannot parse bounded CRI container list"
        return result
    counts = dict.fromkeys(COMPONENTS, 0)
    for item in containers:
        name = item.get("metadata", {}).get("name")
        identity = item.get("id", "")
        if (
            not isinstance(name, str)
            or name not in COMPONENTS
            or counts[name] >= 2
            or not isinstance(identity, str)
            or not re.fullmatch(r"[a-f0-9]{12,64}", identity)
        ):
            continue
        counts[name] += 1
        result["components"].append(
            {
                "name": name,
                "id": identity,
                "state": item.get("state"),
                "logs": capture(run, [*runtime, "logs", "--tail=80", identity], values),
            }
        )
    return result


def diagnose(foundation, action):
    foundation.require(
        foundation.CLUSTER == "fraud-platform", "Dedicated fraud-platform cluster required"
    )
    credentials = list(foundation.credentials().values())
    original = foundation.run
    result = {
        "schema": "fraud_kind_bootstrap_diagnostics_v1",
        "status": "diagnostic_only",
        "action": action,
        "scope": "dedicated_kind_bootstrap_not_service_deployment_or_adoption",
        "host": host_summary(foundation.ROOT),
        "kind_failure": None,
        "ownership_checks_bypassed": False,
        "system_settings_changed": False,
        "clusters_deleted": False,
        "native_foundation_validated": False,
    }
    report = foundation.workspace() / ("bootstrap-diagnostics-" + uuid4().hex + ".json")

    def visible_kind(args, **kwargs):
        if str(args[0]) != "kind":
            return original(args, **kwargs)
        command = list(args)
        is_create = command[:3] == ["kind", "create", "cluster"]
        if is_create and "--retain" not in command:
            command.append("--retain")
            print("Criando fraud-platform com retenção do node em caso de falha...", flush=True)
        requested_check = kwargs.pop("check", True)
        completed = original(command, check=False, **kwargs)
        if requested_check and completed.returncode:
            result["kind_failure"] = {
                "exit_code": completed.returncode,
                "stdout": redact(completed.stdout, credentials),
                "stderr": redact(completed.stderr, credentials),
            }
            raise ValueError(
                "KinD failed; diagnostic report will preserve the cause and node logs"
            )
        return completed

    try:
        if action == "create":
            foundation.run = visible_kind
            try:
                result["creation"] = foundation.create()
                result["status"] = "cluster_created_or_owned_not_services_validated"
            except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
                result["status"] = "bootstrap_failed"
                result["error"] = redact(str(exc), credentials)
            finally:
                foundation.run = original
        result["node"] = inspect_node(foundation, original, credentials)
    finally:
        foundation.run = original
        foundation.private_json(report, result)
    return result, report


def summary(result, path):
    node = result.get("node", {})
    lines = {
        "status": result["status"],
        "report": str(path),
        "retained_node_present": node.get("retained_node_present", False),
        "host": result["host"],
        "kubelet_tail": node.get("kubelet", {}).get("stdout", "").splitlines()[-35:],
        "components": [
            {
                "name": item["name"],
                "state": item["state"],
                "tail": item["logs"].get("stdout", "").splitlines()[-25:],
                "stderr": item["logs"].get("stderr", ""),
            }
            for item in node.get("components", [])
        ],
        "error": result.get("error"),
    }
    if result.get("kind_failure"):
        lines["kind_error_tail"] = result["kind_failure"]["stderr"].splitlines()[-12:]
    return lines


def main():
    from scripts.platform import kind_foundation as foundation

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "inspect"])
    args = parser.parse_args()
    try:
        with foundation.exclusive():
            result, path = diagnose(foundation, args.action)
        print(json.dumps(summary(result, path), indent=2))
        if result["status"] == "bootstrap_failed":
            parser.exit(
                1, "Bootstrap não concluído; node preservado quando criado. Envie o diagnóstico.\n"
            )
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, "Bootstrap diagnostic stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
