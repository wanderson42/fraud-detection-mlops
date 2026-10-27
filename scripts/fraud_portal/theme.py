"""Consistent native Streamlit colors; presentation never relies on browser auto-theme; matches every chart and widget."""

from html import escape

PALETTE = {
    "base": "dark",
    "primaryColor": "#60A5FA",
    "backgroundColor": "#0B1220",
    "secondaryBackgroundColor": "#142238",
    "textColor": "#E7EEF8",
    "blueTextColor": "#93C5FD",
    "blueBackgroundColor": "#172F4C",
    "borderColor": "#30435D",
    "dataframeHeaderBackgroundColor": "#1B2D46",
    "dataframeHeaderTextColor": "#E7EEF8",
    "baseRadius": "10px",
    "showWidgetBorder": "true",
    "sidebar.backgroundColor": "#080F1B",
    "sidebar.secondaryBackgroundColor": "#172940",
    "sidebar.textColor": "#F1F5F9",
    "sidebar.primaryColor": "#93C5FD",
    "sidebar.borderColor": "#38516D",
}


def launch_flags():
    return [f"--theme.{key}={value}" for key, value in PALETTE.items()]


CSS = """<style>
.stApp{--portal-surface:#142238;--portal-border:#30435D;
 --portal-text:#E7EEF8;--portal-accent:#60A5FA}
[data-testid="stHeader"]{height:2.75rem;background:transparent}
.block-container{padding-top:2rem!important;padding-bottom:2rem;max-width:1500px}
[data-testid="stSidebarUserContent"]{padding-top:1.3rem}
h1{font-size:2.35rem!important;letter-spacing:-.045em;line-height:1.18!important}
h2,h3{letter-spacing:-.025em}
[data-testid="stCaptionContainer"]{opacity:1}
[data-testid="stMetric"]{background:var(--portal-surface);
 border:1px solid var(--portal-border);border-radius:12px;padding:18px 20px;
 box-shadow:0 5px 18px #00000018;border-top:3px solid var(--portal-accent)}
[data-testid="stMetricLabel"]{font-weight:600}
[data-testid="stMetricValue"]{font-size:2.15rem;font-weight:650;letter-spacing:-.04em}
[data-testid="stVerticalBlockBorderWrapper"]>div{border-radius:12px}
[data-testid="stSidebar"]{border-right:0}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{opacity:1}
[data-testid="stSidebar"] h3{letter-spacing:.08em;font-size:1.25rem!important}
[data-testid="stSidebar"] label[data-baseweb="radio"]{padding:10px 12px;
 border-radius:8px;margin:3px 0}
[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked){
 background:var(--portal-surface);font-weight:600}
[data-testid="stSidebar"] [data-testid="stRadio"]>label{font-size:.78rem;
 text-transform:uppercase;letter-spacing:.09em}
[data-testid="stDataFrame"]{border-radius:10px}
.portal-hero{position:relative;isolation:isolate;box-sizing:border-box;
 width:100%;min-width:0;padding:18px 24px 20px;margin:0 0 2px;
 border:1px solid #2A4262;border-left:3px solid #60A5FA;border-radius:16px;
 background:radial-gradient(ellipse at 100% 0%,#1B3B602E,transparent 65%),
 linear-gradient(115deg,#15263D 0%,#101E32 60%,#0D1829 100%);
 box-shadow:0 12px 32px #00000020;overflow:visible}
.portal-hero-top{display:flex;align-items:center;justify-content:space-between;
 gap:12px;flex-wrap:wrap;margin-bottom:10px}
.portal-wordmark{color:#A7CFFF;font-size:.69rem;font-weight:750;
 letter-spacing:.19em;line-height:1.5}
.portal-source-pill{display:inline-block;color:#C4D8EF;background:#1D355066;
 border:1px solid #3C5877;border-radius:999px;padding:3px 10px;
 font-size:.69rem;line-height:1.5;font-weight:600;letter-spacing:.02em}
[data-testid="stMarkdownContainer"] .portal-kicker{display:block;box-sizing:border-box;
 color:#EDF5FF;font-size:clamp(1.1rem,2.05vw,1.7rem)!important;font-weight:750;
 letter-spacing:.035em!important;line-height:1.32!important;white-space:normal!important;
 overflow:visible!important;text-overflow:clip!important;max-height:none!important;
 height:auto!important;width:100%;max-width:100%;word-break:normal;
 overflow-wrap:break-word;text-wrap:balance;margin:0!important;padding:0!important}
.portal-hero-subtitle{color:#B4C7DD;font-size:.83rem;line-height:1.6;
 margin:9px 0 0;max-width:64ch}
.portal-case-label{font-size:.72rem;text-transform:uppercase;letter-spacing:.11em;
 font-weight:700;color:var(--portal-text);opacity:.8;margin:0 0 10px}
[data-testid="stMarkdownContainer"] .portal-case-title{font-size:1.35rem!important;font-weight:700;letter-spacing:-.02em;
 color:var(--portal-text);margin:0 0 6px}
.portal-status{display:inline-block;font-size:.72rem;font-weight:650;
 border:1px solid var(--portal-border);border-radius:20px;padding:3px 10px;
 color:var(--portal-text);background:var(--portal-surface);margin:0 0 12px}
.st-key-outcomes-card,.st-key-scores-card,[class*="st-key-priority-case-"]{
 background:var(--portal-surface);border-radius:12px}
[data-testid="stBaseButton-primary"]{color:#081525!important;font-weight:700}
[data-baseweb="tag"],[data-baseweb="tag"] *{color:#081525!important}
button{transition:background-color .15s,border-color .15s}
@media(max-width:700px){h1{font-size:1.8rem!important}
 .block-container{padding-left:1rem;padding-right:1rem;padding-top:2.7rem!important}
 .portal-hero{padding:15px 16px 17px;border-radius:12px}
 .portal-hero-top{gap:8px;margin-bottom:9px}
 [data-testid="stMarkdownContainer"] .portal-kicker{font-size:1.13rem!important;
 letter-spacing:.025em!important;line-height:1.4!important}
 .portal-hero-subtitle{font-size:.79rem}}
@media(prefers-reduced-motion:reduce){button{transition:none}}
</style>"""


def header(source):
    """Responsive portal identity; the source badge describes scope, never system health."""
    return (
        '<section class="portal-hero" aria-label="Identidade do portal">'
        '<div class="portal-hero-top"><span class="portal-wordmark">FRAUD OPS</span>'
        '<span class="portal-source-pill">' + escape(source) + "</span></div>"
        '<div class="portal-kicker" role="heading" aria-level="2">'
        "INTELIGÊNCIA PARA INVESTIGAÇÃO DE FRAUDE</div>"
        '<p class="portal-hero-subtitle">Pontuação, contexto e evidências para orientar a investigação.</p>'
        "</section>"
    )
