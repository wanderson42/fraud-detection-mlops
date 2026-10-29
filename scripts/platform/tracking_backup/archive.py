"""Private, bounded archives; S3 keys never become local paths."""

import hashlib
import json
from pathlib import Path
import re

from scripts.tracking import capture_worker as shared

SCHEMA = "fraud_kind_tracking_backup_v1"
DATABASE = ROLE = "mlflow_kind"
BUCKET = "fraud-kind-mlflow"
MAX_MANIFEST = 2 * 1024**2
CONTROL_FILES = {
    "postgres.dump",
    "metadata.json",
    "objects.json",
    "control.json",
    "owner-marker.json",
}


def require(ok, message):
    shared.require(ok, message)


def checked(path):
    path = Path(path).absolute()
    require(
        path == path.resolve() and not any(p.is_symlink() for p in [path, *path.parents]),
        "Unsafe archive path",
    )
    return path


def sha(path):
    h = hashlib.sha256()
    with checked(path).open("rb") as stream:
        while chunk := stream.read(1024**2):
            h.update(chunk)
    return h.hexdigest()


def document(path):
    path = checked(path)
    require(
        path.is_file() and path.stat().st_size <= MAX_MANIFEST,
        "Missing/oversized control document",
    )
    return json.loads(path.read_bytes())


def member(name):
    require(
        isinstance(name, str)
        and (name in CONTROL_FILES or re.fullmatch(r"objects/[0-9]{4}\.bin", name)),
        "Unmanaged archive member",
    )
    return name


def verify(directory, digest):
    directory = checked(directory)
    require(
        isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest),
        "Explicit manifest SHA-256 required",
    )
    m = document(directory / "manifest.json")
    require(sha(directory / "manifest.json") == digest, "Manifest digest differs")
    require(
        m.get("schema") == SCHEMA
        and m.get("status") == "captured_verified"
        and m.get("source_recovered") is True,
        "Incomplete backup",
    )
    require(
        m.get("database") == DATABASE
        and m.get("bucket") == BUCKET
        and m.get("capture_id") == directory.name,
        "Backup identity differs",
    )
    records = m["files"]
    require(
        isinstance(records, list) and len(records) <= shared.MAX_OBJECTS + len(CONTROL_FILES),
        "Archive member budget exceeded",
    )
    names = set()
    for record in records:
        name = member(record["file"])
        require(name not in names, "Duplicate archive member")
        names.add(name)
        path = checked(directory / name)
        bound = (
            shared.MAX_DUMP
            if name == "postgres.dump"
            else shared.MAX_BYTES
            if name.startswith("objects/")
            else MAX_MANIFEST
        )
        require(
            path.is_file()
            and type(record["bytes"]) is int
            and 0 <= record["bytes"] <= bound
            and path.stat().st_size == record["bytes"]
            and sha(path) == record["sha256"],
            "Archive member missing or changed",
        )
    actual = set()
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "Symlink in backup")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    require(
        CONTROL_FILES <= names and actual == names | {"manifest.json"},
        "Unexpected/missing backup members",
    )
    objects = document(directory / "objects.json")
    require(
        len(objects) <= shared.MAX_OBJECTS
        and sum(r["bytes"] for r in objects) <= shared.MAX_BYTES,
        "Object budget exceeded",
    )
    require(
        len({r["key"] for r in objects}) == len(objects)
        and len({r["file"] for r in objects}) == len(objects),
        "Duplicate object mapping",
    )
    require({r["file"] for r in objects} == names - CONTROL_FILES, "Object file set differs")
    by_file = {r["file"]: r for r in records}
    for obj in objects:
        require(
            all(obj[k] == by_file[obj["file"]][k] for k in ("bytes", "sha256")),
            "Object map digest differs",
        )
    metadata = document(directory / "metadata.json")
    require(
        metadata.get("database") == DATABASE and metadata.get("role") == ROLE,
        "Database identity differs",
    )
    shared.coverage(metadata, objects)
    with (directory / "postgres.dump").open("rb") as stream:
        require(stream.read(5) == b"PGDMP", "Not a custom PostgreSQL dump")
    control = document(directory / "control.json")
    require(
        re.fullmatch(r"models:/m-[a-f0-9]+", control.get("model_uri", "")),
        "Control logged model missing",
    )
    require(control["model_sha256"] == m["model_sha256"], "Control/model binding differs")
    marker = document(directory / "owner-marker.json")
    require(marker == {"owner_id": m["tracking_owner_id"]}, "Owner marker differs")
    return m


def seal(directory, manifest):
    records = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            name = member(path.relative_to(directory).as_posix())
            records.append({"file": name, "bytes": path.stat().st_size, "sha256": sha(path)})
    shared.save(directory / "manifest.json", {**manifest, "files": records})
    digest = sha(directory / "manifest.json")
    verify(directory, digest)
    return digest
