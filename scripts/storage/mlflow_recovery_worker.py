"""MLflow API/model rehearsal guarded at Python file/SQLite/network/process boundaries."""

from contextlib import closing
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import unquote, urlsplit

from scripts.storage import archive
from scripts.storage import mlflow_inventory as inv


def install_guard(candidate, roots, databases):
    state = {"data_violations": [], "blocked_network": [], "process_violations": []}

    def path_of(value):
        if isinstance(value, int):
            return None
        value = os.fsdecode(value)
        if value.startswith("file:"):
            value = unquote(urlsplit(value).path)
        return Path(value).absolute().resolve()

    def check(value, writing=False):
        path = path_of(value)
        if path is None or path == Path("/dev/null"):
            return
        if writing and not path.is_relative_to(candidate):
            state["data_violations"].append("write_outside_copy:" + str(path))
            raise PermissionError("Write outside disposable rehearsal")
        if not path.is_relative_to(candidate) and any(
            path.is_relative_to(root / "data") for root in roots
        ):
            state["data_violations"].append("outside_data_read:" + str(path))
            raise PermissionError("Original/restored project data are blocked in worker")

    def audit(event, args):
        if event == "open":
            mode, flags = args[1:3]
            check(
                args[0],
                writing=(isinstance(mode, str) and any(c in mode for c in "wax+"))
                or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)),
            )
        elif event in {"os.listdir", "os.scandir"}:
            check(args[0] if args and args[0] is not None else os.getcwd())
        elif event == "sqlite3.connect" and args[0] != ":memory:":
            if path_of(args[0]) not in databases:
                state["data_violations"].append("unreviewed_database")
                raise PermissionError("Only disposable tracking DBs may be opened")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod"}:
            check(args[0], writing=True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            check(args[0], writing=True)
            check(args[1], writing=True)
        elif event in {"socket.connect", "socket.bind"}:
            state["blocked_network"].append(event)
            raise PermissionError("Network prohibited in rehearsal")
        elif event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
            state["process_violations"].append(event)
            raise PermissionError("Additional process prohibited in rehearsal")

    sys.addaudithook(audit)
    return state


def metric_values(values):
    return sorted((str(k), float(v).hex()) for k, v in values.items())


def signature_frame(frame, schema):
    """Widen integer columns only for archived double inputs, without value loss."""
    from mlflow.types.schema import DataType

    if schema is None or schema.input_names() != list(frame.columns):
        raise ValueError("Archived model input columns differ from smoke contract")
    adapted, changes = frame.copy(), []
    for spec in schema.inputs:
        column = frame[spec.name]
        if spec.type == DataType.double and column.dtype.kind in {"i", "u"}:
            converted = column.astype("float64")
            if any(int(before) != int(after) for before, after in zip(column, converted)):
                raise ValueError(
                    "Integer input cannot be represented exactly as double: " + spec.name
                )
            adapted[spec.name] = converted
            changes.append({"column": spec.name, "from": str(column.dtype), "to": "float64"})
    return adapted, changes


def inspect_api(client, database, path):
    from mlflow.entities import LoggedModelStatus, ViewType

    runs, token = {}, None
    while True:
        page = client.search_runs(
            [str(e["experiment_id"]) for e in database["experiments"]],
            run_view_type=ViewType.ALL,
            max_results=1000,
            page_token=token,
        )
        for run in page:
            if run.info.run_id in runs:
                raise ValueError("Duplicate API run")
            runs[run.info.run_id] = run
        token = page.token
        if not token:
            break
    if set(runs) != {r["run_uuid"] for r in database["runs"]}:
        raise ValueError("MLflow API omitted runs")
    count = 0
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        for expected in database["experiments"]:
            observed = client.get_experiment(str(expected["experiment_id"]))
            if any(
                getattr(observed, k) != expected[k]
                for k in ("name", "artifact_location", "lifecycle_stage")
            ):
                raise ValueError("Experiment metadata differs through API")
        for expected in database["runs"]:
            run = client.get_run(expected["run_uuid"])
            if any(
                getattr(run.info, k) != expected[k]
                for k in ("status", "lifecycle_stage", "artifact_uri")
            ):
                raise ValueError("Run metadata differs through API")
            for table, actual in (("params", run.data.params), ("tags", run.data.tags)):
                saved = dict(
                    connection.execute(
                        f"SELECT key,value FROM {table} WHERE run_uuid=?", (run.info.run_id,)
                    )
                )
                if actual != saved:
                    raise ValueError("Run params/tags differ through API")
            latest = {
                key: math.nan if nan else value
                for key, value, nan in connection.execute(
                    "SELECT key,value,is_nan FROM latest_metrics WHERE run_uuid=?",
                    (run.info.run_id,),
                )
            }
            if metric_values(run.data.metrics) != metric_values(latest):
                raise ValueError("Latest metrics differ through API")
            for (key,) in connection.execute(
                "SELECT DISTINCT key FROM metrics WHERE run_uuid=?", (run.info.run_id,)
            ):
                expected_history = sorted(
                    (float(math.nan if nan else value).hex(), timestamp, step)
                    for value, timestamp, step, nan in connection.execute(
                        "SELECT value,timestamp,step,is_nan FROM metrics WHERE run_uuid=? AND key=?",
                        (run.info.run_id, key),
                    )
                )
                actual = client.get_metric_history(run.info.run_id, key)
                if (
                    sorted((float(m.value).hex(), m.timestamp, m.step) for m in actual)
                    != expected_history
                ):
                    raise ValueError("Full metric history differs through API")
                count += len(actual)
    for expected in database["logged_models"]:
        model = client.get_logged_model(expected["model_id"])
        if (
            model.source_run_id != expected["source_run_id"]
            or model.artifact_location != expected["artifact_location"]
            or model.status != LoggedModelStatus.from_int(expected["status"])
        ):
            raise ValueError("Logged model metadata differs through API")
    return {
        "runs": len(runs),
        "logged_models": len(database["logged_models"]),
        "metric_history_rows": count,
    }


def execute(request):
    candidate = Path(request["candidate"]).resolve()
    stores = request["stores"]
    guard = install_guard(
        candidate,
        [Path(request["project_root"]), Path(request["historical_root"])],
        {candidate / s["tracking_root"] / "mlflow.db" for s in stores},
    )
    if version("mlflow") != "3.17.0":
        raise ValueError("Restore MLflow 3.17.0")
    import mlflow
    import numpy as np
    from threadpoolctl import threadpool_limits

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.serving.model_release import runtime_environment
    from fraud_detection_mlops.serving.request_schema import ScoreRequest

    control = request["control"]
    if runtime_environment() != control["runtime_environment"]:
        raise ValueError("Restore pinned release environment")
    summaries = []
    for store in stores:
        path = candidate / store["tracking_root"] / "mlflow.db"
        client = mlflow.MlflowClient(tracking_uri=tracking.tracking_uri(path.parent))
        summaries.append(
            {
                "tracking_root": store["tracking_root"],
                **inspect_api(client, store["database"], path),
            }
        )
        if inv.inspect_database(path)["tables"] != store["database"]["tables"]:
            raise ValueError("API reads changed tracking rows")
    if not control["model_uri"].startswith(("runs:/", "models:/")):
        raise ValueError("Expected runs:/ or models:/ URI")
    with tracking.local_tracking(candidate / "data/tracking"), threadpool_limits(limits=4):
        path = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=control["model_uri"], dst_path=str(candidate / "downloads")
            )
        ).resolve()
        if not path.is_relative_to(candidate / "downloads"):
            raise ValueError("Model download escaped rehearsal")
        metadata = mlflow.models.Model.load(path / "MLmodel")
        model_path = archive.checked_path(
            path, Path(metadata.flavors.get("sklearn", {}).get("pickled_model", ""))
        )
        if model_path.parent != path or archive.digest(model_path) != control["model_sha256"]:
            raise ValueError("Tracked model differs from external release pin")
        model = tracking.load_tracked_model(str(path), candidate / "reviewed")
        frame, adaptations = signature_frame(
            ScoreRequest.model_validate(control["request"]).feature_frame(),
            metadata.signature.inputs,
        )
        scores = np.asarray(model.predict(frame))
        if scores.shape != (1, 2) or not np.isfinite(scores).all():
            raise ValueError("Expected binary probabilities")
        score = float(scores[0, 1])
        if not math.isclose(score, control["expected_score"], rel_tol=0, abs_tol=1e-12):
            raise ValueError("Reloaded score differs from historical control")
    for store in stores:
        if (
            inv.inspect_database(candidate / store["tracking_root"] / "mlflow.db")["tables"]
            != store["database"]["tables"]
        ):
            raise ValueError("Model resolution changed tracking rows")
    if guard["data_violations"]:
        raise ValueError("Forbidden rehearsal I/O attempt: " + repr(guard))
    return {
        "status": "success",
        "model_uri": control["model_uri"],
        "model_sha256": control["model_sha256"],
        "serving_release_id": control["serving_release_id"],
        "score": score,
        "expected_score": control["expected_score"],
        "absolute_score_error": abs(score - control["expected_score"]),
        "signature_input_adaptations": adaptations,
        "parity_tolerance": 1e-12,
        "stores": summaries,
        "total_runs": sum(s["runs"] for s in summaries),
        "total_logged_models": sum(s["logged_models"] for s in summaries),
        "metric_history_rows": sum(s["metric_history_rows"] for s in summaries),
        "full_API_history_verified": True,
        "database_rows_unchanged_after_MLflow_reads": True,
        "model_reload_validated": True,
        "operational_tracking_restore_validated": True,
        "guard": "Python audit hooks; not an OS sandbox",
        "data_access_violations": 0,
        "network_connections_allowed": 0,
        "blocked_network_operations": len(guard["blocked_network"]),
        "additional_processes_allowed": 0,
        "blocked_process_operations": len(guard["process_violations"]),
    }


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = execute(json.loads(path.read_bytes()))
    (path.parent / "result.json").write_bytes(archive.encoded(result))
