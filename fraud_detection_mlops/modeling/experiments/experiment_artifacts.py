"""Hash records and integrity checks shared by experimental workflows."""

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.modeling.contracts.experiment_errors import ExperimentError


def file_records(directory, names):
    return [
        {"path": n, "size_bytes": (directory / n).stat().st_size, "sha256": sha256(directory / n)}
        for n in names
    ]


def check_records(directory, records):
    for item in records:
        path = directory / item["path"]
        if (
            not path.resolve().is_relative_to(directory.resolve())
            or not path.is_file()
            or path.stat().st_size != item["size_bytes"]
            or sha256(path) != item["sha256"]
        ):
            raise ExperimentError(f"Artifact integrity mismatch: {item['path']}")
