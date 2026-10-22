"""Public-API import with resumable source IDs, complete readback and no training."""

import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from mlflow.entities import (
    Dataset,
    DatasetInput,
    InputTag,
    LoggedModelInput,
    LoggedModelOutput,
    LoggedModelStatus,
    Metric,
    Param,
    RunTag,
    ViewType,
)
from scripts.platform.history.source import fingerprint, identity_tags, require
from scripts.storage import archive


def paged(function, **kwargs):
    result, token = [], None
    while True:
        page = function(page_token=token, **kwargs)
        result.extend(page)
        token = page.token
        if not token:
            return result


def run_rows(client, experiment):
    return paged(
        client.search_runs,
        experiment_ids=[experiment],
        run_view_type=ViewType.ALL,
        max_results=1000,
    )


def model_rows(client, experiment):
    return paged(client.search_logged_models, experiment_ids=[experiment], max_results=1000)


def expected_tags(plan, store, kind, item):
    return {**item["tags"], **identity_tags(plan, store, kind, item["id"], item["metadata"])}


def target_experiments(client, plan, create):
    # Review every name before creating the first entity.
    for store in plan["stores"]:
        for item in store["experiments"]:
            current = client.get_experiment_by_name(item["target_name"])
            require(
                current is None
                or current.tags == expected_tags(plan, store["tracking_root"], "experiment", item),
                "Destination experiment absent or foreign: " + item["target_name"],
            )
    control = client.get_experiment_by_name("fraud-history-transfer-control")
    require(
        control is None
        or control.tags == identity_tags(plan, "coordinated", "experiment", "control", {}),
        "Foreign transfer-control experiment",
    )
    mapping = {}
    for store in plan["stores"]:
        for item in store["experiments"]:
            expected = expected_tags(plan, store["tracking_root"], "experiment", item)
            current = client.get_experiment_by_name(item["target_name"])
            if current is None and create:
                identity = client.create_experiment(item["target_name"], tags=expected)
                current = client.get_experiment(identity)
            require(
                current is not None and current.tags == expected,
                "Destination experiment absent or foreign: " + item["target_name"],
            )
            require(
                not create or current.lifecycle_stage == "active",
                "Restore/review deleted target experiment before resuming",
            )
            mapping[store["tracking_root"] + ":" + item["id"]] = current.experiment_id
    return mapping


def find_one(rows, plan, store, kind, identity):
    matches = [
        row
        for row in rows
        if all(
            (row.tags if hasattr(row, "tags") else row.data.tags).get(key) == value
            for key, value in identity_tags(plan, store, kind, identity, {}).items()
            if not key.endswith("source_metadata")
        )
    ]
    require(len(matches) <= 1, "Duplicate destination origin identity")
    return matches[0] if matches else None


def resolve(client, plan, create=False):
    experiments = target_experiments(client, plan, create)
    runs, models = {}, {}
    for store in plan["stores"]:
        name = store["tracking_root"]
        for item in store["runs"]:
            eid = experiments[name + ":" + item["experiment_id"]]
            found = find_one(run_rows(client, eid), plan, name, "run", item["id"])
            if found is None and create:
                found = client.create_run(
                    eid,
                    start_time=item["metadata"]["start_time"],
                    tags=expected_tags(plan, name, "run", item),
                    run_name=item["metadata"].get("name"),
                )
            require(found is not None, "Missing imported run: " + item["id"])
            runs[item["id"]] = found.info.run_id
        for item in store["models"]:
            eid = experiments[name + ":" + item["experiment_id"]]
            found = find_one(model_rows(client, eid), plan, name, "model", item["id"])
            if found is None and create:
                metadata = item["metadata"]
                found = client.create_logged_model(
                    eid,
                    name=metadata["name"],
                    source_run_id=runs[metadata["source_run_id"]],
                    tags=expected_tags(plan, name, "model", item),
                    params=item["params"],
                    model_type=metadata.get("model_type"),
                )
            require(found is not None, "Missing imported logged model: " + item["id"])
            models[item["id"]] = found.model_id
    return {"experiments": experiments, "runs": runs, "models": models}


