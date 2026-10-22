"""Optional bounded search; the outer comparison window is never an input."""

import itertools
import json
import time

import mlflow
from mlflow import MlflowClient
import pandas as pd
from scripts.operational_round import lineage
from scripts.operational_round.policy import digest, require

from fraud_detection_mlops.modeling.algorithms.model_catalog import MODEL_CATALOG
from fraud_detection_mlops.modeling.experiments.candidate_training import fit_candidate


def subset(batch, start, end):
    mask = (batch[2].TX_DATETIME >= pd.Timestamp(start)) & (
        batch[2].TX_DATETIME < pd.Timestamp(end)
    )
    return tuple(v.loc[mask].copy() for v in batch)


def choose(name, train, provenance, partitions, policy, uri, update_id, parent, work):
    spec = policy["optimization"][name]
    fixed = spec.get("parameters", {})
    if spec["mode"] == "fixed":
        return fixed, {"mode": "fixed", "parameters": fixed, "trials": []}
    windows = policy["inner_windows"]
    fit = subset(train, windows["fit_start"], windows["fit_end_exclusive"])
    selection = subset(train, windows["selection_start"], windows["selection_end_exclusive"])
    require(
        len(fit[0])
        and len(selection[0])
        and fit[2].LABEL_AVAILABLE_AT.max() < selection[2].TX_DATETIME.min(),
        "Inner tuning needs nonoverlapping mature training feedback",
    )
    descriptions, datasets = {}, {}
    for context, batch in [("training", fit), ("validation", selection)]:
        selected = [
            p
            for p in partitions
            if str(batch[2].TX_DATETIME.min().date())
            <= p["date"]
            <= str(batch[2].TX_DATETIME.max().date())
        ]
        descriptions[context], datasets[context] = lineage.description(
            *batch, context=context, provenance=provenance, partitions=selected
        )
    client = MlflowClient(tracking_uri=uri)
    exp_name = "fraud-update-tuning-" + update_id
    tags = {"fraud.update.id": update_id, "fraud.update.policy_sha256": digest(policy)}
    experiment = client.get_experiment_by_name(exp_name)
    if experiment:
        require(experiment.tags == tags, "Foreign tuning experiment")
        eid = experiment.experiment_id
    else:
        eid = client.create_experiment(exp_name, tags=tags)
    study = None
    if spec["mode"] == "optuna":
        import optuna

        study = optuna.create_study(
            direction="maximize", sampler=optuna.samplers.TPESampler(seed=42)
        )
    space = spec["space"]
    grid = iter(itertools.product(*space.values()))
    results = []
    deadline = time.monotonic() + spec["timeout_seconds"]
    for number in range(spec["trials"]):
        if time.monotonic() >= deadline:
            break
        trial = study.ask() if study else None
        if trial:
            chosen = {k: trial.suggest_categorical(k, v) for k, v in space.items()}
        else:
            values = next(grid, None)
            if values is None:
                break
            chosen = dict(zip(space, values, strict=True))
        parameters = {**fixed, **chosen}
        with mlflow.start_run(
            experiment_id=eid, nested=True, run_name=name + "-" + str(number)
        ) as run:
            mlflow.set_tags(
                {
                    **tags,
                    "model_id": name,
                    "outer_comparison_used": "false",
                    "mlflow.parentRunId": parent,
                }
            )
            for context, dataset in datasets.items():
                d = descriptions[context]
                mlflow.log_input(
                    dataset,
                    context=context,
                    tags={
                        "fraud.dataset.identity_sha256": d["identity_sha256"],
                        "fraud.dataset.content_rows_sha256": d["content_rows_sha256"],
                    },
                )
            model, _, metrics, _ = fit_candidate(
                name,
                fit[0],
                fit[1],
                selection[0],
                selection[1],
                selection[2],
                model_spec=MODEL_CATALOG[name],
                parameters=parameters,
            )
            mlflow.log_params({k: str(v) for k, v in model.get_params(deep=True).items()})
            mlflow.log_metric("inner_average_precision", metrics["average_precision"])
            if study:
                study.tell(trial, metrics["average_precision"])
            results.append(
                {
                    "number": number,
                    "run_id": run.info.run_id,
                    "parameters": parameters,
                    "average_precision": metrics["average_precision"],
                }
            )
    require(results, "No tuning trial completed within the declared budget")
    best = min(results, key=lambda v: (-v["average_precision"], v["number"]))
    result = {
        "mode": spec["mode"],
        "parameters": best["parameters"],
        "trials": results,
        "inner_datasets": descriptions,
        "outer_comparison_used": False,
        "timeout_scope": "checked_between_trials; Job deadline bounds total execution",
    }
    path = work / ("search-" + name + ".json")
    path.write_text(json.dumps(result, indent=2, allow_nan=False))
    mlflow.log_artifact(str(path))
    return best["parameters"], result
