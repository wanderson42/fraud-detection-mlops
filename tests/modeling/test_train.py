import json
from pathlib import Path
import warnings

import numpy as np
import pyarrow.parquet as pq
import pytest
from sklearn.exceptions import ConvergenceWarning
from sklearn.pipeline import Pipeline
from typer.testing import CliRunner

from fraud_detection_mlops import gold
from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.features import FEATURE_COLUMNS
from fraud_detection_mlops.modeling import baseline, persistence, train


def run(source):
    return train.run_baseline(
        source / "gold",
        source / "experiments",
        inventory_path=source / "inventory.json",
        protocol_path=source / "protocol.json",
        tracking_root=None,
    )


def verify(source, directory):
    return train.verify_baseline(
        directory, inventory_path=source / "inventory.json", protocol_path=source / "protocol.json"
    )


def test_run_fits_train_only_and_persists_separate_pipelines(baseline_source, monkeypatch):
    source = baseline_source
    before = {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in (source / "gold").rglob("*")
        if p.is_file()
    }
    original = train.load_split
    calls = []

    def observed(directory, manifest, split):
        calls.append(split)
        assert split in ("train", "validation")
        return original(directory, manifest, split)

    monkeypatch.setattr(train, "load_split", observed)
    result = run(source)
    assert calls == ["train", "validation"]
    assert result["test_evaluated"] is False
    assert result["selected_model"] in {"logistic_regression", "hist_gradient_boosting"}
    directory = Path(result["baseline_path"])
    assert verify(source, directory)["verified_outputs"] == 10
    assert len(list(directory.rglob("model.skops"))) == 3
    model = persistence.load_pipeline(directory / "models/logistic_regression/model.skops")
    source_dir = source / "gold" / ("b" * 40) / "gold_v1"
    manifest = json.loads((source_dir / "manifest.json").read_text())
    X_train, _, _ = original(source_dir, manifest, "train")
    assert model.named_steps["scale"].n_samples_seen_ == 160
    np.testing.assert_allclose(model.named_steps["scale"].mean_, X_train.mean())
    assert list(model.feature_names_in_) == FEATURE_COLUMNS
    dummy = result["comparison"]["dummy_prior"]
    assert dummy["average_precision"] == dummy["fraud_rate"] == 0.1
    assert dummy["roc_auc"] == 0.5
    assert before == {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in (source / "gold").rglob("*")
        if p.is_file()
    }
    record = json.loads(Path(result["audit_path"]).read_text())
    assert record["status"] == "success"
    assert not list((source / "experiments" / ("b" * 40)).glob(".baseline-staging-*"))


def test_selection_tie_break_excludes_the_dummy_reference():
    config = baseline.load_configuration(train.DEFAULT_CONFIG)
    results = {name: {"average_precision": 0.5} for name in train.CLASSIFIERS}
    assert baseline.rank_models(results, config) == [
        "hist_gradient_boosting",
        "logistic_regression",
    ]


def test_test_split_is_rejected_by_training_loader():
    with pytest.raises(train.BaselineError, match="only train and validation"):
        train.load_split(Path("unused"), {}, "test")


def test_verify_never_deserializes_models_and_checks_corruption(baseline_source, monkeypatch):
    result = run(baseline_source)
    directory = Path(result["baseline_path"])
    monkeypatch.setattr(
        persistence.sio, "load", lambda *args, **kwargs: pytest.fail("verify loaded a model")
    )
    assert verify(baseline_source, directory)["status"] == "success"
    model = directory / "models/dummy_prior/model.skops"
    model.write_bytes(b"corruption")
    with pytest.raises(train.BaselineError, match="integrity"):
        verify(baseline_source, directory)


def test_altered_metrics_fail_even_with_updated_file_checksum(baseline_source):
    result = run(baseline_source)
    directory = Path(result["baseline_path"])
    path = directory / "models/dummy_prior/metrics.json"
    value = json.loads(path.read_text())
    value["metrics"]["average_precision"] = 0.99
    path.write_text(json.dumps(value))
    manifest = json.loads((directory / "manifest.json").read_text())
    record = next(
        item for item in manifest["files"] if item["path"] == str(path.relative_to(directory))
    )
    record.update(size_bytes=path.stat().st_size, sha256=sha256(path))
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(train.BaselineError, match="metrics"):
        verify(baseline_source, directory)


