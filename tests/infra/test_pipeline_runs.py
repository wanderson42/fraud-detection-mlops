"""Resume actual synthetic datasets with a simulated Kubernetes boundary."""

import copy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from scripts.platform import kind_pipeline as k
from scripts.platform import pipeline_runs as runs
from scripts.storage import bronze_s3_worker as transport
from tests.infra.test_kind_pipeline_workers import (  # noqa: F401
    OWNER,
    RUN,
    chain,
    data_source,
    prepared,
)
import yaml

from fraud_detection_mlops.storage import managed_paths

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scenario(chain, monkeypatch):  # noqa: F811
    root, published, store = chain
    monkeypatch.setattr(k.f, "ROOT", root)
    monkeypatch.setattr(managed_paths, "DATA_DIR", root / "data")
    target = root / "scripts/platform/kind_pipeline_compute.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/platform/kind_pipeline_compute.py", target)
    monkeypatch.setattr(runs, "pins", lambda: {"host": "a" * 64})
    monkeypatch.setattr(runs, "committed", runs.pins)
    image_record = {"images": {key: {"image": key + "-image"} for key in ("science", "transport")}}
    k.work().mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(k, "initialize", lambda: {"owner_id": OWNER})
    monkeypatch.setattr(k, "images", lambda: copy.deepcopy(image_record))
    monkeypatch.setattr(k.f, "inventory", lambda: {"node": "same", "pvcs": "same"})
    monkeypatch.setattr(runs.datasets, "initialize", lambda: {"owner_id": OWNER})
    monkeypatch.setattr(
        runs.datasets,
        "available",
        lambda: {
            "bundles": [{"layer": "bronze", "bundle_id": published["silver"]["input_bundle_id"]}]
        },
    )
    state = SimpleNamespace(
        helm_values=None,
        jobs={},
        installed=False,
        dispatches=[],
        restores=[],
        published=published,
        store=store,
        root=root,
    )

    def rendered(path, release):
        values = json.loads(Path(path).read_text())
        result = []
        for row in values["jobs"]:
            stage = row["stage"]
            result.append(
                {
                    "kind": "Job",
                    "metadata": {
                        "name": release + "-" + stage,
                        "labels": {"app.kubernetes.io/instance": release},
                        "annotations": {
                            "fraud.pipeline/run-id": RUN,
                            "fraud.pipeline/owner-id": OWNER,
                        },
                    },
                    "spec": {
                        "backoffLimit": 0,
                        "template": {
                            "spec": {
                                "initContainers": [
                                    {
                                        "name": "download",
                                        "image": "transport-image",
                                        "args": [stage, row["inputBundleId"]],
                                    }
                                ],
                                "containers": [{"name": "publish", "image": "transport-image"}],
                            }
                        },
                    },
                }
            )
        return result

    def run(argv, **kwargs):
        assert argv[:2] == ["helm", "template"]
        return SimpleNamespace(
            stdout=yaml.safe_dump_all(rendered(argv[argv.index("--values") + 1], argv[2]))
        )

    def helm(*args, **kwargs):
        if args[0] == "get":
            assert args[1] == "values"
            return SimpleNamespace(stdout=json.dumps(state.helm_values))
        if args[0] == "upgrade":
            state.helm_values = json.loads(Path(args[args.index("--values") + 1]).read_text())
            state.installed = True
            for job in rendered(args[args.index("--values") + 1], args[2]):
                name = job["metadata"]["name"]
                if name not in state.jobs:
                    job["metadata"]["uid"] = "uid-" + name
                    state.jobs[name] = job
                    state.dispatches.append(name)
        else:
            assert args[0] == "uninstall"
            state.jobs.clear()
            state.installed = False

    def kube(*args, **kwargs):
        if args[:2] == ("get", "job"):
            value = state.jobs.get(args[2])
            return SimpleNamespace(stdout=json.dumps(value) if value else "")
        if args[:2] == ("get", "jobs,pods"):
            return SimpleNamespace(stdout=json.dumps({"items": list(state.jobs.values())}))
        if args[:2] == ("get", "secrets"):
            return SimpleNamespace(stdout=json.dumps({"items": [{}] if state.installed else []}))
        assert args[0] == "logs"
        return SimpleNamespace(stdout=json.dumps(published[args[1].rsplit("-", 1)[1]]))

    def inventory(release, stage, *args):
        job = state.jobs[release + "-" + stage]
        return {
            "job": job["metadata"]["name"],
            "job_uid": job["metadata"]["uid"],
            "pod": release + "-" + stage,
            "pod_uid": "pod-" + stage,
            "images": {},
        }

    def restore(action, output_id, stage):
        assert action == "restore"
        state.restores.append(stage)
        attempt = runs.datasets.work() / ("restore-" + str(len(state.restores)))
        attempt.mkdir(parents=True)
        downloaded = transport.download(attempt, output_id, store, runs.datasets.LAYERS[stage])
        directory = attempt / downloaded["candidate"]
        _, _, verified = runs.datasets.verify_directory(
            directory, output_id, runs.datasets.LAYERS[stage]
        )
        destination = attempt / "restored"
        directory.rename(destination)
        return {"restored_directory": str(destination), "semantic_verification": verified}

    monkeypatch.setattr(k.f, "run", run)
    monkeypatch.setattr(k.f, "helm", helm)
    monkeypatch.setattr(k.f, "kube", kube)
    monkeypatch.setattr(k, "await_job", lambda name: None)
    monkeypatch.setattr(k, "job_inventory", inventory)
    monkeypatch.setattr(k, "wait_removed", lambda release: None)
    monkeypatch.setattr(runs.datasets, "transfer", restore)
    state.plan = runs.prepare(RUN)
    state.directory = runs.directory(RUN)
    return state


