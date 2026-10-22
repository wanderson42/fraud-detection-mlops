"""Explicit dashboard colors; no queries, metrics, alert rules or model gates change."""

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PALETTE = {
    "information": "blue",
    "valid": "green",
    "investigate": "yellow",
    "invalid": "red",
    "unavailable": "gray",
}
MISSING = {
    "type": "special",
    "options": {"match": "null", "result": {"text": "Sem dados / sem suporte", "color": "gray"}},
}

NAN_MISSING = {
    "type": "special",
    "options": {"match": "nan", "result": {"text": "Sem dados / sem suporte", "color": "gray"}},
}


def category(filename, panel_id):
    if filename == "replay-operations.json":
        return {2: "collector", 7: "timing_gap", 13: "session"}.get(panel_id, "information")
    if filename == "feature-health.json":
        return {
            2: "collector",
            3: "comparison",
            5: "invalid_count",
            6: "missing_fraction",
            7: "invalid_count",
            8: "feature_shift",
            9: "feature_lag",
            10: "invalid_count",
        }.get(panel_id, "information")
    if filename == "kind-overview.json":
        return {3: "collector", 4: "complete"}.get(panel_id, "information")
    return "information"


def style(kind):
    result = {
        "color": {"mode": "fixed", "fixedColor": "blue"},
        "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]},
        "mappings": [MISSING, NAN_MISSING],
    }
    states = {
        "collector": {
            "0": {"text": "Inválido", "color": "red"},
            "1": {"text": "Válido", "color": "green"},
        },
        "comparison": {
            "0": {"text": "Sem comparação", "color": "gray"},
            "1": {"text": "Comparação disponível", "color": "blue"},
        },
        "session": {
            "0": {"text": "Parada", "color": "gray"},
            "1": {"text": "Em execução", "color": "blue"},
        },
        "complete": {
            "0": {"text": "Não concluído", "color": "blue"},
            "1": {"text": "Concluído", "color": "blue"},
        },
    }
    if kind in states:
        result["mappings"] = [{"type": "value", "options": states[kind]}, MISSING, NAN_MISSING]
    elif kind in {
        "invalid_count",
        "missing_fraction",
        "timing_gap",
        "feature_shift",
        "feature_lag",
    }:
        base = "blue" if kind == "feature_shift" else "green"
        level = {
            "invalid_count": 1,
            "missing_fraction": 0.001,
            "timing_gap": 1,
            "feature_shift": math.nextafter(0.1, math.inf),
            "feature_lag": 1e-9,
        }[kind]
        signal = "red" if kind in {"invalid_count", "missing_fraction"} else "yellow"
        result["color"] = {"mode": "thresholds"}
        result["thresholds"]["steps"] = [
            {"color": base, "value": None},
            {"color": signal, "value": level},
        ]
    return result


def explanation(kind):
    return {
        "information": "Azul: informação, sem classificação automática de bom/ruim. Nenhum limiar de aceitação é inferido do número.",
        "collector": "Válido (verde): snapshot coletado; Inválido (vermelho): collector sem snapshot válido. Ausência de amostra é cinza, não zero.",
        "comparison": "Comparação disponível (azul): suporte e dados válidos. Sem comparação (cinza): comparação indisponível; consultar qualidade e suporte antes de interpretar drift.",
        "session": "Parada (cinza) não significa falha: é esperada no replay concluído. Em execução (azul) informa o estado, sem atestar saúde.",
        "complete": "Estado informativo do replay. Concluído não representa novo processamento ou nova avaliação de qualidade.",
        "invalid_count": "Verde: zero violações observadas. Vermelho: uma ou mais violações explícitas de contrato/relógio; investigar o estado preservado.",
        "missing_fraction": "Verde: nenhuma ausência. Vermelho: ausência detectada. A menor fração positiva é 1/1000, pois a janela tem até 1000 observações.",
        "timing_gap": "Verde: nenhum commit sem tempo medido. Amarelo: há lacunas de medição; investigar antes de interpretar latência. Não indica perda do commit.",
        "feature_shift": "Azul até 0,1; amarelo estritamente acima de 0,1: investigar mudança descritiva entre janelas com suporte. Não comprova perda de qualidade ou autoriza CT.",
        "feature_lag": "Verde: relógios alinhados. Amarelo: atraso positivo em resolução de nanossegundos; investigar sua causa. Não é violação automática de SLA.",
    }[kind]


