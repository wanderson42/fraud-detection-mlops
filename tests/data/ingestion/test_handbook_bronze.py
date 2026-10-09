"""Exercise acquisition and failure recovery without external network or pickle loading."""

from copy import deepcopy
import hashlib
from http.client import IncompleteRead
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.data.ingestion import handbook_bronze as bronze
from fraud_detection_mlops.data.ingestion.handbook_download import app


@pytest.fixture
def source(tmp_path, monkeypatch):
    payloads = {
        "2018-04-01.pkl": b"original bytes: first day",
        "2018-04-02.pkl": b"original bytes: second day",
        "2018-04-03.pkl": b"original bytes: third day",
    }
    inventory = {
        "schema_version": 1,
        "source": {"repository": bronze.SOURCE_REPOSITORY, "commit": "a" * 40},
        "files": [
            {
                "date": name.removesuffix(".pkl"),
                "filename": name,
                "source_path": f"data/{name}",
                "size_bytes": len(payload),
                "git_blob_sha1": hashlib.sha1(
                    f"blob {len(payload)}\0".encode() + payload
                ).hexdigest(),
            }
            for name, payload in payloads.items()
        ],
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory))
    calls = []

    def fetch(request, timeout):
        calls.append((request.full_url, timeout))
        return BytesIO(payloads[request.full_url.rsplit("/", 1)[-1]])

    monkeypatch.setattr(bronze, "urlopen", fetch)
    monkeypatch.setattr(bronze.time, "sleep", lambda _: None)
    return {
        "payloads": payloads,
        "inventory": inventory,
        "inventory_path": inventory_path,
        "root": tmp_path / "bronze",
        "snapshot": tmp_path / "bronze" / ("a" * 40),
        "calls": calls,
        "fetch": fetch,
    }


def extract(source, **kwargs):
    return bronze.extract_bronze(source["root"], inventory_path=source["inventory_path"], **kwargs)


def verify(source, **kwargs):
    return bronze.verify_bronze(source["root"], inventory_path=source["inventory_path"], **kwargs)


def manifest(source):
    return json.loads((source["snapshot"] / "manifest.json").read_text())


def audits(source):
    return [json.loads(path.read_text()) for path in (source["snapshot"] / "runs").glob("*.json")]


def no_network(*args, **kwargs):
    pytest.fail("Unexpected network access")


def test_original_bytes_pinned_provenance_checksums_and_success_audit(source):
    result = extract(source)
    assert result["downloaded_count"] == 3
    assert result["skipped_count"] == 0
    assert result["status"] == "success"
    assert result["started_at"] <= result["finished_at"]
    assert (
        result["inventory_sha256"]
        == hashlib.sha256(source["inventory_path"].read_bytes()).hexdigest()
    )
    stored = manifest(source)
    assert stored["complete"] is True
    for record in stored["files"]:
        payload = source["payloads"][record["filename"]]
        assert (source["snapshot"] / record["filename"]).read_bytes() == payload
        assert record["sha256"] == hashlib.sha256(payload).hexdigest()
        assert f"/{'a' * 40}/data/" in record["source_url"]
    assert len(source["calls"]) == 3
    assert verify(source, require_complete=True)["verified_file_count"] == 3


def test_repeated_extraction_is_offline_and_retains_original_metadata(source, monkeypatch):
    first = extract(source)
    original = manifest(source)
    monkeypatch.setattr(bronze, "urlopen", no_network)
    second = extract(source)
    assert second["downloaded_count"] == 0
    assert second["skipped_count"] == 3
    assert first["run_id"] != second["run_id"]
    assert manifest(source) == original
    assert len(audits(source)) == 2
    assert verify(source)["complete"] is True


def test_partial_snapshot_can_expand_without_duplicate_records(source):
    extract(source, start_date="2018-04-02", end_date="2018-04-02")
    assert verify(source)["verified_file_count"] == 1
    assert verify(source)["complete"] is False
    with pytest.raises(bronze.BronzeError, match="incomplete"):
        verify(source, require_complete=True)
    result = extract(source)
    assert (result["downloaded_count"], result["skipped_count"]) == (2, 1)
    assert len(manifest(source)["files"]) == 3


@pytest.mark.parametrize("payload", [b"short", b"x" * 24, b"x" * 100])
def test_untrusted_response_is_not_promoted_to_bronze(source, monkeypatch, payload):
    monkeypatch.setattr(bronze, "urlopen", lambda *args, **kwargs: BytesIO(payload))
    with pytest.raises(bronze.BronzeError):
        extract(source)
    assert not list(source["snapshot"].glob("*.pkl"))
    assert not list(source["snapshot"].glob("*.part"))
    assert not (source["snapshot"] / "manifest.json").exists()
    assert audits(source)[0]["status"] == "failed"


