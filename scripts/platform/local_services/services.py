"""Allowlisted foreground commands and readiness endpoints; no deployment operations."""

import json
from pathlib import Path
import re
import socket
import sys
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[3]
PORTS = {"grafana": 13001, "prometheus": 19091, "exporter": 19101, "portal": 18501}
HEALTH = {
    "api": "/ready",
    "mlflow": "/health",
    "grafana": "/api/health",
    "prometheus": "/-/ready",
    "exporter": "/readyz",
    "rustfs": "/health/ready",
    "portal": "/_stcore/health",
}
COMMON = ("api", "mlflow", "grafana", "portal")


def command(service, port=None, portal_id="investigation-v1"):
    if service not in HEALTH:
        raise ValueError("Unknown local access service")
    if port is not None and (service not in PORTS or not 1024 <= port <= 65535):
        raise ValueError("Only dashboard/monitoring accesses accept an unprivileged fixed port")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", portal_id):
        raise ValueError("Invalid portal ID")
    prefix = [sys.executable, "-u", "-m"]
    if service in {"api", "mlflow"}:
        return prefix + ["scripts.platform.local_access", service]
    if service == "rustfs":
        return prefix + ["scripts.platform.local_services.services"]
    if service == "portal":
        return prefix + [
            "scripts.fraud_portal.controller",
            "open",
            "--portal-id",
            portal_id,
            "--port",
            str(port or PORTS[service]),
        ]
    return prefix + [
        "scripts.platform.kind_observability",
        "open",
        "--component",
        service,
        "--port",
        str(port or PORTS[service]),
    ]


def free_port(service, port):
    selected = port or PORTS.get(service)
    if selected is not None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", selected))


def probe(service, url):
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", url):
        return False
    try:
        with build_opener(ProxyHandler({})).open(url + HEALTH[service], timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def rustfs():
    from scripts.platform import kind_datasets, kind_foundation

    kind_foundation.inventory()
    with kind_datasets.port_forward() as url:
        if not probe("rustfs", url):
            raise ValueError("RustFS readiness failed")
        print(json.dumps({"service": "rustfs", "url": url}), flush=True)
        import time

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    rustfs()
