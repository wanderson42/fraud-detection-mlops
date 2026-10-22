"""Foreground access to owned KinD services using their existing scoped forwards."""

import argparse
from contextlib import contextmanager
import copy
import json
import subprocess
import time


@contextmanager
def connection(service):
    from scripts.platform import kind_foundation as foundation

    foundation.owner()
    if service == "mlflow":
        from scripts.platform import kind_tracking as tracking

        tracking.inventory()
        with tracking.port_forward() as url:
            yield {"service": service, "url": url}
    elif service == "api":
        from scripts.model_updates import deployment
        from scripts.operational_round import serving
        from scripts.platform import serving_recovery

        active = serving_recovery.current()
        slot = active["slot"]
        manifest = active["plan"]["releases"][slot]["manifest"]
        foundation.require(
            active["model_sha256"] == manifest["model_sha256"],
            "Active model binding differs",
        )
        policy = json.loads(
            (foundation.ROOT / "references/model_update_policy_v1.json").read_bytes()
        )
        # The shared deployment gate checks Registry, route, images and HTTP identity.
        # Pass a copy: existing gates may enrich plan paths while inspecting them.
        inspected = copy.deepcopy(active)
        deployment.check_active(inspected, policy)
        plan = inspected["plan"]
        with serving.forward(serving.gateway(plan)) as url:
            checked = deployment.gate(url, plan, slot, policy)
            foundation.require(
                serving_recovery.current() == active,
                "Active deployment changed while opening access; reopen the command",
            )
            yield {
                "service": service,
                "url": url,
                "registry_version": active["version"],
                "slot": slot,
                "model_sha256": active["model_sha256"],
                "serving_release_id": manifest["serving_release_id"],
                "verification": checked,
            }
        foundation.require(
            serving_recovery.current() == active,
            "Active deployment changed during access; reopen the command",
        )
        deployment.check_active(copy.deepcopy(active), policy)
    elif service == "synthetic-api":
        from scripts.platform import kind_serving as serving
        from scripts.platform import kind_serving_delivery as delivery

        plan = delivery.plan()
        observed = serving.observe(plan)
        with delivery.forward(observed["pods"]["gateway"]["name"]) as url:
            checked = serving.wait_route(url, plan, "blue")
            yield {"service": service, "url": url, "verification": checked}
    else:
        raise ValueError("Only mlflow, api and synthetic-api local access are supported")


def open_local(service):
    with connection(service) as result:
        print(json.dumps(result, indent=2), flush=True)
        print("Ctrl+C encerra somente este acesso local.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("mlflow", "api", "synthetic-api"))
    args = parser.parse_args()
    try:
        open_local(args.service)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Local access stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