def mapped_tags(item, plan, store, ids):
    result = expected_tags(plan, store, "run", item)
    if item["metadata"].get("name") is not None:
        result["mlflow.runName"] = item["metadata"]["name"]
    parent = result.get("mlflow.parentRunId")
    if parent:
        require(parent in ids["runs"], "Parent run outside reviewed snapshot")
        result["mlflow.parentRunId"] = ids["runs"][parent]
    uri = result.get("logged_model_uri", "")
    if uri.startswith("models:/"):
        require(uri[8:] in ids["models"], "Logged-model tag outside reviewed snapshot")
        result["logged_model_uri"] = "models:/" + ids["models"][uri[8:]]
    return result


def upload(client, identity, items, snapshot, model=False):
    function = client.log_model_artifact if model else client.log_artifact
    for relative, item in sorted(items.items()):
        path = archive.checked_path(snapshot, Path(item["source"]))
        require(archive.digest(path) == item["sha256"], "Source artifact changed before upload")
        parent = str(PurePosixPath(relative).parent)
        function(identity, str(path), artifact_path=None if parent == "." else parent)


def remote_files(client, identity, model=False):
    result = {}
    listing = client.list_logged_model_artifacts if model else client.list_artifacts

    def visit(prefix):
        for entry in listing(identity, prefix):
            path = PurePosixPath(entry.path)
            require(
                not path.is_absolute() and ".." not in path.parts and str(path) == entry.path,
                "Unexpected remote artifact path",
            )
            if entry.is_dir:
                visit(entry.path)
            else:
                require(entry.path not in result, "Duplicate remote artifact")
                result[entry.path] = entry.file_size

    visit(None)
    return result


def check_files(client, identity, items, uri, model=False):
    from mlflow.artifacts import download_artifacts

    listed = remote_files(client, identity, model)
    expected_sizes = {n: v["bytes"] for n, v in items.items()}
    difference = {
        "identity": identity,
        "kind": "model" if model else "run",
        "missing": sorted(expected_sizes.keys() - listed.keys()),
        "unexpected": sorted(listed.keys() - expected_sizes.keys()),
        "size_differences": [
            {"path": name, "expected": expected_sizes[name], "actual": listed[name]}
            for name in sorted(expected_sizes.keys() & listed.keys())
            if listed[name] != expected_sizes[name]
            and not (listed[name] is None and expected_sizes[name] == 0)
        ],
    }
    require(
        not any(difference[k] for k in ("missing", "unexpected", "size_differences")),
        "Remote artifact set or size differs: " + json.dumps(difference, sort_keys=True),
    )
    if not items:
        return
    with TemporaryDirectory(prefix="history-readback-") as temporary:
        if model:
            directory = Path(
                download_artifacts(
                    artifact_uri=client.get_logged_model(identity).artifact_location,
                    tracking_uri=uri,
                    dst_path=temporary,
                )
            )
        else:
            directory = Path(client.download_artifacts(identity, "", temporary))
        actual = {
            p.relative_to(directory).as_posix(): archive.digest(p)
            for p in directory.rglob("*")
            if p.is_file()
        }
        downloaded_sizes = {
            p.relative_to(directory).as_posix(): p.stat().st_size
            for p in directory.rglob("*")
            if p.is_file()
        }
        require(
            downloaded_sizes == expected_sizes, "Downloaded artifact sizes differ for " + identity
        )
        require(
            actual == {n: v["sha256"] for n, v in items.items()}, "Remote artifact SHA-256 differs"
        )


def log_lineage(client, store, ids):
    datasets = {row["dataset_uuid"]: row for row in store["datasets"]}
    for edge in store["edges"]:
        source, destination = edge["source_id"], edge["destination_id"]
        step = edge.get("step", 0)
        if edge["source_type"] == "RUN":
            existing = client.get_run(ids["runs"][source]).outputs.model_outputs
            matches = [m for m in existing if m.model_id == ids["models"][destination]]
            require(
                not matches or len(matches) == 1 and matches[0].step == step,
                "Existing output edge differs",
            )
            if matches:
                continue
            client.log_outputs(
                ids["runs"][source], [LoggedModelOutput(ids["models"][destination], step)]
            )
        elif edge["source_type"] == "MODEL":
            existing = client.get_run(ids["runs"][destination]).inputs.model_inputs
            if any(m.model_id == ids["models"][source] for m in existing):
                continue
            client.log_inputs(
                ids["runs"][destination], models=[LoggedModelInput(ids["models"][source])]
            )
        else:
            item = datasets[source]
            tags = [
                InputTag(t["name"], t["value"])
                for t in store["input_tags"]
                if t["input_uuid"] == edge["input_uuid"]
            ]
            dataset = Dataset(
                item["name"],
                item["digest"],
                item["dataset_source_type"],
                item["dataset_source"],
                schema=item["dataset_schema"],
                profile=item["dataset_profile"],
            )
            client.log_inputs(ids["runs"][destination], datasets=[DatasetInput(dataset, tags)])


