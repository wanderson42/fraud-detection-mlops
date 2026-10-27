"""Explicit read-only access binding; safe in the isolated Streamlit environment."""

import copy
import json
from pathlib import Path
import re

from scripts.fraud_portal import data
from scripts.product_session import reader

ROOT = Path(__file__).resolve().parents[2]


def path_for(root, portal_id):
    data.require(
        bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", portal_id)), "ID do portal inválido"
    )
    return data.checked_path(
        root / "data/storage/kind-platform/portal" / (portal_id + "-session.json")
    )


def load(path, expected, *, root=ROOT):
    path = data.checked_path(Path(path))
    data.require(data.sha(path) == expected, "Vínculo da sessão alterado; reabra o portal")
    link = json.loads(path.read_bytes())
    data.require(
        isinstance(link, dict)
        and set(link)
        == {
            "schema",
            "portal_id",
            "session_id",
            "directory",
            "session_sha256",
            "input_sha256",
            "model_sha256",
            "serving_release_id",
        }
        and link["schema"] == "fraud_portal_session_access_v1",
        "Vínculo da sessão inválido",
    )
    data.require(path == path_for(root, link["portal_id"]), "Vínculo fora do portal esperado")
    data.require(
        isinstance(link["session_id"], str)
        and bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", link["session_id"])),
        "ID da sessão inválido",
    )
    directory = data.checked_path(Path(link["directory"]))
    data.require(
        directory
        == root / "data/storage/kind-platform/product-flow" / ("session-" + link["session_id"]),
        "Diretório da sessão divergente",
    )
    for name in ("session_sha256", "input_sha256", "model_sha256"):
        data.require(
            isinstance(link[name], str) and bool(re.fullmatch(r"[a-f0-9]{64}", link[name])),
            "Digest da sessão inválido",
        )
    data.require(
        isinstance(link["serving_release_id"], str)
        and bool(re.fullmatch(r"[a-f0-9]{32}", link["serving_release_id"])),
        "Release da sessão inválida",
    )
    receipt = data.checked_path(directory / "session.json")
    source = data.checked_path(directory / "input.json")
    data.require(
        data.sha(receipt) == link["session_sha256"] and data.sha(source) == link["input_sha256"],
        "Fonte da sessão alterada",
    )
    prepared = json.loads(receipt.read_bytes())
    value = json.loads(source.read_bytes())
    data.require(
        prepared["session_id"] == link["session_id"]
        and prepared["input_sha256"] == link["input_sha256"],
        "Recibo pertence a outra sessão",
    )
    data.require(
        all(
            value["release_manifest"][name] == link[name]
            for name in ("model_sha256", "serving_release_id")
        ),
        "Modelo/release não corresponde ao vínculo",
    )
    return link


def page(path, expected, *, root=ROOT, **options):
    link = load(path, expected, root=root)
    value = reader.read(Path(link["directory"]), link["session_sha256"], **options)
    data.require(
        all(
            value[name] == link[name]
            for name in ("session_id", "model_sha256", "serving_release_id")
        ),
        "Checkpoint pertence a outro vínculo",
    )
    return value


def explainable(cases, snapshot, manifest_sha256):
    """Reuse verified SHAP only for byte-equivalent request/response identities."""
    originals = {c["request"]["transaction_id"]: c for c in snapshot["cases"]} if snapshot else {}
    result = []
    for case in cases:
        current = copy.deepcopy(case)
        current["SHAP_explanation"] = None
        previous = originals.get(case["request"]["transaction_id"])
        if previous and previous.get("SHAP_explanation"):
            exact = all(
                data.encoded(previous[name]) == data.encoded(case[name])
                for name in ("request", "response")
            )
            explanation = previous["SHAP_explanation"]
            identity = all(
                explanation[name] == case["response"][name]
                for name in ("model_sha256", "serving_release_id")
            )
            if exact and identity:
                current["SHAP_explanation"] = copy.deepcopy(explanation)
                current["explanation_origin"] = {
                    "portal_manifest_sha256": manifest_sha256,
                    "exact_request_and_response_verified": True,
                }
        result.append(current)
    return result
