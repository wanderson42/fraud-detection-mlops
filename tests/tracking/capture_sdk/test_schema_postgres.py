"""Optional real PostgreSQL 17 logical roundtrip, using only fresh temporary clusters.

Set FRAUD_PG17_BIN to an installed PG17 bin directory. No existing DSN is accepted.
The ordinary SDK suite skips this fixture when PostgreSQL binaries are absent.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from scripts.tracking import capture_worker as archive
from scripts.tracking import restore_worker as worker


def test_real_pg17_mlflow_schema_dump_restore_and_negative_mutations(tmp_path):
    configured = os.environ.get("FRAUD_PG17_BIN")
    if not configured:
        pytest.skip("Optional: set FRAUD_PG17_BIN to PG17 binaries for fresh test clusters")
    binaries = Path(configured).resolve(strict=True)

    def run(name, *args, **kwargs):
        return subprocess.run(
            [str(binaries / name), *map(str, args)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            **kwargs,
        )

    assert " 17." in run("postgres", "--version").stdout
    started = []
    ports = []
    for _ in range(2):
        with closing(socket.socket()) as sock:
            sock.bind(("127.0.0.1", 0))
            ports.append(sock.getsockname()[1])
    assert ports[0] != ports[1]

    def connect(port):
        import psycopg

        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="mlflow_lab", user="mlflow", connect_timeout=5
        )

    try:
        for name, port in zip(["source", "target"], ports, strict=True):
            directory = tmp_path / name
            run(
                "initdb",
                "-D",
                directory,
                "--locale=C",
                "--encoding=UTF8",
                "-A",
                "trust",
                "-U",
                "mlflow",
            )
            started.append(directory)  # Cleanup also covers a partial startup.
            run(
                "pg_ctl",
                "-D",
                directory,
                "-l",
                tmp_path / (name + ".log"),
                "-o",
                f"-p {port} -h 127.0.0.1 -c unix_socket_directories=''",
                "start",
            )
            import psycopg

            with psycopg.connect(
                host="127.0.0.1", port=port, dbname="postgres", user="mlflow", autocommit=True
            ) as connection:
                connection.execute("CREATE DATABASE mlflow_lab OWNER mlflow")
        environment = {
            **os.environ,
            "MLFLOW_DISABLE_TELEMETRY": "true",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
        }
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore; "
                    f"SqlAlchemyStore('postgresql+psycopg://mlflow@127.0.0.1:{ports[0]}/mlflow_lab', 'mlflow-artifacts:/')"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        snapshot = tmp_path / "snapshot.dump"
        run(
            "pg_dump",
            "-h",
            "127.0.0.1",
            "-p",
            ports[0],
            "-U",
            "mlflow",
            "-d",
            "mlflow_lab",
            "-Fc",
            "-f",
            snapshot,
        )
        run(
            "pg_restore",
            "-h",
            "127.0.0.1",
            "-p",
            ports[1],
            "-U",
            "mlflow",
            "-d",
            "mlflow_lab",
            "--exit-on-error",
            "--single-transaction",
            "--clean",
            "--if-exists",
            snapshot,
        )
        with connect(ports[0]) as connection:
            source = archive.database_inventory(connection)
        with connect(ports[1]) as connection:
            target = archive.database_inventory(connection)
        assert len(source["columns"]) == 493 and len(source["constraints"]) == 134
        assert {key for key in source if source[key] != target[key]} == {"columns", "constraints"}
        canonical = worker.canonical_metadata(source)
        assert canonical == worker.canonical_metadata(target)
        # All changes operate on the disposable target only; restore the original
        # value after each mutation so each rejection has an independent cause.
        status_check = next(
            row[3] for row in target["constraints"] if row[1] == "runs_status_check"
        )
        mutations = [
            (
                "ALTER TABLE spans ALTER COLUMN input_cost TYPE real",
                "ALTER TABLE spans ALTER COLUMN input_cost TYPE double precision",
            ),
            (
                "ALTER TABLE spans ALTER COLUMN input_cost SET DEFAULT 1",
                "ALTER TABLE spans ALTER COLUMN input_cost DROP DEFAULT",
            ),
            (
                "ALTER TABLE spans ALTER COLUMN model_name SET NOT NULL",
                "ALTER TABLE spans ALTER COLUMN model_name DROP NOT NULL",
            ),
            (
                "ALTER TABLE runs DROP CONSTRAINT runs_status_check; ALTER TABLE runs ADD CONSTRAINT runs_status_check CHECK (status::text = 'FINISHED')",
                "ALTER TABLE runs DROP CONSTRAINT runs_status_check; ALTER TABLE runs ADD CONSTRAINT runs_status_check "
                + status_check,
            ),
        ]
        for change, undo in mutations:
            with connect(ports[1]) as connection:
                connection.execute(change)
            with connect(ports[1]) as connection:
                assert (
                    worker.canonical_metadata(archive.database_inventory(connection)) != canonical
                )
            with connect(ports[1]) as connection:
                connection.execute(undo)
        with connect(ports[1]) as connection:
            assert worker.canonical_metadata(archive.database_inventory(connection)) == canonical
        print(
            json.dumps(
                {
                    "postgresql": "17",
                    "columns": 493,
                    "constraints": 134,
                    "roundtrip": True,
                    "negative_sql_mutations_rejected": len(mutations),
                    "existing_services_accessed": False,
                }
            )
        )
    finally:
        for directory in reversed(started):
            run("pg_ctl", "-D", directory, "-m", "immediate", "stop")
