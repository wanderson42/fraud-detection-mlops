"""Evaluate the committed frozen candidate once; reuse verified results on retry."""

from datetime import UTC, datetime
import fcntl
from importlib.metadata import version
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
from typing import Annotated

import duckdb
import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.features import DTYPES, FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.gold import ARROW_SCHEMA, _check_values
from fraud_detection_mlops.modeling import experiments, freeze, tracking
from fraud_detection_mlops.modeling.metrics import evaluate_ranking

EVALUATION_VERSION = "final_evaluation_v1"
OUTPUTS = [
    "frozen_candidate.json",
    "test_predictions.parquet",
    "report.json",
    "daily.csv",
    "model_card.md",
]
MODEL_ID = "hist_gradient_boosting"
app = typer.Typer(no_args_is_help=True)


class EvaluationError(ValueError):
    """Final evaluation identities, population or persisted results disagree."""


def load_test(directory: Path, records: list, holdout: dict) -> pd.DataFrame:
    """Read only pinned test partitions, reusing the frozen Gold schema/checks."""
    expected_days = pd.date_range(holdout["start"], holdout["end_exclusive"], inclusive="left")
    if [r["date"] for r in records] != [str(d.date()) for d in expected_days]:
        raise EvaluationError("Incomplete or unordered test date coverage")
    experiments.check_records(directory, records)
    with duckdb.connect() as connection:
        for record in records:
            path = directory / record["path"]
            parquet = pq.ParquetFile(path)
            if (
                not parquet.schema_arrow.equals(ARROW_SCHEMA, check_metadata=False)
                or parquet.metadata.num_rows != record["rows"]
                or record["rows"] <= 0
                or record["split"] != "test"
            ):
                raise EvaluationError("Test schema or partition row count mismatch")
            connection.read_parquet(str(path), hive_partitioning=False).create_view(
                "part", replace=True
            )
            wrong_day = connection.execute(
                "SELECT count(*) FROM part WHERE CAST(TX_DATETIME AS DATE) != ?::DATE",
                [record["date"]],
            ).fetchone()[0]
            if wrong_day or _check_values(connection, "part"):
                raise EvaluationError("Test partition failed frozen Gold checks")
        connection.read_parquet(
            [str(directory / r["path"]) for r in records], hive_partitioning=False
        ).create_view("selected")
        frame = (
            connection.execute(
                "SELECT "
                + ", ".join(DTYPES)
                + " FROM selected ORDER BY TX_DATETIME, TRANSACTION_ID"
            )
            .fetchdf()
            .astype(DTYPES)
        )
    if len(frame) != holdout["expected_rows"] or frame.TRANSACTION_ID.duplicated().any():
        raise EvaluationError("Test population differs from frozen contract")
    return frame


def load_frozen_model(receipt: dict, destination: Path):
    """Check frozen bytes before the existing skops/signature loader deserializes."""
    root = PROJECT_ROOT / receipt["tracking_root"]
    if not (root / "mlflow.db").is_file():
        raise EvaluationError("Restore the existing local MLflow store")
    with tracking.local_tracking(root):
        run = mlflow.MlflowClient().get_run(receipt["model"]["mlflow_run_id"])
        if run.info.status != "FINISHED":
            raise EvaluationError("Frozen model run is not FINISHED")
        path = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=receipt["model_uri"], dst_path=str(destination / "download")
            )
        )
        if sha256(path / "MLmodel") != receipt["model"]["mlmodel_sha256"]:
            raise EvaluationError("Frozen MLmodel bytes changed")
        metadata = mlflow.models.Model.load(path / "MLmodel")
        model_path = path / metadata.flavors["sklearn"]["pickled_model"]
        if (
            model_path.resolve().parent != path.resolve()
            or sha256(model_path) != receipt["model"]["model_sha256"]
        ):
            raise EvaluationError("Frozen model bytes changed")
        model, _ = tracking.download_pipeline(path.resolve().as_uri(), destination / "reviewed")
    if (
        len(model.steps) != 1
        or type(model[-1]) is not HistGradientBoostingClassifier
        or any(
            model[-1].get_params().get(k) != v
            for k, v in receipt["model_parameters"]["parameters"].items()
        )
    ):
        raise EvaluationError("Frozen model parameters changed")
    return model


