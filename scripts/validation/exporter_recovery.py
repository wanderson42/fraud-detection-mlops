"""Explicit local exporter outage check; never executes or changes the replay."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen

PROMETHEUS = "http://127.0.0.1:19090"
EXPORTER = "http://127.0.0.1:19100"
SELECTOR = (
    '{__name__=~"up|fraud_monitor_collector_up|fraud_replay_events_total",job="fraud-replay"}'
)
ALERT = "FraudExporterUnavailable"
EXPECTED = {
    "events": 556824,
    "transactions": 278412,
    "feedback": 278412,
    "decisions": 143945,
    "alerts": 1453,
}
POLL_SECONDS = 2
DEADLINE_SECONDS = 120


def get(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def prometheus(path):
    result = get(PROMETHEUS + path)
    if result.get("status") != "success":
        raise RuntimeError("Prometheus não retornou success")
    return result["data"]


def metrics():
    result = prometheus("/api/v1/query?" + urlencode({"query": SELECTOR}))
    rows = result["result"]
    values = {row["metric"]["__name__"]: float(row["value"][1]) for row in rows}
    expected = {
        "up": 1,
        "fraud_monitor_collector_up": 1,
        "fraud_replay_events_total": EXPECTED["events"],
    }
    return len(rows) == 3 and values == expected


def alerts():
    return [
        alert
        for alert in prometheus("/api/v1/alerts")["alerts"]
        if alert["labels"].get("alertname") == ALERT
        and alert["labels"].get("job") == "fraud-replay"
    ]


def summary():
    result = get(EXPORTER + "/summary")
    if (
        result.get("complete") is not True
        or result.get("counts") != EXPECTED
        or result.get("session_running") != 0
        or result.get("pending_uncommitted_events") != 0
        or result.get("unmeasured_committed_events") != 0
        or result.get("pending_feedback_transactions") != 0
    ):
        raise RuntimeError("O estado nativo difere do ensaio completo esperado")
    return result


def wait_until(check, description):
    deadline = time.monotonic() + DEADLINE_SECONDS
    last_error = "condição ainda não observada"
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            last_error = str(exc)
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"Prazo de {DEADLINE_SECONDS}s excedido: {description}; {last_error}")


def hashes(state):
    result = {}
    for name in (
        "metadata.json",
        "checkpoint.sqlite",
        "telemetry.sqlite",
        "assessment/result.json",
        "assessment/receipt.json",
    ):
        with (state / name).open("rb") as stream:
            result[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def run_check(root: Path) -> dict:
    root = root.resolve()
    infra = root / "infra/observability"
    state = root / "data/replay/paced-native-v1"
    if not (infra / ".env").is_file() or not (infra / "compose.yaml").is_file():
        raise SystemExit("Execute na raiz do projeto, com a stack existente.")
    folder = root / "data/replay/measurements/paced-native-v1"
    folder.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="exporter-recovery-", dir=folder)) / "result.json"
    report = {
        "schema_version": 1,
        "status": "running",
        "scope": "local_exporter_outage_no_replay",
        "poll_seconds": POLL_SECONDS,
        "phase_deadline_seconds": DEADLINE_SECONDS,
        "alert": ALERT,
        "events": [],
        "production_sla_validated": False,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "report_path": str(output),
    }
    origin = time.monotonic()

    def record(event, **details):
        entry = {
            "event": event,
            "at_utc": datetime.now(UTC).isoformat(),
            "elapsed_seconds": time.monotonic() - origin,
            **details,
        }
        report["events"].append(entry)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(event, flush=True)

    compose = [
        "docker",
        "compose",
        "--env-file",
        str(infra / ".env"),
        "-f",
        str(infra / "compose.yaml"),
    ]
    print(f"Relatório: {output}", flush=True)
    try:
        report["summary_before"] = summary()
        if not metrics() or alerts():
            raise RuntimeError("Scrape/coleta inválidos ou alerta já ativo antes do teste")
        groups = prometheus("/api/v1/rules?type=alert")["groups"]
        rules = [rule for group in groups for rule in group["rules"] if rule.get("name") == ALERT]
        if len(rules) != 1 or rules[0].get("health") != "ok":
            raise RuntimeError("A regra esperada não está carregada e saudável")
        report["hashes_before"] = hashes(state)
        record("baseline_verified")
        fault_error = None
        try:
            record("stop_requested")
            stop_start = time.monotonic()
            subprocess.run(
                compose + ["stop", "--timeout", "10", "exporter"], check=True, timeout=40
            )
            record("exporter_stopped")
            firing = wait_until(
                lambda: [a for a in alerts() if a.get("state") == "firing"], "alerta firing"
            )
            report["stop_request_to_firing_observed_seconds"] = time.monotonic() - stop_start
            record("alert_firing_observed", alerts=firing)
        except BaseException as exc:  # noqa: BLE001 - restore and verify before propagating
            fault_error = exc
            record("fault_phase_interrupted", error=f"{type(exc).__name__}: {exc}")
        finally:
            record("start_requested")
            start_time = time.monotonic()
            subprocess.run(compose + ["start", "exporter"], check=True, timeout=40)
            record("exporter_started")
        wait_until(
            lambda: metrics() and not alerts() and summary(),
            "scrape, coleta, resumo nativo e resolução do alerta",
        )
        report["start_request_to_recovery_observed_seconds"] = time.monotonic() - start_time
        report["summary_after"] = summary()
        report["hashes_after"] = hashes(state)
        report["state_files_unchanged"] = report["hashes_before"] == report["hashes_after"]
        if not report["state_files_unchanged"]:
            raise RuntimeError("Um arquivo de estado mudou; preserve o ensaio para inspeção")
        record("recovery_verified")
        if fault_error is not None:
            raise fault_error
        report["status"] = "success"
        record("test_complete")
    except BaseException as exc:
        report["status"] = "failed"
        record("test_failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        print(f"Relatório preservado: {output}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    report = run_check(args.project_root)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "report_path",
                    "stop_request_to_firing_observed_seconds",
                    "start_request_to_recovery_observed_seconds",
                    "state_files_unchanged",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