def test_plan_precedes_jobs_and_repeated_prepare_keeps_identity(scenario):
    assert scenario.dispatches == []
    assert (scenario.directory / "plan.json").stat().st_mode & 0o077 == 0
    assert runs.prepare(RUN) == scenario.plan
    with pytest.raises(ValueError, match="another Bronze"):
        runs.prepare(RUN, "f" * 64)


def test_resume_between_silver_and_gold_does_not_recompute_silver(scenario):
    assert runs.execute(RUN, stop_after="silver")["status"] == "pipeline_run_paused"
    silver_receipt = (scenario.directory / "stage-silver.json").read_bytes()
    result = runs.execute(RUN)
    assert result["status"] == "success" and result["temporary_jobs_removed"]
    assert scenario.dispatches == [scenario.plan["release"] + "-" + stage for stage in runs.STAGES]
    assert scenario.restores == ["silver", "gold"]
    assert (scenario.directory / "stage-silver.json").read_bytes() == silver_receipt
    assert (
        result["stages"]["gold"]["result"]["input_bundle_id"]
        == result["stages"]["silver"]["result"]["output_bundle_id"]
    )
    assert runs.execute(RUN) == result
    assert scenario.restores == ["silver", "gold"]


def test_controller_interruption_leaves_job_and_resume_attaches_same_uid(scenario, monkeypatch):
    def interrupted(name):
        raise KeyboardInterrupt("controller stopped while Job continues")

    monkeypatch.setattr(k, "await_job", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runs.execute(RUN)
    saved_uid = runs.load(scenario.directory / "job-silver.json")["uid"]
    assert len(scenario.jobs) == 1
    monkeypatch.setattr(k, "await_job", lambda name: None)
    runs.execute(RUN)
    assert runs.load(scenario.directory / "job-silver.json")["uid"] == saved_uid
    assert len(scenario.dispatches) == 2


def test_finished_gold_without_local_receipt_resumes_from_same_job(scenario, monkeypatch):
    seal = runs.seal
    failed = []

    def fail_once(path, value):
        if path.name == "stage-gold.json" and not failed:
            failed.append(True)
            raise KeyboardInterrupt("host died before Gold receipt")
        return seal(path, value)

    monkeypatch.setattr(runs, "seal", fail_once)
    with pytest.raises(KeyboardInterrupt):
        runs.execute(RUN)
    assert len(scenario.dispatches) == 2
    assert runs.execute(RUN)["status"] == "success"
    assert len(scenario.dispatches) == 2
    assert scenario.restores == ["silver", "gold", "gold"]


def test_gold_complete_before_final_result_requires_only_cleanup(scenario):
    runs.execute(RUN, stop_after="gold")
    assert not (scenario.directory / "result.json").exists()
    runs.execute(RUN)
    assert len(scenario.dispatches) == 2 and scenario.restores == ["silver", "gold"]


def test_cleanup_interruption_resumes_without_recreating_jobs(scenario, monkeypatch):
    helm = k.f.helm

    def stop_after_delete(*args, **kwargs):
        result = helm(*args, **kwargs)
        if args[0] == "uninstall":
            raise KeyboardInterrupt("after uninstall before result receipt")
        return result

    monkeypatch.setattr(k.f, "helm", stop_after_delete)
    with pytest.raises(KeyboardInterrupt):
        runs.execute(RUN)
    assert not scenario.jobs
    monkeypatch.setattr(k.f, "helm", helm)
    assert runs.execute(RUN)["status"] == "success"
    assert len(scenario.dispatches) == 2


@pytest.mark.parametrize("fault", ["uid", "input", "owner"])
def test_changed_running_job_is_refused_without_recreation(scenario, monkeypatch, fault):
    monkeypatch.setattr(k, "await_job", lambda name: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        runs.execute(RUN)
    job = next(iter(scenario.jobs.values()))
    if fault == "uid":
        job["metadata"]["uid"] = "replacement"
    elif fault == "owner":
        job["metadata"]["annotations"]["fraud.pipeline/owner-id"] = "foreign"
    else:
        job["spec"]["template"]["spec"]["initContainers"][0]["args"][1] = "0" * 64
    with pytest.raises(ValueError, match="differs"):
        runs.execute(RUN)
    assert len(scenario.dispatches) == 1


def test_dispatched_but_missing_job_is_ambiguous_and_never_recreated(scenario, monkeypatch):
    monkeypatch.setattr(
        k.f, "helm", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        runs.execute(RUN)
    with pytest.raises(ValueError, match="ambiguous"):
        runs.execute(RUN)
    assert scenario.dispatches == []


def test_corrupt_saved_output_is_rejected_before_starting_gold(scenario):
    runs.execute(RUN, stop_after="silver")
    record = runs.load(scenario.directory / "stage-silver.json")
    directory = Path(record["independent_restore"]["restored_directory"])
    payload = next(directory.rglob("*.parquet"))
    payload.write_bytes(payload.read_bytes() + b"corrupt")
    with pytest.raises(ValueError):
        runs.execute(RUN)
    assert len(scenario.dispatches) == 1


def test_second_invocation_cannot_enter_same_run(scenario):
    with runs.locked(RUN), pytest.raises(ValueError, match="Another invocation"):
        runs.execute(RUN)
    assert scenario.dispatches == []


def test_failed_job_is_not_recreated_or_reported_success(scenario, monkeypatch):
    monkeypatch.setattr(
        k, "await_job", lambda name: (_ for _ in ()).throw(ValueError("Job failed"))
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="Job failed"):
            runs.execute(RUN)
    assert len(scenario.dispatches) == 1
    assert not (scenario.directory / "result.json").exists()


def test_round_resume_after_pipeline_complete_before_round_receipt(scenario, monkeypatch):
    from scripts.operational_round import controller as round_controller

    folder = scenario.directory / "round-fixture"
    folder.mkdir(mode=0o700)
    monkeypatch.setattr(round_controller, "work", lambda key: folder)
    monkeypatch.setattr(
        round_controller,
        "read",
        lambda key: {
            "owner_id": RUN,
            "source_pins": {"scripts/platform/pipeline_runs.py": "a" * 64},
        },
    )
    monkeypatch.setattr(round_controller.kind_pipeline, "build", lambda: None)
    monkeypatch.setattr(round_controller, "verified_gold", lambda key: None)
    save = round_controller.save
    monkeypatch.setattr(
        round_controller, "save", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        round_controller.data("round-fixture")
    assert (scenario.directory / "result.json").exists()
    monkeypatch.setattr(round_controller, "save", save)
    assert round_controller.data("round-fixture")["status"] == "success"
    assert len(scenario.dispatches) == 2
    assert round_controller.data("round-fixture")["status"] == "success"
    assert scenario.restores == ["silver", "gold"]


def test_incomplete_legacy_round_cannot_silently_replace_old_attempt(scenario, monkeypatch):
    from scripts.operational_round import controller as round_controller

    monkeypatch.setattr(round_controller, "work", lambda key: scenario.directory)
    monkeypatch.setattr(round_controller, "read", lambda key: {"source_pins": {}})
    monkeypatch.setattr(round_controller.kind_pipeline, "build", lambda: None)
    with pytest.raises(ValueError, match="Legacy incomplete"):
        round_controller.data("old-round")
    assert scenario.dispatches == []


def test_run_lock_excludes_a_second_process(scenario):
    import subprocess
    import sys

    code = """import fcntl,sys
with open(sys.argv[1], "a") as stream:
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(23)
raise SystemExit(0)
"""
    target = scenario.directory / "operation.lock"
    with runs.locked(RUN):
        result = subprocess.run([sys.executable, "-c", code, str(target)], check=False, timeout=10)
        assert result.returncode == 23
    result = subprocess.run([sys.executable, "-c", code, str(target)], check=False, timeout=10)
    assert result.returncode == 0


@pytest.mark.parametrize("stage", ["silver", "gold"])
def test_foreign_helm_values_block_upgrade_or_cleanup(scenario, stage):
    runs.execute(RUN, stop_after=stage)
    scenario.helm_values["ownerId"] = "foreign"
    with pytest.raises(ValueError, match="Helm release values differ"):
        runs.execute(RUN)
    assert len(scenario.dispatches) == (1 if stage == "silver" else 2)
    assert scenario.jobs and not (scenario.directory / "result.json").exists()


def test_changed_host_sources_refuse_resume_before_mutation(scenario, monkeypatch):
    runs.execute(RUN, stop_after="silver")
    monkeypatch.setattr(runs, "pins", lambda: {"host": "f" * 64})
    with pytest.raises(ValueError, match="source/image identity"):
        runs.execute(RUN)
    assert len(scenario.dispatches) == 1


def test_incomplete_temporary_write_never_becomes_a_receipt(scenario, monkeypatch):
    target = scenario.directory / "atomic-fixture.json"
    link = runs.os.link
    monkeypatch.setattr(
        runs.os,
        "link",
        lambda *args: (_ for _ in ()).throw(OSError("injected publication failure")),
    )
    with pytest.raises(OSError, match="publication failure"):
        runs.seal(target, {"status": "complete"})
    assert not target.exists()
    monkeypatch.setattr(runs.os, "link", link)
    runs.seal(target, {"status": "complete"})
    assert runs.load(target) == {"status": "complete"}


def test_round_data_receipt_uses_atomic_publication(scenario, monkeypatch):
    from scripts.operational_round import controller as round_controller

    monkeypatch.setattr(round_controller, "work", lambda key: scenario.directory)

    def forbidden(*args):
        pytest.fail("data receipt must not be written directly")

    monkeypatch.setattr(round_controller.f, "private_json", forbidden)
    assert round_controller.save("unit", "data.json", {"status": "success"}) == {
        "status": "success"
    }
    assert runs.load(scenario.directory / "data.json")["status"] == "success"