def measurements(predictions: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Same ranking contract for the candidate and a constant-score control."""
    comparison, daily = {}, []
    for name, scores in (
        (MODEL_ID, predictions.SCORE.to_numpy()),
        ("constant_score_control", np.full(len(predictions), 0.5)),
    ):
        comparison[name] = evaluate_ranking(predictions.TX_FRAUD, scores, predictions)
        days = predictions.TX_DATETIME.dt.strftime("%Y-%m-%d")
        for item in comparison[name]["daily_customer_metrics"]:
            mask = days == item["date"]
            part = predictions.loc[mask]
            metrics = evaluate_ranking(part.TX_FRAUD, scores[mask.to_numpy()], part)
            total = int(part.groupby("CUSTOMER_ID").TX_FRAUD.max().sum())
            found = item["fraudulent_customers_in_alerts"]
            daily.append(
                {
                    **item,
                    "model_id": name,
                    "transactions": len(part),
                    "fraud_count": metrics["fraud_count"],
                    "fraud_rate": metrics["fraud_rate"],
                    "average_precision": metrics["average_precision"],
                    "fraudulent_customers": total,
                    "missed_fraudulent_customers": total - found,
                    "customer_recall_at_100": found / total if total else None,
                }
            )
    daily_frame = pd.DataFrame(daily)
    daily_frame["customer_recall_at_100"] = daily_frame.customer_recall_at_100.astype("float64")
    return comparison, daily_frame


def acceptance(metrics: dict, policy: dict) -> dict:
    criteria = {
        name: {"value": metrics[name], "minimum": policy["acceptance"]["minimum_" + name]}
        for name in ("average_precision", "daily_customer_precision_at_100")
    }
    for item in criteria.values():
        item["passed"] = item["value"] >= item["minimum"]
    passed = all(item["passed"] for item in criteria.values())
    return {
        "scope": policy["acceptance"]["scope"],
        "criteria": criteria,
        "passed": passed,
        "decision": "eligible_for_laboratory_serving_review" if passed else "do_not_advance",
        "production_promotion": False,
    }


def model_card(receipt: dict, report: dict) -> str:
    metrics = report["comparison"][MODEL_ID]
    return (
        "# Model Card — candidato congelado de detecção de fraude\n\n"
        f"Modelo: HistGradientBoostingClassifier (HGB); URI `{receipt['model_uri']}`.\n"
        f"Recibo SHA256: `{report['freeze_sha256']}`; 19 features Gold v1.\n\n"
        "Uso pretendido: laboratório reproduzível para priorização de investigação.\n"
        "Dados sintéticos do Fraud Detection Handbook; treino original, sem refit.\n"
        f"Teste: {receipt['policy']['holdout']['start']} até "
        f"{receipt['policy']['holdout']['end_exclusive']} (fim exclusivo).\n"
        f"Transações: {metrics['rows']}; fraudes: {metrics['fraud_count']}; "
        f"prevalência: {metrics['fraud_rate']:.6f}.\n\n"
        f"AP: {metrics['average_precision']:.6f}; ROC AUC: {metrics['roc_auc']}; "
        f"precisão diária @100: {metrics['daily_customer_precision_at_100']:.6f}.\n"
        f"Decisão: `{report['gate']['decision']}`. Os dois critérios congelados "
        "constam de report.json. Não há promoção em produção.\n\n"
        "Política: máximo score por cliente/dia, até 100 alertas, empate por ID; "
        "dia completo retrospectivo, sem bloqueio de clientes comprometidos.\n"
        "Limites: score não calibrado; dados simulados e uma janela temporal curta "
        "não demonstram eficácia em pagamentos reais. Entidades repetidas "
        "impedem tratar linhas como observações independentes. Sem teste de hipótese, "
        "intervalo de confiança, estimativa financeira ou demonstração de decisão online.\n"
        "Feedback com atraso fixo de sete dias; sem investigação humana real.\n"
        "Mudanças futuras exigem protocolo e nova janela preservada; este teste foi consumido.\n"
    )


def verify_evaluation(directory: Path) -> dict:
    """Recompute metrics/gate from saved scores, without Gold or model access."""
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest["schema_version"] != 1
        or manifest["version"] != EVALUATION_VERSION
        or [f["path"] for f in manifest["files"]] != OUTPUTS
    ):
        raise EvaluationError("Unexpected final evaluation outputs")
    experiments.check_records(directory, manifest["files"])
    if sha256(directory / "frozen_candidate.json") != manifest["identity"]["freeze_sha256"]:
        raise EvaluationError("Frozen receipt snapshot mismatch")
    receipt = json.loads((directory / "frozen_candidate.json").read_text())
    predictions = pd.read_parquet(directory / "test_predictions.parquet")
    comparison, daily = measurements(predictions)
    report = json.loads((directory / "report.json").read_text())
    holdout = receipt["policy"]["holdout"]
    stamps = predictions.TX_DATETIME
    if (
        len(predictions) != holdout["expected_rows"]
        or not (
            (stamps >= pd.Timestamp(holdout["start"]))
            & (stamps < pd.Timestamp(holdout["end_exclusive"]))
        ).all()
        or report["freeze_sha256"] != manifest["identity"]["freeze_sha256"]
        or report["schema_version"] != 1
        or report["version"] != EVALUATION_VERSION
        or report["model_uri"] != receipt["model_uri"]
        or sorted(stamps.dt.strftime("%Y-%m-%d").unique())
        != [
            str(d.date())
            for d in pd.date_range(holdout["start"], holdout["end_exclusive"], inclusive="left")
        ]
        or report["comparison"] != comparison
        or report["gate"] != acceptance(comparison[MODEL_ID], receipt["policy"])
        or report["test_evaluated"] is not True
        or report["refit"] is not False
        or report["formal_superiority_claim"] is not False
        or (directory / "model_card.md").read_text() != model_card(receipt, report)
    ):
        raise EvaluationError("Final metrics, population, decision or Model Card mismatch")
    pd.testing.assert_frame_equal(
        daily, pd.read_csv(directory / "daily.csv", float_precision="round_trip")
    )
    return {
        "evaluation_path": str(directory),
        "verified_outputs": len(OUTPUTS),
        "freeze_sha256": report["freeze_sha256"],
        "comparison": {
            name: {k: v for k, v in metrics.items() if k != "daily_customer_metrics"}
            for name, metrics in comparison.items()
        },
        "gate": report["gate"],
        "test_evaluated": True,
        "refit": False,
        "status": "success",
    }


def publish_evaluation(directory: Path, receipt: dict) -> dict:
    """One native MLflow evaluation run; never modify the historical model run."""
    report = json.loads((directory / "report.json").read_text())
    report_hash = sha256(directory / "report.json")
    root = PROJECT_ROOT / receipt["tracking_root"]
    with tracking.local_tracking(root):
        if mlflow.active_run() is not None:
            raise EvaluationError("Finish the active MLflow run before publishing evaluation")
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name("fraud-final-evaluation-v1")
        experiment_id = (
            experiment.experiment_id
            if experiment
            else client.create_experiment(
                "fraud-final-evaluation-v1",
                artifact_location=(root / "artifacts").resolve().as_uri(),
            )
        )
        runs = client.search_runs(
            [experiment_id], f"tags.freeze_sha256 = '{report['freeze_sha256']}'", max_results=2
        )
        if len(runs) > 1 or (runs and runs[0].data.tags.get("report_sha256") != report_hash):
            raise EvaluationError("Conflicting MLflow evaluation history")
        if not runs:
            runs = [
                client.create_run(
                    experiment_id,
                    tags={
                        "freeze_sha256": report["freeze_sha256"],
                        "report_sha256": report_hash,
                        "model_uri": receipt["model_uri"],
                        "test_evaluated": "true",
                        "refit": "false",
                        "decision": report["gate"]["decision"],
                        "mlflow.runName": "frozen-hgb-test",
                    },
                )
            ]
        if runs[0].info.status != "FINISHED":
            with mlflow.start_run(run_id=runs[0].info.run_id) as run:
                for name, metrics in report["comparison"].items():
                    mlflow.log_metrics(
                        {name + "." + k: v for k, v in metrics.items() if type(v) in (int, float)}
                    )
                for name in [*OUTPUTS, "manifest.json"]:
                    mlflow.log_artifact(str(directory / name))
                run_id = run.info.run_id
        else:
            run_id = runs[0].info.run_id
    record = {
        "run_id": run_id,
        "report_sha256": report_hash,
        "freeze_sha256": report["freeze_sha256"],
    }
    write_json(directory / "mlflow.json", record)
    return record


def run_evaluation(freeze_path: Path = freeze.DEFAULT_FREEZE) -> dict:
    """Committed inputs, exact frozen model, fixed holdout, then persisted decision."""
    freeze.verify_freeze(freeze_path, require_committed=True)
    provenance = experiments.committed_inputs(freeze_path)
    receipt = json.loads(freeze_path.read_text())
    for package, expected in receipt["environment"].items():
        if version(package) != expected:
            raise EvaluationError(f"Restore frozen package version: {package}=={expected}")
    gold = PROJECT_ROOT / receipt["gold_path"]
    if sha256(gold / "manifest.json") != receipt["policy"]["reference"]["gold_manifest_sha256"]:
        raise EvaluationError("Frozen Gold manifest changed")
    manifest = json.loads((gold / "manifest.json").read_text())
    records = [f for f in manifest["files"] if f["split"] == "test"]
    identity = {
        "freeze_sha256": sha256(freeze_path),
        "implementation_files": provenance["files"],
        "test_partitions": records,
    }
    directory = gold.parent / EVALUATION_VERSION / identity["freeze_sha256"]
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EvaluationError("Final evaluation is already running") from exc
        state_path = directory / "state.json"
        if (directory / "manifest.json").exists() and not state_path.exists():
            raise EvaluationError("Restore the original access record before reusing evaluation")
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "first_started_at_utc": datetime.now(UTC).isoformat(),
                "attempts": 0,
                "implementation_revision": provenance["git_revision"],
                "test_access_started": False,
            }
        )
        if state["identity"] != identity:
            raise EvaluationError("Retry inputs or implementation differ from the first attempt")
        if (directory / "manifest.json").exists():
            result = verify_evaluation(directory)
            return {**result, "reused": True, "tracking": publish_evaluation(directory, receipt)}
        state.update(status="started", attempts=state["attempts"] + 1)
        write_json(state_path, state)
        try:
            with TemporaryDirectory(prefix="fraud-final-evaluation-") as temporary:
                model = load_frozen_model(receipt, Path(temporary))
                # Conservatively record consumption before the first analytical read.
                state.update(test_access_started=True, status="accessing_test")
                write_json(state_path, state)
                frame = load_test(gold, records, receipt["policy"]["holdout"])
                with threadpool_limits(limits=4):
                    scores = model.predict_proba(frame[FEATURE_COLUMNS].astype("float64"))[:, 1]
                predictions = frame[list(METADATA_DTYPES)].assign(SCORE=scores)
            comparison, daily = measurements(predictions)
            report = {
                "schema_version": 1,
                "version": EVALUATION_VERSION,
                "freeze_sha256": identity["freeze_sha256"],
                "model_uri": receipt["model_uri"],
                "comparison": comparison,
                "gate": acceptance(comparison[MODEL_ID], receipt["policy"]),
                "test_evaluated": True,
                "refit": False,
                "formal_superiority_claim": False,
            }
            (directory / "frozen_candidate.json").write_bytes(freeze_path.read_bytes())
            predictions.to_parquet(
                directory / "test_predictions.parquet", index=False, compression="zstd"
            )
            write_json(directory / "report.json", report)
            daily.to_csv(directory / "daily.csv", index=False)
            (directory / "model_card.md").write_text(model_card(receipt, report))
            experiments.check_records(gold, records)
            freeze.verify_freeze(freeze_path, require_committed=True)
            if experiments.committed_inputs(freeze_path)["files"] != provenance["files"]:
                raise EvaluationError("Implementation changed during evaluation")
            write_json(
                directory / "manifest.json",
                {
                    "schema_version": 1,
                    "version": EVALUATION_VERSION,
                    "identity": identity,
                    "environment": {
                        "python": platform.python_version(),
                        **{
                            p: version(p)
                            for p in (
                                "mlflow",
                                "skops",
                                "scikit-learn",
                                "numpy",
                                "pandas",
                                "duckdb",
                                "pyarrow",
                            )
                        },
                    },
                    "files": experiments.file_records(directory, OUTPUTS),
                },
            )
            result = verify_evaluation(directory)
            state.update(status="success", completed_at_utc=datetime.now(UTC).isoformat())
            write_json(state_path, state)
        except Exception as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            write_json(state_path, state)
            raise
        return {**result, "reused": False, "tracking": publish_evaluation(directory, receipt)}


@app.command("run")
def run(freeze_path: Annotated[Path, typer.Option()] = freeze.DEFAULT_FREEZE):
    try:
        print(json.dumps(run_evaluation(freeze_path), indent=2))
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        print(f"Final evaluation failed: {exc}")
        raise typer.Exit(1) from exc


@app.command("verify")
def verify(directory: Annotated[Path, typer.Argument()]):
    try:
        print(json.dumps(verify_evaluation(directory), indent=2))
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        print(f"Final evaluation verification failed: {exc}")
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
