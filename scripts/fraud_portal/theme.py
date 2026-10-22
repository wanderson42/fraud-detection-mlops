"""Consistent native Streamlit colors; presentation never relies on browser auto-theme; matches every chart and widget."""

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
.block-container{padding-top:4.75rem;padding-bottom:2rem;max-width:1500px}
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
.portal-kicker{color:var(--portal-accent);font-size:.75rem;font-weight:750;
 letter-spacing:.16em;line-height:1.65;overflow-wrap:anywhere;margin:0 0 8px}
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
 .block-container{padding-left:1rem;padding-right:1rem}}
</style>"""
