"""Standard-library performance reader; no estimator, MLflow or algorithm imports in UI."""

import json
import math

from scripts.fraud_portal import data


def load(directory, expected, portal_manifest_sha, snapshot):
    directory = data.checked_path(directory)
    path = data.checked_path(directory / "manifest.json")
    data.require(data.sha(path) == expected, "Manifest de desempenho alterado")
    manifest = json.loads(path.read_bytes())
    report = data.checked_path(directory / "performance.json")
    data.require(report.stat().st_size <= 4 * 1024 * 1024, "Relatório de desempenho grande demais")
    data.require(data.sha(report) == manifest["report_sha256"], "Desempenho alterado")
    value = json.loads(report.read_bytes())
    data.require(
        manifest["schema"] == "fraud_portal_performance_manifest_v1"
        and value["schema"] == "fraud_portal_performance_v1"
        and value["portal_manifest_sha256"] == portal_manifest_sha
        and value["model_sha256"] == snapshot["model_sha256"]
        and value["serving_release_id"] == snapshot["serving_release_id"]
        and str(value["registry"]["version"]) == str(snapshot["registry_version_at_capture"]),
        "A avaliação pertence a outro snapshot, modelo ou release",
    )
    names = [c["model_id"] for c in value["candidates"] if c["is_snapshot_model"]]
    data.require(
        len(names) == 1 and names[0] == snapshot["model_id"], "Identidade avaliada divergente"
    )
    selected = next(c for c in value["candidates"] if c["is_snapshot_model"])
    data.require(
        selected["model_sha256"] == value["model_sha256"], "Pesos da avaliação divergentes"
    )
    for candidate in value["candidates"]:
        data.require(
            candidate["metrics"]["rows"] == value["dataset"]["rows"],
            "Tamanho de avaliação divergente",
        )
        for curve in candidate["curves"].values():
            data.require(
                len(curve["x"]) == len(curve["y"]) and 2 <= len(curve["x"]) <= 250,
                "Curva incompatível",
            )
            data.require(
                all(
                    type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1
                    for n in curve["x"] + curve["y"]
                ),
                "Pontos da curva inválidos",
            )
        for key in ("average_precision", "roc_auc", "daily_customer_precision_at_100"):
            number = candidate["metrics"][key]
            data.require(
                type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 1,
                "Métrica inválida",
            )
    return value, manifest