def test_failed_second_file_is_audited_and_resume_keeps_first_file(source, monkeypatch):
    def failing_fetch(request, timeout):
        if request.full_url.endswith("2018-04-02.pkl"):
            raise HTTPError(request.full_url, 404, "Not Found", None, None)
        return source["fetch"](request, timeout)

    monkeypatch.setattr(bronze, "urlopen", failing_fetch)
    with pytest.raises(bronze.BronzeError, match="2018-04-02"):
        extract(source)
    failed = audits(source)[0]
    assert failed["status"] == "failed"
    assert failed["downloaded_count"] == 1
    assert manifest(source)["stored_file_count"] == 1
    assert verify(source)["complete"] is False
    monkeypatch.setattr(bronze, "urlopen", source["fetch"])
    result = extract(source)
    assert (result["downloaded_count"], result["skipped_count"]) == (2, 1)


@pytest.mark.parametrize("error", [503, 429, "network", "partial"])
def test_transient_failure_retries_then_succeeds(source, monkeypatch, error):
    calls = []

    def unstable_fetch(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            if error == "network":
                raise URLError("network unavailable")
            if error == "partial":
                raise IncompleteRead(b"partial", 10)
            raise HTTPError(request.full_url, error, "temporary", None, None)
        return source["fetch"](request, timeout)

    monkeypatch.setattr(bronze, "urlopen", unstable_fetch)
    extract(source, end_date="2018-04-01")
    assert len(calls) == 2
    assert verify(source)["verified_file_count"] == 1


@pytest.mark.parametrize("status, expected_calls", [(404, 1), (503, 3)])
def test_http_failures_have_bounded_attempts(source, monkeypatch, status, expected_calls):
    calls = []

    def always_fail(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, status, "failure", None, None)

    monkeypatch.setattr(bronze, "urlopen", always_fail)
    with pytest.raises(bronze.BronzeError, match="Download failed"):
        extract(source, attempts=3)
    assert len(calls) == expected_calls
    assert audits(source)[0]["status"] == "failed"


def test_existing_corruption_is_detected_without_overwriting(source, monkeypatch):
    extract(source)
    path = source["snapshot"] / "2018-04-01.pkl"
    path.write_bytes(b"corrupted")
    monkeypatch.setattr(bronze, "urlopen", no_network)
    with pytest.raises(bronze.BronzeError, match="Integrity check failed"):
        extract(source, start_date="2018-04-03", end_date="2018-04-03")
    with pytest.raises(bronze.BronzeError, match="Integrity check failed"):
        verify(source)
    assert path.read_bytes() == b"corrupted"


def test_missing_file_downgrades_coverage_even_if_redownload_fails(source, monkeypatch):
    extract(source)
    (source["snapshot"] / "2018-04-03.pkl").unlink()
    monkeypatch.setattr(
        bronze, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline"))
    )
    with pytest.raises(bronze.BronzeError, match="Download failed"):
        extract(source)
    assert manifest(source)["complete"] is False
    assert manifest(source)["stored_file_count"] == 2
    assert verify(source)["verified_file_count"] == 2


def test_download_promoted_before_manifest_failure_can_be_recovered(source, monkeypatch):
    original_write = bronze.write_json

    def fail_manifest(path, value):
        if path.name == "manifest.json":
            raise OSError("disk failure")
        return original_write(path, value)

    monkeypatch.setattr(bronze, "write_json", fail_manifest)
    with pytest.raises(bronze.BronzeError, match="disk failure"):
        extract(source, end_date="2018-04-01")
    assert (source["snapshot"] / "2018-04-01.pkl").is_file()
    monkeypatch.setattr(bronze, "write_json", original_write)
    monkeypatch.setattr(bronze, "urlopen", no_network)
    result = extract(source, end_date="2018-04-01")
    assert result["skipped_count"] == 1
    assert verify(source)["verified_file_count"] == 1


@pytest.mark.parametrize("field", ["sha256", "git_blob_sha1", "source_url", "filename"])
def test_manifest_tampering_is_rejected_offline(source, monkeypatch, field):
    extract(source)
    value = manifest(source)
    value["files"][0][field] = "0" * 64 if field == "sha256" else ["invalid"]
    (source["snapshot"] / "manifest.json").write_text(json.dumps(value))
    monkeypatch.setattr(bronze, "urlopen", no_network)
    with pytest.raises(bronze.BronzeError):
        verify(source)
    with pytest.raises(bronze.BronzeError):
        extract(source)


def test_unknown_files_and_missing_manifested_files_are_rejected(source):
    extract(source)
    extra = source["snapshot"] / "unknown.pkl"
    extra.write_bytes(b"unknown")
    with pytest.raises(bronze.BronzeError, match="untracked"):
        verify(source)
    with pytest.raises(bronze.BronzeError, match="Untracked"):
        extract(source)
    extra.unlink()
    (source["snapshot"] / "2018-04-01.pkl").unlink()
    with pytest.raises(bronze.BronzeError, match="missing"):
        verify(source)


def test_lock_prevents_concurrent_mutation_and_is_not_removed(source, monkeypatch):
    source["snapshot"].mkdir(parents=True)
    lock = source["snapshot"] / ".extract.lock"
    lock.write_text("another run")
    monkeypatch.setattr(bronze, "urlopen", no_network)
    with pytest.raises(bronze.BronzeError, match="locked"):
        extract(source)
    assert lock.read_text() == "another run"


@pytest.mark.parametrize(
    "options",
    [
        {"start_date": "invalid"},
        {"start_date": "2018-04-04"},
        {"end_date": "2018-03-31"},
        {"start_date": "2018-04-03", "end_date": "2018-04-01"},
        {"attempts": 0},
        {"timeout": 0},
    ],
)
def test_invalid_requests_fail_before_output_or_network(source, monkeypatch, options):
    monkeypatch.setattr(bronze, "urlopen", no_network)
    with pytest.raises(bronze.BronzeError):
        extract(source, **options)
    assert not source["root"].exists()


@pytest.mark.parametrize("change", ["branch", "traversal", "gap", "duplicate"])
def test_invalid_inventory_rejected_before_network(source, monkeypatch, change):
    value = deepcopy(source["inventory"])
    if change == "branch":
        value["source"]["commit"] = "main"
    elif change == "traversal":
        value["files"][0]["filename"] = "../file.pkl"
    elif change == "gap":
        value["files"].pop(1)
    else:
        value["files"].append(value["files"][0])
    source["inventory_path"].write_text(json.dumps(value))
    monkeypatch.setattr(bronze, "urlopen", no_network)
    with pytest.raises(bronze.BronzeError, match="Invalid source inventory"):
        extract(source)
    assert not source["root"].exists()


def test_checked_in_source_inventory_has_expected_coverage():
    inventory = bronze.load_inventory()
    assert inventory["source"]["commit"] == "6e67dbd0a3bfe0d7ec33abc4bce5f37cd4ff0d6a"
    assert len(inventory["files"]) == 183
    assert inventory["files"][0]["date"] == "2018-04-01"
    assert inventory["files"][-1]["date"] == "2018-09-30"
    assert sum(item["size_bytes"] for item in inventory["files"]) == 107121710


def test_cli_acquisition_verification_and_failure_exit_status(source, monkeypatch):
    runner = CliRunner()
    options = ["--output-root", str(source["root"]), "--inventory", str(source["inventory_path"])]
    result = runner.invoke(app, ["extract", *options, "--end-date", "2018-04-01"])
    assert result.exit_code == 0, result.output
    assert "Downloaded: 1; skipped: 0" in result.output
    monkeypatch.setattr(bronze, "urlopen", no_network)
    result = runner.invoke(app, ["verify", *options])
    assert result.exit_code == 0, result.output
    assert "Verified: 1/3; complete: False" in result.output
    result = runner.invoke(app, ["verify", *options, "--require-complete"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["extract", *options, "--start-date", "invalid"])
    assert result.exit_code == 1


def test_different_source_commit_creates_separate_snapshot(source):
    extract(source)
    first_manifest = manifest(source)
    value = deepcopy(source["inventory"])
    value["source"]["commit"] = "b" * 40
    source["inventory_path"].write_text(json.dumps(value))
    result = extract(source)
    assert result["downloaded_count"] == 3
    assert Path(result["snapshot_path"]).name == "b" * 40
    assert manifest(source) == first_manifest
    assert verify(source, require_complete=True)["complete"] is True


def test_interruption_is_audited_and_releases_lock(source, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bronze, "urlopen", interrupted)
    with pytest.raises(KeyboardInterrupt):
        extract(source)
    assert audits(source)[0]["status"] == "interrupted"
    assert not (source["snapshot"] / ".extract.lock").exists()


def test_midstream_timeout_removes_partial_download_before_retry(source, monkeypatch):
    payload = source["payloads"]["2018-04-01.pkl"]

    class InterruptedStream(BytesIO):
        def read(self, size=-1):
            if self.tell() > 0:
                raise TimeoutError("stream interrupted")
            return super().read(5)

    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            return InterruptedStream(payload)
        assert not list(source["snapshot"].glob("*.part"))
        return BytesIO(payload)

    monkeypatch.setattr(bronze, "urlopen", fetch)
    extract(source, end_date="2018-04-01")
    assert len(calls) == 2
    assert verify(source)["verified_file_count"] == 1
    assert not list(source["snapshot"].glob("*.part"))
