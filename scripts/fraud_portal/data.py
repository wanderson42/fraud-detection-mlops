"""Standard-library verified snapshot reader, filters and investigation summaries."""

import hashlib
import json
import math
from pathlib import Path
import re


class PortalError(ValueError):
    """The selected source cannot be presented as a verified investigation snapshot."""


def require(condition, message):
    if not condition:
        raise PortalError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        + "\n"
    ).encode()


def checked_path(path):
    path = Path(path).absolute()
    require(
        path.resolve() == path and ".." not in path.parts, "Caminho redirecionado não permitido"
    )
    return path


def load_bundle(directory, expected):
    directory = checked_path(directory)
    manifest_path = checked_path(directory / "manifest.json")
    require(
        isinstance(expected, str)
        and re.fullmatch(r"[a-f0-9]{64}", expected)
        and sha(manifest_path) == expected,
        "O manifest do portal difere do digest configurado",
    )
    manifest = json.loads(manifest_path.read_bytes())
    require(
        manifest["schema"] == "fraud_investigation_portal_v1", "Formato de snapshot desconhecido"
    )
    path = checked_path(directory / "snapshot.json")
    require(
        path.stat().st_size <= 32 * 1024 * 1024 and sha(path) == manifest["snapshot_sha256"],
        "O snapshot foi alterado ou excede o tamanho permitido",
    )
    value = json.loads(path.read_bytes())
    require(
        value["schema"] == "fraud_investigation_snapshot_v1" and 1 <= len(value["cases"]) <= 10000,
        "Snapshot sem decisões ou fora do limite",
    )
    ids = set()
    for case in value["cases"]:
        request, response = case["request"], case["response"]
        tid = request["transaction_id"]
        require(
            type(tid) is int and tid not in ids and response["transaction_id"] == tid,
            "Identidade de transação inválida ou repetida",
        )
        ids.add(tid)
        require(
            response["model_sha256"] == value["model_sha256"]
            and response["serving_release_id"] == value["serving_release_id"]
            and isinstance(response["score"], (int, float))
            and math.isfinite(response["score"])
            and 0 <= response["score"] <= 1,
            "Modelo, release ou score divergente",
        )
        explanation = case.get("SHAP_explanation")
        if explanation:
            require(
                explanation["model_sha256"] == value["model_sha256"]
                and explanation["serving_release_id"] == value["serving_release_id"],
                "Explicação identifica outro modelo",
            )
    require(
        manifest["model_sha256"] == value["model_sha256"]
        and manifest["serving_release_id"] == value["serving_release_id"]
        and manifest["case_count"] == len(value["cases"]),
        "Vínculo do snapshot divergente",
    )
    return value, manifest


def status_name(status):
    return {
        "alert": "Alerta gerado",
        "below_threshold": "Abaixo do limiar",
        "already_alerted": "Cliente já alertado",
        "budget_exhausted": "Orçamento esgotado",
    }.get(status, status.replace("_", " ").capitalize())


def summary(cases):
    return {
        "decisions": len(cases),
        "customers": len({c["request"]["customer_id"] for c in cases}),
        "alerts": sum(c["triage"]["status"] == "alert" for c in cases),
        "feedback": sum(c["transaction_feedback"] is not None for c in cases),
        "explanations": sum(bool(c.get("SHAP_explanation")) for c in cases),
    }


def select(cases, *, statuses=(), customer=None, transaction="", minimum_score=0.0):
    """Presentation filters preserve policy decisions; no new risk classification."""
    transaction = transaction.strip()
    require(
        not transaction or re.fullmatch(r"[0-9]{1,19}", transaction),
        "Use um ID numérico de transação",
    )
    return sorted(
        [
            c
            for c in cases
            if (not statuses or c["triage"]["status"] in statuses)
            and (customer is None or c["request"]["customer_id"] == customer)
            and (not transaction or c["request"]["transaction_id"] == int(transaction))
            and c["response"]["score"] >= minimum_score
        ],
        key=lambda c: (
            c["triage"]["status"] != "alert",
            -c["response"]["score"],
            c["request"]["transaction_id"],
        ),
    )


FEATURE_LABELS = {
    "TX_AMOUNT": "Valor da transação",
    "TX_HOUR": "Hora da transação",
    "TX_WEEKDAY": "Dia da semana",
}
for days, suffix in [(1, "1 dia"), (7, "7 dias")]:
    FEATURE_LABELS.update(
        {
            f"CUSTOMER_TX_COUNT_{days}D": f"Transações do cliente · {suffix}",
            f"CUSTOMER_AVG_AMOUNT_{days}D": f"Valor médio do cliente · {suffix}",
            f"CUSTOMER_AMOUNT_RATIO_{days}D": f"Valor / média do cliente · {suffix}",
            f"CUSTOMER_AMOUNT_RATIO_VALID_{days}D": f"Histórico do cliente disponível · {suffix}",
            f"TERMINAL_TX_COUNT_{days}D": f"Transações do terminal · {suffix}",
            f"TERMINAL_KNOWN_LABEL_COUNT_{days}D": f"Rótulos conhecidos do terminal · {suffix}",
            f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D": f"Fraudes conhecidas do terminal · {suffix}",
            f"TERMINAL_KNOWN_FRAUD_RATE_{days}D": f"Taxa conhecida de fraude do terminal · {suffix}",
        }
    )


def feature_label(name):
    return FEATURE_LABELS.get(name, name)