def apply(dashboard, filename):
    legend = "**Cores:** azul = informação; verde = condição válida verificada; amarelo = investigar; vermelho = violação explícita; cinza = indisponível ou não aplicável. Cor não substitui o texto e o suporte da métrica."
    header = next((p for p in dashboard["panels"] if p["type"] == "text"), None)
    if header and "**Cores:**" not in header["options"]["content"]:
        header["options"]["content"] = (
            header["options"]["content"].replace(r"\n", "\n") + "\n\n" + legend
        )
        boundary = header["gridPos"]["y"] + header["gridPos"]["h"]
        header["gridPos"]["h"] += 2
        for p in dashboard["panels"]:
            if p is not header and p["gridPos"]["y"] >= boundary:
                p["gridPos"]["y"] += 2
    for panel in dashboard["panels"]:
        if panel["type"] == "text":
            continue
        kind = category(filename, panel["id"])
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        defaults.update(style(kind))
        defaults["noValue"] = "Sem dados / sem suporte"
        panel["description"] = explanation(kind)
        if panel["type"] == "stat":
            panel.setdefault("options", {})["colorMode"] = "value"
    return dashboard


def subset(expected, observed):
    """Grafana may add defaults/indices; require every explicitly provisioned leaf."""
    if isinstance(expected, dict):
        return isinstance(observed, dict) and all(
            k in observed and subset(v, observed[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(expected) == len(observed)
            and all(subset(a, b) for a, b in zip(expected, observed, strict=True))
        )
    return (
        type(expected) is type(observed) and expected == observed
        if isinstance(expected, bool)
        else expected == observed
    )


def verify_provisioned(expected, observed):
    if len(expected["panels"]) != len(observed["panels"]):
        raise ValueError("Dashboard panel count differs")
    for wanted, actual in zip(expected["panels"], observed["panels"], strict=True):
        if wanted["id"] != actual["id"]:
            raise ValueError("Dashboard panel identity differs")
        if wanted["type"] == "text":
            if not subset(wanted.get("options", {}), actual.get("options", {})):
                raise ValueError("Provisioned dashboard legend differs")
            continue
        checks = {
            "description": wanted["description"],
            "fieldConfig": {
                "defaults": {
                    key: wanted["fieldConfig"]["defaults"][key]
                    for key in ("color", "thresholds", "mappings", "noValue")
                }
            },
        }
        if wanted["type"] == "stat":
            checks["options"] = {"colorMode": "value"}
        if not subset(checks, actual):
            raise ValueError("Provisioned dashboard color policy differs")


def validate():
    locations = (
        ROOT / "infra/observability/grafana/dashboards",
        ROOT / "infra/helm/fraud-observability/files/dashboards",
    )
    panels = 0
    for location in locations:
        for path in sorted(location.glob("*.json")):
            dashboard = json.loads(path.read_text())
            for panel in dashboard["panels"]:
                if panel["type"] == "text":
                    continue
                panels += 1
                expected = style(category(path.name, panel["id"]))
                if not subset(expected, panel.get("fieldConfig", {}).get("defaults", {})):
                    raise ValueError(
                        "Dashboard policy differs: " + str(path) + " panel " + str(panel["id"])
                    )
    return {
        "status": "dashboard_color_policy_validated",
        "policy": "semantic_colors_v1",
        "numeric_panels_checked_across_both_destinations": panels,
        "native_Grafana_validated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate",))
    parser.parse_args()
    print(json.dumps(validate(), indent=2))


if __name__ == "__main__":
    main()
