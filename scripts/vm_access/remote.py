"""Prepare selected loopback accesses using existing VM controllers."""

import argparse
import json
import subprocess

from scripts.platform.local_services import controller, services

ALLOWED = ("portal", "grafana", "mlflow", "api", "prometheus")


def prepare(service, portal_id, port=None):
    if service not in ALLOWED:
        raise ValueError("Unsupported remote access")
    if service == "grafana":
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "fraud-grafana-access.service"],
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            if port not in (None, 13001):
                raise ValueError("Supervised Grafana uses VM port 13001")
            url = "http://127.0.0.1:13001"
            if not services.probe(service, url):
                raise ValueError("Supervised Grafana is not healthy; inspect its user unit")
            return {"service": service, "url": url, "owner": "systemd"}
    result = controller.start(service, port=port, portal_id=portal_id)
    return {"service": service, "url": result["url"], "owner": "local_services"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=ALLOWED, required=True)
    parser.add_argument("--portal-id", required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.service, args.portal_id, args.port)))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"VM access preparation stopped: {exc}\n")


if __name__ == "__main__":
    main()