def metric_key(value):
    return value.key, float(value.value).hex(), value.timestamp, value.step


def verify(client, plan, ids, uri):
    histories, file_count = 0, 0
    for store in plan["stores"]:
        name = store["tracking_root"]
        for item in store["runs"]:
            run = client.get_run(ids["runs"][item["id"]])
            require(
                run.data.params == item["params"]
                and run.data.tags == mapped_tags(item, plan, name, ids),
                "Run parameters/tags differ",
            )
            expected = item["metadata"]
            require(
                all(
                    getattr(run.info, key) == expected[key]
                    for key in ("status", "start_time", "end_time", "lifecycle_stage")
                ),
                "Run status/times/lifecycle differ",
            )
            require(
                {k: float(v).hex() for k, v in run.data.metrics.items()} == item["latest"],
                "Latest run metrics differ",
            )
            for key in item["latest"]:
                old = sorted(
                    (m["key"], m["value"], m["timestamp"], m["step"])
                    for m in item["metrics"]
                    if m["key"] == key
                )
                current = sorted(
                    metric_key(m) for m in client.get_metric_history(run.info.run_id, key)
                )
                require(current == old, "Complete metric history differs")
                histories += len(current)
            check_files(client, run.info.run_id, item["artifacts"], uri)
            file_count += len(item["artifacts"])
            expected_outputs = sorted(
                (ids["models"][e["destination_id"]], e.get("step", 0))
                for e in store["edges"]
                if e["source_type"] == "RUN" and e["source_id"] == item["id"]
            )
            require(
                sorted((m.model_id, m.step) for m in run.outputs.model_outputs)
                == expected_outputs,
                "Run model outputs differ",
            )
            expected_inputs = sorted(
                (ids["models"][e["source_id"]], e.get("step", 0))
                for e in store["edges"]
                if e["source_type"] == "MODEL" and e["destination_id"] == item["id"]
            )
            require(
                sorted(m.model_id for m in run.inputs.model_inputs)
                == [identity for identity, _ in expected_inputs],
                "Run model inputs differ",
            )
            datasets = {d["dataset_uuid"]: d for d in store["datasets"]}
            expected_datasets = []
            for edge in store["edges"]:
                if edge["source_type"] != "DATASET" or edge["destination_id"] != item["id"]:
                    continue
                dataset = datasets[edge["source_id"]]
                expected_datasets.append(
                    {
                        "dataset": {
                            "name": dataset["name"],
                            "digest": dataset["digest"],
                            "source_type": dataset["dataset_source_type"],
                            "source": dataset["dataset_source"],
                            "schema": dataset["dataset_schema"] or None,
                            "profile": dataset["dataset_profile"] or None,
                        },
                        "tags": {
                            t["name"]: t["value"]
                            for t in store["input_tags"]
                            if t["input_uuid"] == edge["input_uuid"]
                        },
                    }
                )
            actual_datasets = [
                {"dataset": d.dataset.to_dictionary(), "tags": {t.key: t.value for t in d.tags}}
                for d in run.inputs.dataset_inputs
            ]
            require(
                sorted(map(fingerprint, actual_datasets))
                == sorted(map(fingerprint, expected_datasets)),
                "Dataset lineage contents or tags differ",
            )
        for item in store["models"]:
            current = client.get_logged_model(ids["models"][item["id"]])
            metadata = item["metadata"]
            require(
                current.source_run_id == ids["runs"][metadata["source_run_id"]]
                and current.status == LoggedModelStatus.from_int(metadata["status"])
                and current.params == item["params"]
                and current.tags == expected_tags(plan, name, "model", item)
                and current.name == metadata["name"]
                and (current.model_type or None) == (metadata.get("model_type") or None),
                "Logged model metadata differs",
            )
            expected = sorted(
                (
                    m["key"],
                    m["value"],
                    m["timestamp"],
                    m["step"],
                    ids["runs"][m["run_id"]],
                    m["dataset_name"],
                    m["dataset_digest"],
                )
                for m in item["metrics"]
            )
            actual = sorted(
                (*metric_key(m), m.run_id, m.dataset_name, m.dataset_digest)
                for m in current.metrics or []
            )
            require(actual == expected, "Logged model metric history differs")
            check_files(client, current.model_id, item["artifacts"], uri, model=True)
            file_count += len(item["artifacts"])
    return {
        "runs": len(ids["runs"]),
        "logged_models": len(ids["models"]),
        "metric_history_rows": histories,
        "owned_artifact_files": file_count,
        "metadata_and_full_readback_verified": True,
    }


