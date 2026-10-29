"""Update only the presentation of an already accepted session installation."""

import argparse
import json
import subprocess
import time

from scripts.platform import kind_foundation as f
from scripts.platform import kind_observability as k
from scripts.platform import session_observability as mode
from scripts.session_monitoring import components as c
from scripts.session_monitoring import controller as s

SOURCE = "scripts/platform/session_dashboard_presentation.py"
TRANSITION = "references/session_dashboard_presentation_transition_v1.json"


def revision():
    return int(json.loads(k.helm("status", k.RELEASE, "--output", "json").stdout)["version"])


def unchanged(plan):
    directory, binding = s.bound(plan["binding"]["session_id"])
    f.require(binding == plan["binding"], "Session binding changed")
    f.require(c.sources(directory) == plan["accepted_sources"], "Accepted session changed")
    f.require(c.protected() == plan["protected"], "Protected resources or PVCs changed")
    for name, digest in plan["receipt_hashes"].items():
        f.require(mode.digest(s.work() / name) == digest, "Monitoring receipt changed: " + name)
    return directory, binding


def verify(plan):
    directory, binding = unchanged(plan)
    projection = s.collector.collect(directory, binding, include_details=True)
    with s.forward() as receiver, k.forward("prometheus") as prom, k.forward("grafana") as grafana:
        s.put(receiver, "/projection", projection)
        c.verify_metrics(receiver, prom, grafana, projection)
    deadline = time.monotonic() + 90
    while True:
        try:
            dashboards = c.verify_dashboards()
            with k.forward("grafana") as grafana:
                for path in (k.CHART / "files/components").glob("*.json"):
                    wanted = json.loads(path.read_bytes())
                    got = k.available_http(
                        grafana, "/api/dashboards/uid/" + wanted["uid"], auth=True
                    )["dashboard"]
                    for a, b in zip(wanted["panels"], got["panels"], strict=True):
                        f.require(
                            all(
                                a.get(field) == b.get(field)
                                for field in ("title", "targets", "transformations", "gridPos")
                            ),
                            "Live table transformations or panel layout differ",
                        )
            break
        except ValueError, RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    unchanged(plan)
    return {
        "status": "session_dashboard_presentation_verified",
        "dashboards": dashboards,
        "binding": binding,
        "monitoring_receipts_unchanged": True,
        "accepted_sources_unchanged": True,
        "monitoring_PVCs_preserved": True,
        "receiver_image_unchanged": True,
        "visual_review_validated": False,
    }


def operate(key, session_id, action):
    mode.committed_file(SOURCE)
    mode.committed_file(TRANSITION)
    s.committed()
    mode.profile()
    f.require(c.enabled(), "Install and accept the session components first")
    s.image_record()
    directory, binding = s.bound(session_id)
    target = c.work(key)
    path = target / "presentation-plan.json"
    with c.no_watch():
        if not path.exists():
            f.require(action == "apply", "No presentation backup exists")
            live = json.loads(k.helm("get", "values", k.RELEASE, "--output", "json").stdout)
            f.require(live == k.values(True), "Live Helm values differ from owned installation")
            plan = {
                "schema": "fraud_session_dashboard_presentation_v1",
                "binding": binding,
                "source_pins": s.pins(),
                "script_sha256": mode.digest(f.ROOT / SOURCE),
                "transition_sha256": mode.digest(f.ROOT / TRANSITION),
                "previous_revision": revision(),
                "values": live,
                "protected": c.protected(),
                "accepted_sources": c.sources(directory),
                "receipt_hashes": {n: mode.digest(s.work() / n) for n in (*c.FILES, c.MARKER)},
            }
            f.private_json(path, plan)
        plan = json.loads(path.read_bytes())
        f.require(
            plan["binding"] == binding
            and plan["source_pins"] == s.pins()
            and plan["script_sha256"] == mode.digest(f.ROOT / SOURCE)
            and plan["transition_sha256"] == mode.digest(f.ROOT / TRANSITION),
            "Presentation plan belongs to different sources or session",
        )
        unchanged(plan)
        current = json.loads(k.helm("get", "values", k.RELEASE, "--output", "json").stdout)
        f.require(
            current == plan["values"] == k.values(True), "Helm values changed; preserve backup"
        )
        f.require(
            revision() in (plan["previous_revision"], plan["previous_revision"] + 1),
            "Another Helm maintenance occurred; preserve backup",
        )
        if action == "rollback":
            f.require(
                revision() == plan["previous_revision"] + 1, "No candidate revision to roll back"
            )
            k.helm(
                "rollback",
                k.RELEASE,
                str(plan["previous_revision"]),
                "--wait",
                "--timeout",
                "5m",
                timeout=360,
            )
            unchanged(plan)
            result = {
                "status": "session_dashboard_presentation_rolled_back",
                "restored_revision": plan["previous_revision"],
                "accepted_sources_unchanged": True,
            }
        else:
            if revision() == plan["previous_revision"]:
                f.require(action == "apply", "Apply the presentation before verification")
                f.private_json(
                    target / "presentation-applying.json", {"previous_revision": revision()}
                )
                k.install(True)
            result = verify(plan)
        f.private_json(target / ("presentation-" + action + ".json"), result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "verify", "rollback"))
    parser.add_argument("--id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--no-other-writers", action="store_true", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(operate(args.id, args.session_id, args.action), indent=2))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(
            1,
            "Presentation maintenance stopped; preserve backup and keep watch stopped: "
            + str(exc)
            + "\n",
        )


if __name__ == "__main__":
    main()