def test_write_failure_records_audit_and_leaves_no_published_run(baseline_source, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("model disk failure")

    monkeypatch.setattr(persistence.sio, "dump", fail)
    with pytest.raises(OSError, match="model disk failure"):
        run(baseline_source)
    parent = baseline_source / "experiments" / ("b" * 40)
    assert not (parent / "baseline_v2").exists()
    assert not list(parent.glob(".baseline-staging-*"))
    assert json.loads(next((parent / "runs").glob("*.json")).read_text())["status"] == "failed"


def test_convergence_warning_prevents_publishing_a_misleading_model(baseline_source, monkeypatch):
    original = train.make_model

    def model(name):
        pipeline = original(name)
        if name == "logistic_regression":
            pipeline.fit = lambda *args, **kwargs: warnings.warn(
                "did not converge", ConvergenceWarning
            )
        return pipeline

    monkeypatch.setattr(train, "make_model", model)
    with pytest.raises(ConvergenceWarning):
        run(baseline_source)
    assert not (baseline_source / "experiments" / ("b" * 40) / "baseline_v2").exists()


def test_input_changed_during_fit_prevents_publication(baseline_source, monkeypatch):
    original = Pipeline.fit

    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        path = baseline_source / "protocol.json"
        path.write_text(path.read_text() + "\n")
        return result

    monkeypatch.setattr(Pipeline, "fit", changed)
    with pytest.raises(train.BaselineError, match="Inputs or implementation changed"):
        run(baseline_source)
    assert not (baseline_source / "experiments" / ("b" * 40) / "baseline_v2").exists()


def test_unknown_policy_is_rejected_before_audit_or_fit(baseline_source):
    path = baseline_source / "config.json"
    config = json.loads(train.DEFAULT_CONFIG.read_text())
    config["test_evaluated"] = True
    path.write_text(json.dumps(config))
    with pytest.raises(train.BaselineError, match="Unsupported"):
        train.run_baseline(config_path=path, output_root=baseline_source / "experiments")
    assert not (baseline_source / "experiments").exists()


def test_cli_runs_and_verifies_validation_experiment(baseline_source):
    runner = CliRunner()
    options = [
        "--inventory",
        str(baseline_source / "inventory.json"),
        "--protocol",
        str(baseline_source / "protocol.json"),
    ]
    response = runner.invoke(
        train.app,
        [
            "run",
            "--no-track",
            "--gold-root",
            str(baseline_source / "gold"),
            "--output-root",
            str(baseline_source / "experiments"),
            *options,
        ],
    )
    assert response.exit_code == 0, response.output
    parent = baseline_source / "experiments" / ("b" * 40) / "baseline_v2"
    directory = next(parent.iterdir())
    checked = runner.invoke(train.app, ["verify", str(directory), *options])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["test_evaluated"] is False


def rewrite_gold_labels(source, splits, single_class=False):
    directory = source / "gold" / ("b" * 40) / "gold_v1"
    manifest = json.loads((directory / "manifest.json").read_text())
    for item in manifest["files"]:
        if item["split"] in splits:
            path = directory / item["path"]
            frame = pq.ParquetFile(path).read().to_pandas()
            frame["TX_FRAUD"] = 0 if single_class else 1 - frame["TX_FRAUD"]
            frame = frame.astype(gold.DTYPES)
            frame.to_parquet(
                path, engine="pyarrow", compression="zstd", index=False, version="2.6"
            )
            item.update(size_bytes=path.stat().st_size, sha256=sha256(path))
    (directory / "manifest.json").write_text(json.dumps(manifest))


def test_validation_and_test_label_changes_do_not_change_fitted_models(baseline_source):
    before = run(baseline_source)
    rewrite_gold_labels(baseline_source, {"validation", "test"})
    after = run(baseline_source)
    assert before["run_id"] != after["run_id"]
    for name in train.CLASSIFIERS:
        first = Path(before["baseline_path"]) / "models" / name
        second = Path(after["baseline_path"]) / "models" / name
        a = pq.ParquetFile(first / "validation_predictions.parquet").read().to_pandas()
        b = pq.ParquetFile(second / "validation_predictions.parquet").read().to_pandas()
        np.testing.assert_allclose(a.SCORE, b.SCORE, rtol=1e-12, atol=1e-12)
        assert not a.TX_FRAUD.equals(b.TX_FRAUD)
    assert after["comparison"]["dummy_prior"]["fraud_rate"] == 0.9


def test_single_class_training_is_rejected_before_fitting(baseline_source, monkeypatch):
    rewrite_gold_labels(baseline_source, {"train"}, single_class=True)
    monkeypatch.setattr(train, "make_model", lambda *args: pytest.fail("one-class data fitted"))
    with pytest.raises(train.BaselineError, match="Invalid training classes"):
        run(baseline_source)


def test_single_class_validation_cannot_select_a_model(baseline_source, monkeypatch):
    rewrite_gold_labels(baseline_source, {"validation"}, single_class=True)
    monkeypatch.setattr(
        train, "make_model", lambda *args: pytest.fail("unusable validation fitted")
    )
    with pytest.raises(train.BaselineError, match="Invalid training classes"):
        run(baseline_source)