def control_run(client, plan, create):
    name = "fraud-history-transfer-control"
    tags = identity_tags(plan, "coordinated", "experiment", "control", {})
    experiment = client.get_experiment_by_name(name)
    if experiment is None and create:
        experiment = client.get_experiment(client.create_experiment(name, tags=tags))
    require(
        experiment is not None and experiment.tags == tags,
        "Missing or foreign transfer-control experiment",
    )
    tags = identity_tags(plan, "coordinated", "control", "snapshot-extras", {})
    run = find_one(
        run_rows(client, experiment.experiment_id),
        plan,
        "coordinated",
        "control",
        "snapshot-extras",
    )
    if run is None and create:
        run = client.create_run(
            experiment.experiment_id, tags=tags, run_name="source-snapshot-provenance"
        )
    require(run is not None, "Missing provenance run")
    return run.info.run_id


def execute(client, plan, uri, create=False):
    snapshot = Path(plan["snapshot_directory"])
    ids = resolve(client, plan, create)
    if create:
        for store in plan["stores"]:
            name = store["tracking_root"]
            for item in store["runs"]:
                identity = ids["runs"][item["id"]]
                print("Importing historical run " + item["id"], flush=True)
                if client.get_run(identity).info.lifecycle_stage == "deleted":
                    client.restore_run(identity)
                tags = mapped_tags(item, plan, name, ids)
                metrics = [
                    Metric(m["key"], float.fromhex(m["value"]), m["timestamp"], m["step"])
                    for m in item["metrics"]
                ]
                client.log_batch(
                    identity,
                    params=[Param(k, v) for k, v in item["params"].items()],
                    tags=[RunTag(k, v) for k, v in tags.items()],
                )
                for start in range(0, len(metrics), 500):
                    client.log_batch(identity, metrics=metrics[start : start + 500])
                upload(client, identity, item["artifacts"], snapshot)
            for item in store["models"]:
                identity = ids["models"][item["id"]]
                print("Importing historical logged model " + item["id"], flush=True)
                upload(client, identity, item["artifacts"], snapshot, model=True)
            log_lineage(client, store, ids)
            for item in store["models"]:
                identity = ids["models"][item["id"]]
                for m in item["metrics"]:
                    client.log_batch(
                        ids["runs"][m["run_id"]],
                        metrics=[
                            Metric(
                                m["key"],
                                float.fromhex(m["value"]),
                                m["timestamp"],
                                m["step"],
                                model_id=identity,
                                dataset_name=m["dataset_name"],
                                dataset_digest=m["dataset_digest"],
                            )
                        ],
                    )
                client.finalize_logged_model(
                    identity, LoggedModelStatus.from_int(item["metadata"]["status"])
                )
            for item in store["runs"]:
                identity = ids["runs"][item["id"]]
                if item["metadata"]["end_time"] is None:
                    # A snapshot may contain an interrupted RUNNING run; do not invent an end.
                    client.update_run(identity, status=item["metadata"]["status"])
                else:
                    client.set_terminated(
                        identity, item["metadata"]["status"], end_time=item["metadata"]["end_time"]
                    )
                if item["metadata"]["lifecycle_stage"] == "deleted":
                    client.delete_run(identity)
        extra = control_run(client, plan, True)
        upload(client, extra, plan["extras"], snapshot)
        client.set_terminated(extra)
    summary = verify(client, plan, ids, uri)
    extra = control_run(client, plan, False)
    check_files(client, extra, plan["extras"], uri)
    summary.update(extra_snapshot_files=len(plan["extras"]), provenance_run_id=extra)
    require(
        summary["runs"] == plan["source_counts"]["runs"]
        and summary["logged_models"] == plan["source_counts"]["logged_models"],
        "Imported entity counts differ",
    )
    return {
        "mapping": ids,
        "verification": summary,
        "source_plan_sha256": fingerprint(plan),
        "refit": False,
    }
