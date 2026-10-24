"""Prepare verified analyst snapshots and launch an isolated, loopback-only Streamlit client."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import ProxyHandler, build_opener
import venv

from scripts.fraud_portal import data, session_link, theme

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = ROOT / ".portal-venv"


def identifier(value):
    data.require(bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value)), "ID inválido")
    return value


def config(key):
    path = data.checked_path(
        ROOT / "data/storage/kind-platform/portal" / (identifier(key) + ".json")
    )
    receipt = json.loads(path.read_bytes())
    expected = ROOT / "data/reports/fraud-portal" / key
    data.require(
        receipt["schema"] == "fraud_portal_access_v1"
        and receipt["portal_id"] == key
        and Path(receipt["directory"]) == expected,
        "Configuração de acesso divergente",
    )
    value, manifest = data.load_bundle(expected, receipt["manifest_sha256"])
    return receipt, value, manifest


def performance_config(key):
    receipt, snapshot, _ = config(key)
    path = ROOT / "data/storage/kind-platform/portal" / (identifier(key) + "-performance.json")
    if not path.exists():
        return None
    from scripts.fraud_portal.performance import reader

    link = json.loads(data.checked_path(path).read_bytes())
    directory = data.checked_path(Path(link["directory"]))
    data.require(
        link["schema"] == "fraud_portal_performance_access_v1"
        and directory.parent == ROOT / "data/reports/fraud-portal/performance",
        "Configuração de desempenho divergente",
    )
    report, _ = reader.load(
        directory, link["manifest_sha256"], receipt["manifest_sha256"], snapshot
    )
    return link, report


def session_config(key):
    path = session_link.path_for(ROOT, key)
    if not path.exists():
        return None
    return session_link.load(path, data.sha(path), root=ROOT)


def link_session(key, session_id, previous_sha=None):
    from scripts.platform import kind_foundation as f
    from scripts.product_session import controller as sessions
    from scripts.product_session import reader

    committed()
    config(key)
    directory, _ = sessions.load(session_id)
    prepared = json.loads((directory / "session.json").read_bytes())
    value = reader.read(directory, data.sha(directory / "session.json"), page_size=25)
    link = {
        "schema": "fraud_portal_session_access_v1",
        "portal_id": key,
        "session_id": session_id,
        "directory": str(directory),
        "session_sha256": data.sha(directory / "session.json"),
        "input_sha256": prepared["input_sha256"],
        "model_sha256": value["model_sha256"],
        "serving_release_id": value["serving_release_id"],
    }
    path = session_link.path_for(ROOT, key)
    if path.exists():
        old = session_config(key)
        if old == link:
            return {"status": "portal_session_already_linked", **link}
        data.require(
            previous_sha == old["session_sha256"],
            "Informe o SHA da sessão anterior para trocar o vínculo",
        )
        archive = path.with_name(key + "-session-" + old["session_sha256"] + ".json")
        if not archive.exists():
            f.private_json(archive, old)
    else:
        data.require(previous_sha is None, "Não existe vínculo anterior para substituir")
    # Atomic pointer only; no writes to session, snapshot, performance or Registry.
    sessions.atomic(path, link)
    return {"status": "portal_session_linked", **link}


def verify_session(key):
    link = session_config(key)
    data.require(link is not None, "Execute make portal-session-link primeiro")
    path = session_link.path_for(ROOT, key)
    value = session_link.page(path, data.sha(path), root=ROOT, page_size=25)
    return {
        "status": "portal_session_readonly_verified",
        **link,
        "counts": value["counts"],
        "totals": value["totals"],
        "model_loaded": False,
        "champion_modified": False,
    }


def install():
    data.checked_path(ENVIRONMENT)
    if not ENVIRONMENT.exists():
        venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
    python = ENVIRONMENT / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            str(ROOT / "infra/portal/requirements.txt"),
        ],
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    return {
        "status": "portal_runtime_installed",
        "environment": str(ENVIRONMENT),
        "scientific_environment_modified": False,
    }


def committed():
    paths = list((ROOT / "scripts/fraud_portal").rglob("*.py"))
    paths += list((ROOT / "infra/portal").glob("requirements*"))
    import hashlib

    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        result = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=ROOT, capture_output=True, check=False
        )
        data.require(
            result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == data.sha(path),
            "Commite a implementação revisada antes de preparar: " + name,
        )


def open_portal(key, port):
    receipt, _, _ = config(key)
    python = ENVIRONMENT / "bin/python"
    data.require(python.is_file(), "Execute make portal-install primeiro")
    data.require(type(port) is int and 1024 <= port <= 65535, "Porta inválida")
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "XDG_CACHE_HOME"}
    environment = {k: v for k, v in os.environ.items() if k in allowed}
    environment.update(
        PYTHONPATH=str(ROOT),
        FRAUD_PORTAL_BUNDLE=receipt["directory"],
        FRAUD_PORTAL_MANIFEST_SHA256=receipt["manifest_sha256"],
        STREAMLIT_BROWSER_GATHER_USAGE_STATS="false",
        DO_NOT_TRACK="true",
    )
    session_path = session_link.path_for(ROOT, key)
    if session_path.exists():
        environment.update(
            FRAUD_PORTAL_SESSION_LINK=str(session_path),
            FRAUD_PORTAL_SESSION_LINK_SHA256=data.sha(session_path),
        )
    evaluation = performance_config(key)
    if evaluation:
        link, _ = evaluation
        environment.update(
            FRAUD_PORTAL_PERFORMANCE=link["directory"],
            FRAUD_PORTAL_PERFORMANCE_SHA256=link["manifest_sha256"],
        )
    print(f"Portal: http://127.0.0.1:{port} — Ctrl+C encerra somente esta interface", flush=True)
    return subprocess.run(
        [
            str(python),
            "-m",
            "streamlit",
            "run",
            str(ROOT / "scripts/fraud_portal/app.py"),
            "--server.address=127.0.0.1",
            f"--server.port={port}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            "--client.toolbarMode=viewer",
            *theme.launch_flags(),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    ).returncode


def smoke(key, port):
    _, value, manifest = config(key)
    with build_opener(ProxyHandler({})).open(
        f"http://127.0.0.1:{port}/_stcore/health", timeout=5
    ) as response:
        data.require(
            response.status == 200 and response.read(100).strip() == b"ok",
            "Portal não está saudável",
        )
    return {
        "status": "portal_HTTP_and_snapshot_verified",
        "url": f"http://127.0.0.1:{port}",
        "summary": data.summary(value["cases"]),
        "model_sha256": manifest["model_sha256"],
        "read_only": True,
        "champion_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "install",
            "prepare",
            "verify",
            "open",
            "smoke",
            "performance-prepare",
            "performance-verify",
            "session-link",
            "session-verify",
            "session-smoke",
        ],
    )
    parser.add_argument("--session-id", default="arrival-2k-v1")
    parser.add_argument("--previous-session-sha256")
    parser.add_argument("--portal-id", default="investigation-v1")
    parser.add_argument("--flow-id", default="champion-http-v1")
    parser.add_argument("--explanation-id", default="champion-shap-v1")
    parser.add_argument("--explanation-sha256")
    parser.add_argument("--no-other-writers", action="store_true")
    parser.add_argument("--performance-id", default="validation-v1")
    parser.add_argument("--evaluation-round", default="portfolio-v1")
    parser.add_argument("--gold-directory", type=Path)
    parser.add_argument("--without-explanations", action="store_true")
    parser.add_argument("--port", type=int, default=18501)
    args = parser.parse_args()
    try:
        key = identifier(args.portal_id)
        if args.action == "install":
            result = install()
        elif args.action == "prepare":
            data.require(
                not args.without_explanations or not args.explanation_sha256,
                "Não combine digest SHAP com --without-explanations",
            )
            from scripts.fraud_portal.export import prepare
            from scripts.platform import kind_foundation as f

            committed()
            with f.exclusive():
                result = prepare(
                    ROOT,
                    key,
                    identifier(args.flow_id),
                    None if args.without_explanations else identifier(args.explanation_id),
                    None if args.without_explanations else args.explanation_sha256,
                    args.no_other_writers,
                )
        elif args.action == "performance-prepare":
            from scripts.fraud_portal.performance.collect import prepare
            from scripts.platform import kind_foundation as f

            committed()
            with f.exclusive():
                result = prepare(
                    ROOT,
                    key,
                    identifier(args.performance_id),
                    identifier(args.evaluation_round),
                    args.no_other_writers,
                    args.gold_directory,
                )
        elif args.action == "performance-verify":
            evaluation = performance_config(key)
            data.require(evaluation is not None, "Desempenho ainda não preparado")
            link, report = evaluation
            result = {
                "status": "portal_performance_verified",
                **link,
                "model_sha256": report["model_sha256"],
                "validation_rows": report["dataset"]["rows"],
                "model_loaded": False,
                "champion_modified": False,
            }
        elif args.action == "session-link":
            from scripts.platform import kind_foundation as f

            with f.exclusive():
                result = link_session(key, args.session_id, args.previous_session_sha256)
        elif args.action == "session-verify":
            result = verify_session(key)
        elif args.action == "session-smoke":
            result = {
                "HTTP_health": smoke(key, args.port),
                "session": verify_session(key),
                "UI_browser_review_validated": False,
            }
        elif args.action == "open":
            raise SystemExit(open_portal(key, args.port))
        elif args.action == "smoke":
            result = smoke(key, args.port)
        else:
            receipt, value, _ = config(key)
            result = {
                "status": "portal_snapshot_verified",
                **receipt,
                "summary": data.summary(value["cases"]),
                "model_loaded": False,
            }
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Portal interrompido: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
