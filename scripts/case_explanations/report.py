"""Self-contained investigation report; contribution colors encode direction, not verdict."""

from html import escape
import json


def chart(case):
    explanation = case["SHAP_explanation"]
    pairs = sorted(explanation["contributions"].items(), key=lambda p: (-abs(p[1]), p[0]))
    maximum = max([abs(v) for _, v in pairs] + [1e-12])
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1040 710" role="img">',
        "<title>Contribuições SHAP em log-odds: laranja aumenta, azul diminui o score</title>",
        '<rect width="1040" height="710" fill="white"/>',
        '<text x="24" y="28" font-family="sans-serif" font-size="18">Contribuições para o score do modelo</text>',
        '<text x="24" y="53" font-family="sans-serif" font-size="13">Laranja: aumenta • Azul: diminui • Unidade: log-odds</text>',
        '<line x1="680" y1="72" x2="680" y2="650" stroke="#64748b"/>',
    ]
    for i, (name, contribution) in enumerate(pairs):
        y = 82 + i * 29
        width = abs(contribution) / maximum * 210
        x = 680 if contribution >= 0 else 680 - width
        color = "#c26416" if contribution > 0 else "#2463a6" if contribution < 0 else "#64748b"
        parts += [
            f'<text x="24" y="{y + 15}" font-family="monospace" font-size="12">{escape(name)}</text>',
            f'<rect x="{x:.4f}" y="{y}" width="{max(width, 0.5):.4f}" height="20" fill="{color}"/>',
            f'<text x="920" y="{y + 15}" font-family="monospace" font-size="12">{contribution:+.5f}</text>',
        ]
    parts += [
        '<text x="24" y="685" font-family="sans-serif" font-size="13">As contribuições explicam o modelo; não determinam causalidade ou fraude confirmada.</text>',
        "</svg>",
    ]
    return "\n".join(parts)


def write(directory, cases, manifest):
    """Only bounded, escaped case content; no JavaScript, external assets or live service."""
    sections = []
    for case in cases:
        tid = case["request"]["transaction_id"]
        explanation = case["SHAP_explanation"]
        filename = f"case-{tid}.svg"
        (directory / filename).write_text(chart(case))
        rows = "".join(
            f"<tr><th>{escape(name)}</th><td>{escape(str(value))}</td>"
            f"<td>{explanation['contributions'][name]:+.8f}</td></tr>"
            for name, value in case["request"]["features"].items()
        )
        feedback = case["transaction_feedback"]
        feedback_text = "ainda não disponível" if feedback is None else str(feedback)
        sections.append(
            f'<section id="case-{tid}"><h2>Transação {tid}</h2>'
            f"<p>Cliente {case['request']['customer_id']} · "
            f"Instante {escape(case['request']['tx_datetime'])} · "
            f"Triagem: {escape(case['triage']['status'])} · "
            f"Score: <strong>{case['response']['score']:.8f}</strong></p>"
            f"<p>Feedback posterior da transação: {feedback_text}. "
            "O feedback não participa do cálculo da explicação.</p>"
            f'<img src="{filename}" alt="Contribuições SHAP da transação {tid}">'
            f"<p>Base {explanation['base_log_odds']:.8f} + contribuições = "
            f"{explanation['raw_log_odds']:.8f} log-odds. "
            f"Sigmoide = {explanation['reconstructed_score']:.8f}.</p>"
            "<details><summary>Features usadas e contribuições completas</summary>"
            "<table><thead><tr><th>Feature</th><th>Valor enviado</th><th>SHAP (log-odds)</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></details></section>"
        )
    html = (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Investigação de casos — champion</title><style>"
        "body{font:16px/1.6 system-ui;color:#18283b;background:#f4f6f8;margin:0}"
        "main{max-width:1100px;margin:auto;padding:28px}section{background:white;padding:24px;margin:24px 0;border-radius:10px}"
        "img{width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:13px}"
        "td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}code{overflow-wrap:anywhere}"
        "</style><main><h1>Investigação de casos do champion</h1>"
        "<p>Contexto da transação, score registrado, triagem e fatores que contribuíram "
        "para a saída do modelo. Score de ranking não equivale a probabilidade calibrada de fraude.</p>"
        f"<p>Modelo: <code>{escape(manifest['model_sha256'])}</code><br>"
        f"Release: <code>{escape(manifest['serving_release_id'])}</code></p>"
        "<p>Casos selecionados por alertas e score, sem seleção por rótulo. "
        "Esta seleção serve à investigação; não mede a qualidade global do modelo. "
        "As explicações de features correlacionadas dependem do método utilizado.</p>"
        + "".join(sections)
        + "</main></html>"
    )
    (directory / "index.html").write_text(html)
    (directory / "cases.json").write_text(
        json.dumps(cases, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
