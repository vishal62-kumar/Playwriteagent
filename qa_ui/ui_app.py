"""
Medcross QA Atelier — a Streamlit front end for run_agent.py.

This file does not import or modify anything in run_agent.py's pipeline (prepare/execute/etc).
It drives the agent exactly the way a person at the terminal would: by invoking
`python run_agent.py <page> --focus "..." ...` as a subprocess and reading the same files
(report.html / report.csv) the CLI already writes. That keeps this UI completely decoupled from
the agent's internals — it will keep working even if the pipeline is restructured later, and it
never risks running Playwright's sync API off the main thread (a real hazard if the agent's
functions were imported and called directly from inside Streamlit's script-runner thread).

The only thing read directly from the project is qa/library.py's known_page_paths()/list_sets(),
used purely to populate convenience dropdowns (pages tested before, saved sets) — read-only,
nothing here writes to or changes any file in qa/ or run_agent.py.

This file lives in its own qa_ui/ subfolder on purpose, not next to run_agent.py. Streamlit
auto-adds every .py file in a folder literally named "pages" sitting beside whatever script you
run as an extra sidebar page - and this project already has an unrelated pages/ folder (old
Playwright page-object files) that would otherwise get swept in and imported by Streamlit,
crashing with "No module named 'playwright'" since that's not this environment's job.

Run with (from the qa_ui folder):  streamlit run ui_app.py
"""
import csv
import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # qa_ui/ui_app.py -> project root is one level up
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import qa.library as library
    import qa.config as qconfig
    import qa.source_parser as source_parser
    HAVE_LIBRARY = True
except Exception:
    HAVE_LIBRARY = False

try:
    from streamlit_searchbox import st_searchbox
    HAVE_SEARCHBOX = True
except Exception:
    HAVE_SEARCHBOX = False


# --------------------------------------------------------------------------------- page suggestions
# Mirrors run_agent.py's correct_page_typo() matching logic, read-only and without any prompting -
# it only suggests, it never calls the real agent or touches anything in qa/ or run_agent.py.
_FILLER_WORDS = re.compile(r"\s*\b(page|screen|form|aspx|the)\b\s*", re.I)


def _saved_cookie_header():
    try:
        state = json.loads(qconfig.STATE_FILE.read_text(encoding="utf-8"))
        return "; ".join(f"{c['name']}={c['value']}" for c in state.get("cookies", []))
    except Exception:
        return ""


def _url_not_404(page_name, timeout=2.5):
    url = f"{qconfig.BASE_URL}/{page_name}.aspx"
    req = urllib.request.Request(url, headers={"Cookie": _saved_cookie_header()})
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return True  # can't tell - don't block a real suggestion over a network hiccup


def suggest_pages(typed, limit=3):
    """Up to `limit` (candidate, verified) suggestions for a typed page, best first. Empty list
    means either an exact match or nothing close enough to suggest."""
    if not typed or not HAVE_LIBRARY:
        return []
    known = library.known_page_paths()
    if typed in known:
        return []
    lower_map = {k.lower(): k for k in known}
    if typed.lower() in lower_map:
        return []  # case-only difference - not worth asking about

    cleaned = _FILLER_WORDS.sub("", typed).strip() or typed
    probe = cleaned.lower() if len(cleaned) >= len(typed) * 0.6 else typed.lower()
    words = {probe, typed.lower()}

    def ranked(pool, verified):
        scores = {}
        for cand in pool:
            r = max(difflib.SequenceMatcher(None, w, cand.lower()).ratio() for w in words)
            if r >= 0.55:
                scores[cand] = max(scores.get(cand, 0), r)
        return [(c, r, verified) for c, r in scores.items()]

    try:
        source_pages = source_parser.list_all_pages(qconfig.SOURCE_DIR)
    except Exception:
        source_pages = set()
    all_matches = ranked(known, True) + ranked(source_pages, False)
    if not all_matches:
        return []

    best = {}
    for cand, r, verified in all_matches:
        prev = best.get(cand)
        if not prev or verified and not prev[1] or (verified == prev[1] and r > prev[0]):
            best[cand] = (r, verified)
    ranked_all = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)

    checked, live_checks = [], 0
    for cand, (r, verified) in ranked_all:
        if verified or live_checks >= 6:
            checked.append((cand, verified))
            continue
        live_checks += 1
        if _url_not_404(cand):
            checked.append((cand, verified))
    return checked[:limit]


def tested_pages_by_recency():
    """Every page ever successfully tested (read straight from test_library/ on disk, so it's
    there every time this page loads - today, tomorrow, after a restart, doesn't matter), most
    recently tested first so yesterday's or today's work is right at the top of the dropdown."""
    if not HAVE_LIBRARY:
        return []
    latest = {}
    try:
        for f in list(qconfig.LIBRARY_DIR.glob("*/*/scenarios.json")) + \
                 list(qconfig.LIBRARY_DIR.glob("*/*/draft_scenarios.json")):
            try:
                page = json.loads(f.read_text(encoding="utf-8")).get("page")
            except Exception:
                continue
            if not page:
                continue
            mtime = f.stat().st_mtime
            if page not in latest or mtime > latest[page]:
                latest[page] = mtime
    except Exception:
        return sorted(library.known_page_paths()) if HAVE_LIBRARY else []
    return [p for p, _ in sorted(latest.items(), key=lambda kv: kv[1], reverse=True)]


# ----------------------------------------------------------------------------------- page & styling
st.set_page_config(page_title="Medcross QA Atelier", page_icon="✦", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"]  { font-family: 'Inter', sans-serif; }
h1, h2, h3, .luxury-title { font-family: 'Inter', sans-serif !important; }

.stApp {
    background: #ffffff;
}

/* ---- the native Streamlit toolbar: collapsed to nothing, since the custom navbar below replaces
   it entirely (matches the reference design, which has its own navbar/banner, not Streamlit's) ---- */
header[data-testid="stHeader"], [data-testid="stHeader"], [data-testid="stToolbar"], .stAppHeader {
    height: 0 !important;
    min-height: 0 !important;
    visibility: hidden !important;
}
.block-container { padding-top: 0 !important; }

/* ---- custom top navbar (logo + nav + profile) - visual chrome matching the reference design.
   Only "Home" is a real place in this app; the other links are shown for the look but aren't
   wired to anything, since this is a single-page tool, not a multi-page site. ---- */
.topnav {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 32px; background: #ffffff; border-bottom: 1px solid #e5e7eb;
    margin: 0 -1rem 0 -1rem;
}
.topnav .brand { display: flex; align-items: center; gap: 10px; }
.topnav .brand .mark {
    width: 38px; height: 38px; border-radius: 10px;
    background: linear-gradient(135deg, #4f46e5, #22c3a6);
    display: flex; align-items: center; justify-content: center;
    color: #ffffff; font-size: 20px; font-weight: 800; flex-shrink: 0;
}
.topnav .brand .name { line-height: 1.15; }
.topnav .brand .name b { font-size: 16px; color: #18181b; display: block; }
.topnav .brand .name span { font-size: 12px; color: #6b7280; }
.topnav .links { display: flex; align-items: center; gap: 30px; }
.topnav .links a {
    color: #6b7280; text-decoration: none; font-size: 14px; font-weight: 600;
    display: flex; align-items: center; gap: 6px; padding-bottom: 20px; margin-bottom: -20px;
    cursor: default;
}
.topnav .links a.active { color: #4f46e5; border-bottom: 2px solid #4f46e5; }
.topnav .profile { display: flex; align-items: center; gap: 10px; }
.topnav .profile .avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: linear-gradient(135deg, #a78bfa, #4f46e5);
    color: #ffffff; font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
}
.topnav .profile .pname { font-size: 14px; font-weight: 600; color: #18181b; }

/* ---- announcement banner (rocket + tagline + decorative "Deploy" pill) ---- */
.topbanner {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 14px 32px; margin: 0 -1rem 28px -1rem;
    background: linear-gradient(90deg, #eef2ff, #ecfeff);
    border-bottom: 1px solid #e5e7eb;
}
.topbanner .msg { display: flex; align-items: center; gap: 10px; color: #18181b; font-weight: 700; font-size: 15px; }
.topbanner .deploy-pill {
    display: flex; align-items: center; gap: 6px;
    background: #4f46e5; color: #ffffff; font-weight: 600; font-size: 13px;
    padding: 9px 18px; border-radius: 6px; white-space: nowrap; cursor: default;
}

/* ---- hero card wrapping the Atelier header, with a small illustration ---- */
.hero-card {
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
    background: linear-gradient(120deg, #eef2ff 0%, #f5f3ff 45%, #ecfeff 100%);
    border-radius: 14px; padding: 32px 36px; margin: 0 0 24px 0;
    border: 1px solid #e5e7eb;
}
.hero-card .badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: #d1fae5; color: #065f46; font-size: 12px; font-weight: 700;
    padding: 5px 12px; border-radius: 999px; margin-bottom: 14px;
}
.hero-illustration { flex-shrink: 0; }
/* ---- sidebar: light neutral panel, not a separate dark block - minimal, not themed on its own ---- */
[data-testid="stSidebar"] {
    background: #fafafa !important;
    border-right: 1px solid #e5e7eb !important;
}
/* ---- header banner (now inside .hero-card, see above) ---- */
.atelier-header .eyebrow {
    letter-spacing: 1.5px;
    font-size: 12px;
    color: #4f46e5;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 6px;
}
.atelier-header h1 {
    color: #18181b;
    font-size: 36px;
    margin: 0;
    font-weight: 700;
    letter-spacing: -0.5px;
}
.atelier-header .tagline {
    color: #6b7280;
    margin-top: 8px;
    font-size: 15px;
}

/* ---- card ---- */
.luxury-card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 28px 30px;
    margin-bottom: 22px;
}
.luxury-card h3 {
    color: #18181b;
    font-size: 18px;
    margin-top: 0;
    margin-bottom: 18px;
    font-weight: 600;
}
.section-label {
    color: #4f46e5;
    font-size: 12px;
    letter-spacing: 1px;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-label .step-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: #4f46e5;
    color: #ffffff;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0;
    text-transform: none;
    flex-shrink: 0;
}
.char-counter {
    font-size: 12px;
    color: #9ca3af;
    text-align: right;
    margin-top: 2px;
}
.tip-box {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    background: #eef2ff;
    border: 1px solid #e0e7ff;
    border-radius: 8px;
    padding: 10px 12px;
    margin-top: 14px;
    font-size: 13px;
    color: #4338ca;
}
.sidebar-section-label {
    color: #4f46e5;
    font-size: 12px;
    letter-spacing: 1px;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 6px;
}
.history-item {
    font-size: 13px;
    color: #374151;
    padding: 3px 0;
}
.help-card {
    background: #fafafa;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 12px 14px;
    margin-top: 16px;
    font-size: 13px;
}
.help-card .title { font-weight: 600; color: #18181b; margin-bottom: 2px; }
.help-card .sub { color: #6b7280; font-size: 12px; margin-bottom: 8px; }
.help-card a {
    display: inline-block;
    background: #4f46e5;
    color: #ffffff !important;
    text-decoration: none;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 10px;
    border-radius: 6px;
}

/* ---- inputs (selectors kept broad/attribute-based so they survive Streamlit DOM changes) ---- */
input[type="text"], input[type="number"], textarea,
.stTextInput input, .stTextArea textarea, .stNumberInput input,
[data-baseweb="input"], [data-baseweb="input"] input, [data-baseweb="textarea"] {
    background-color: #ffffff !important;
    color: #1f2430 !important;
    border: 1px solid #d7dce3 !important;
    border-radius: 6px !important;
}
input[type="text"]:focus, input[type="number"]:focus, textarea:focus {
    border-color: #4f46e5 !important;
    box-shadow: 0 0 0 1px #4f46e5 !important;
}
.stSelectbox div[data-baseweb="select"], .stSelectbox div[data-baseweb="select"] > div,
[data-baseweb="popover"], [data-baseweb="menu"] {
    background-color: #ffffff !important;
    border-color: #d1d5db !important;
    border-radius: 6px !important;
}
.stCheckbox label, .stSlider label, .stNumberInput label, .stTextInput label, .stTextArea label, .stSelectbox label {
    color: #6b7280 !important;
    font-size: 13px !important;
}

/* the live search-as-you-type box (streamlit-searchbox) renders inside its own iframe, so its
   INTERNAL colors can't be reached from here - but the iframe element itself (its outer box) can,
   which covers the "looks darker than the rest of the form" edge some browsers show. */
.element-container iframe {
    background-color: #ffffff !important;
    border-radius: 6px !important;
}

/* ---- checkboxes: checked state uses the indigo accent (the sidebar is light now, so the
   default box already has reasonable contrast - no dark-background override needed) ---- */
[data-baseweb="checkbox"] input:checked + div,
[data-baseweb="checkbox"] input:checked ~ div,
[data-baseweb="checkbox"] [aria-checked="true"] {
    background-color: #4f46e5 !important;
    border-color: #4f46e5 !important;
}

/* ---- buttons (selectors kept broad so they survive Streamlit DOM/version changes) ---- */
.stButton button, .stButton>button, .stDownloadButton button,
button[kind="secondary"], button[kind="secondaryFormSubmit"],
[data-testid^="stBaseButton"] {
    background: #4f46e5 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 6px !important;
    padding: 10px 24px;
    font-weight: 600;
    font-size: 14px;
    text-transform: none;
    letter-spacing: 0;
    box-shadow: none;
    transition: background 0.15s ease;
}
.stButton button:disabled, [data-testid^="stBaseButton"]:disabled {
    background: #c7c9f5 !important;
    color: #ffffff !important;
    box-shadow: none !important;
}
.stButton button:hover, .stButton>button:hover, .stDownloadButton button:hover,
[data-testid^="stBaseButton"]:hover {
    background: #4338ca !important;
}

/* ---- console ---- */
.console-box {
    background: #fafafa;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 16px 20px;
    max-height: 420px;
    overflow-y: auto;
    font-family: 'SFMono-Regular', Consolas, monospace;
    font-size: 12.5px;
    color: #374151;
    white-space: pre-wrap;
    line-height: 1.55;
}

/* ---- stat pills ---- */
.stat-row { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 18px; }
.stat-card {
    flex: 1; min-width: 120px;
    background: #ffffff; border: 1px solid #e5e7eb;
    border-radius: 8px; padding: 14px 18px;
}
.stat-card .n { font-size: 26px; font-weight: 700; }
.stat-card .l { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: #9ca3af; margin-top: 2px;}
.c-PASSED .n { color: #16a34a; }
.c-APP_DEFECT .n { color: #db2777; }
.c-SCRIPT_ERROR .n { color: #d97706; }
.c-ENVIRONMENT .n { color: #6b7280; }
.c-TOTAL .n { color: #4f46e5; }

.alert-error {
    border: 1px solid #fbcfe8;
    background: #fdf2f8;
    border-radius: 8px; padding: 16px 20px; color: #9d174d;
}
.alert-ok {
    border: 1px solid #bbf7d0;
    background: #f0fdf4;
    border-radius: 8px; padding: 16px 20px; color: #15803d;
}

/* result table */
.result-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.result-table th {
    text-align: left; color: #6b7280; text-transform: uppercase; font-size: 11px;
    letter-spacing: 1px; border-bottom: 1px solid #e5e7eb; padding: 8px 10px;
}
.result-table td { padding: 9px 10px; border-bottom: 1px solid #f3f4f6; color: #374151; vertical-align: top; }
.pill { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.pill-PASSED { background: rgba(63,174,126,0.15); color: #2e9e67; }
.pill-APP_DEFECT { background: rgba(224,84,142,0.15); color: #c9448c; }
.pill-SCRIPT_ERROR { background: rgba(217,140,63,0.15); color: #d98c3f; }
.pill-ENVIRONMENT { background: rgba(108,143,174,0.15); color: #6c8fae; }
.pill-ASSERTION_FAILED { background: rgba(224,84,142,0.15); color: #c9448c; }
.pill-SKIPPED { background: rgba(60,50,60,0.08); color: #8a8490; }

footer, #MainMenu { visibility: hidden; }

/* the little chain-link icon Streamlit adds next to headings for copying a section link */
[data-testid="stHeaderActionElements"], a.anchor-link, .stMarkdown h1 a, .stMarkdown h2 a, .stMarkdown h3 a {
    display: none !important;
}

/* the little "Press Enter to apply" hint Streamlit shows under a changed text box - not needed here */
[data-testid="InputInstructions"] { display: none !important; }

/* native bordered container used for the "New Test Run" card, styled to match the rest of the page */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    box-shadow: none !important;
    padding: 6px 10px !important;
}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------------------- header
st.markdown("""
<div class="topnav">
  <div class="brand">
    <div class="mark">M</div>
    <div class="name"><b>Medcross</b><span>QA Atelier</span></div>
  </div>
  <div class="links">
    <a class="active" href="#">Home</a>
    <a href="#">Automation</a>
    <a href="#">Reports</a>
    <a href="#">Settings</a>
  </div>
  <div class="profile">
    <div class="avatar">QA</div>
    <div class="pname">QA Engineer</div>
  </div>
</div>

<div class="topbanner">
  <div class="msg">🚀 Breaking Bugs Before They Break Your Business &mdash; Automation Tester | Quality Assurance | Test Automation</div>
  <div class="deploy-pill">Deploy</div>
</div>

<div class="hero-card">
  <div class="badge">AI-Powered Testing</div>
  <div class="atelier-header">
    <div class="eyebrow">Medcross · Automated QA</div>
    <h1>Atelier</h1>
    <div class="tagline">Describe what to test — Verity handles the rest.</div>
  </div>
  <div class="hero-illustration"></div>
</div>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------------------- helpers
CATEGORY_ORDER = ["APP_DEFECT", "SCRIPT_ERROR", "ENVIRONMENT", "ASSERTION_FAILED", "SKIPPED", "PASSED"]
CATEGORY_LABEL = {
    "PASSED": "Passed", "APP_DEFECT": "App defect", "SCRIPT_ERROR": "Script error",
    "ENVIRONMENT": "Environment", "ASSERTION_FAILED": "Assertion failed", "SKIPPED": "Skipped",
}


def _reader_thread(proc, state):
    """Runs in the background so the main Streamlit script is never blocked reading output -
    that's what lets a Cancel button actually take effect mid-run instead of the whole page
    freezing until run_agent.py finishes on its own."""
    for line in proc.stdout:
        state["lines"].append(line.rstrip("\n"))
    proc.wait()
    state["done"] = True


def start_run(cmd):
    """Launches run_agent.py as a subprocess and starts a background thread tailing its output
    into st.session_state, so the page can keep re-rendering (and responding to Cancel) while
    it runs, instead of one long blocking read."""
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace", env=env,
    )
    state = {"lines": [], "done": False}
    st.session_state["run_proc"] = proc
    st.session_state["run_state"] = state
    st.session_state["run_active"] = True
    threading.Thread(target=_reader_thread, args=(proc, state), daemon=True).start()


def cancel_run():
    proc = st.session_state.get("run_proc")
    if proc and proc.poll() is None:
        proc.terminate()  # ask it to stop; the agent's own subprocess (pytest) goes down with it
    st.session_state["run_active"] = False


def parse_report_paths(full_log: str):
    html_m = re.search(r"Report\s*:\s*(.+)", full_log)
    html_path = Path(html_m.group(1).strip()) if html_m else None
    csv_path = html_path.parent / "report.csv" if html_path else None
    return html_path, csv_path


def render_stats_and_table(csv_path: Path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    counts = {c: 0 for c in CATEGORY_ORDER}
    for r in rows:
        counts[r.get("Category", "")] = counts.get(r.get("Category", ""), 0) + 1

    stat_html = '<div class="stat-row"><div class="stat-card c-TOTAL"><div class="n">' + str(len(rows)) + \
                '</div><div class="l">Total</div></div>'
    for c in CATEGORY_ORDER:
        if counts.get(c):
            stat_html += f'<div class="stat-card c-{c}"><div class="n">{counts[c]}</div>' \
                         f'<div class="l">{CATEGORY_LABEL[c]}</div></div>'
    stat_html += "</div>"
    st.markdown(stat_html, unsafe_allow_html=True)

    table_html = (
        '<table class="result-table"><thead><tr>'
        '<th>ID</th><th>Scenario</th><th>Result</th><th>Reason</th><th>Time</th>'
        '</tr></thead><tbody>'
    )
    for r in rows:
        cat = r.get("Category", "")
        table_html += (
            f"<tr><td>{r.get('Test ID','')}</td>"
            f"<td>{r.get('Scenario','')}<br><span style='color:#7a7568;font-size:11px'>{r.get('Type','')}</span></td>"
            f"<td><span class='pill pill-{cat}'>{CATEGORY_LABEL.get(cat, cat)}</span></td>"
            f"<td style='color:{'#eb6f6f' if r.get('Status')=='FAILED' else '#7a7568'}'>{r.get('Reason','') or '—'}</td>"
            f"<td>{r.get('Duration (s)','')}s</td></tr>"
        )
    table_html += "</tbody></table>"
    st.markdown(f'<div class="luxury-card">{table_html}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown('<div class="sidebar-section-label">⚙️ Settings</div>', unsafe_allow_html=True)
    allow_writes = st.checkbox("Allow saving / changing data", value=False)
    headed = st.checkbox("Show the browser while testing", value=False)
    use_source = st.checkbox("Read the application's source code", value=True)
    heal_rounds = 2  # no longer a visible control - this is the agent's own sensible default
    st.markdown("---")
    st.markdown('<div class="sidebar-section-label">🕓 History</div>', unsafe_allow_html=True)
    if HAVE_LIBRARY:
        try:
            tested = tested_pages_by_recency()
            if tested:
                for p in tested:
                    st.markdown(f'<div class="history-item">&rsaquo; {p}</div>', unsafe_allow_html=True)
            else:
                st.caption("No pages tested yet.")
        except Exception:
            st.caption("(history unavailable)")
    else:
        st.caption("(history unavailable — qa package not found next to this file)")

    st.markdown(
        '<div class="help-card">'
        '<div class="title">Need Help?</div>'
        '<div class="sub">Contact our support team for assistance.</div>'
        '<a href="#">Contact Support</a>'
        '</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------------------- main form
try:
    known_paths = tested_pages_by_recency()  # most recently tested first - picked up fresh every load
except Exception:
    known_paths = []

# "form_gen" is a plain counter, not tied to any widget - bumping it is always legal, at any
# point in the script. Every field below is keyed off it (e.g. key=f"page_input_{FORM_GEN}"), so
# bumping the counter and calling st.rerun() makes Streamlit build a brand-new, blank set of
# widgets next run instead of trying to overwrite the values of ones already on screen - which is
# what actually guarantees Reset clears everything on the very first click, with no dependency on
# callback-ordering details that can vary between Streamlit versions.
if "form_gen" not in st.session_state:
    st.session_state["form_gen"] = 0
FORM_GEN = st.session_state["form_gen"]

PAGE_PICK_PLACEHOLDER = "Select page"


def _apply_page_pick(_key):
    picked = st.session_state.get(_key, PAGE_PICK_PLACEHOLDER)
    if picked != PAGE_PICK_PLACEHOLDER:
        st.session_state[f"page_input_{FORM_GEN}"] = picked


with st.container(border=True):
    st.markdown("<h3>New Test Run</h3>", unsafe_allow_html=True)

    st.markdown('<div class="section-label"><span class="step-badge">1</span> Where — which page</div>', unsafe_allow_html=True)

    # True live search-as-you-type needs streamlit-searchbox (it debounces keystrokes client-side
    # and calls this search function on each one). If it's not installed, or anything about its
    # API doesn't match what's installed, fall back to the classic box + dropdown below instead of
    # breaking the page.
    use_classic_page_input = not HAVE_SEARCHBOX

    if HAVE_SEARCHBOX:
        def _search_pages(searchterm):
            term = (searchterm or "").strip()
            results = []
            seen = set()
            if term:
                results.append((f"Use exactly what I typed: {term}", term))
                seen.add(term)
                for cand, verified in suggest_pages(term, limit=6):
                    if cand not in seen:
                        label = cand + ("" if verified else "  (found in your code, not tested before)")
                        results.append((label, cand))
                        seen.add(cand)
                low = term.lower()
                for p in known_paths:
                    if low in p.lower() and p not in seen:
                        results.append((p, p))
                        seen.add(p)
                        if len(results) >= 10:
                            break
            else:
                for p in known_paths[:10]:
                    results.append((p, p))
            return results

        try:
            picked = st_searchbox(
                _search_pages,
                key=f"page_searchbox_{FORM_GEN}",
                placeholder="Start typing a page — e.g. Contacts/PracticeReferralDoctor",
                clear_on_submit=False,
            )
            page = picked or st.session_state.get(f"page_input_{FORM_GEN}", "")
            st.session_state[f"page_input_{FORM_GEN}"] = page
        except Exception:
            use_classic_page_input = True

    if use_classic_page_input:
        if not HAVE_SEARCHBOX:
            st.caption("Tip: run `pip install streamlit-searchbox` and restart for live search-as-you-type here.")
        col1, col2 = st.columns([2, 1])
        with col1:
            page = st.text_input("Page path", placeholder="e.g. Contacts/PracticeReferralDoctor",
                                 label_visibility="collapsed", key=f"page_input_{FORM_GEN}")
        with col2:
            pick_key = f"page_pick_{FORM_GEN}"
            st.selectbox("Or pick a tested page", [PAGE_PICK_PLACEHOLDER] + known_paths, label_visibility="collapsed",
                        key=pick_key, on_change=_apply_page_pick, args=(pick_key,))

        if page:
            suggestions = suggest_pages(page)
            if suggestions:
                keep_label = "Keep what I typed: " + page
                labels = [f"{cand}" + ("" if verified else "  (found in your code, not tested before)")
                          for cand, verified in suggestions]
                suggest_options = [keep_label] + labels
                suggest_key = f"suggest_pick_{FORM_GEN}_{page}"

                def _apply_suggestion(_key=suggest_key, _labels=labels, _suggestions=suggestions, _keep=keep_label):
                    picked = st.session_state.get(_key, _keep)
                    if picked != _keep:
                        st.session_state[f"page_input_{FORM_GEN}"] = _suggestions[_labels.index(picked)][0]

                st.markdown(f'<div class="section-label" style="margin-top:4px;">'
                            f"Matching pages — pick one from the dropdown, or keep typing:</div>",
                            unsafe_allow_html=True)
                st.selectbox("Matching pages", suggest_options, label_visibility="collapsed",
                            key=suggest_key, on_change=_apply_suggestion)

    rerun_set = None
    sets = []
    if page and HAVE_LIBRARY:
        try:
            sets = library.list_sets(page)
        except Exception:
            sets = []
    if sets:
        NEW_TEST_OPTION = "— New test (don't re-run a saved set) —"
        set_labels = {NEW_TEST_OPTION: None}
        for s in sets[:25]:  # newest first; plenty for "multiple sets a day" without an endless list
            res = ", ".join(f"{k.lower().replace('_', ' ')} {v}" for k, v in s.get("counts", {}).items() if v) or "not run yet"
            label = f"{s['when']} — {s['focus'][:50]} ({s['count']} tests, {res})"
            set_labels[label] = s
        chosen_label = st.selectbox(
            "Re-run a previously saved test set for this page, or start a new one",
            list(set_labels.keys()),
            key=f"rerun_pick_{FORM_GEN}",
        )
        rerun_set = set_labels[chosen_label]

    st.markdown('<div class="section-label" style="margin-top:18px;"><span class="step-badge">2</span> What — describe the test</div>', unsafe_allow_html=True)
    focus = st.text_area(
        "Focus", placeholder="e.g. 'mobile number validation' — or leave blank for full automatic page coverage",
        label_visibility="collapsed", disabled=bool(rerun_set), key=f"focus_input_{FORM_GEN}",
    )
    st.markdown(f'<div class="char-counter">{len(focus or "")} characters</div>', unsafe_allow_html=True)
    count = None
    if focus:
        count = st.number_input("Max test cases", min_value=1, max_value=50, value=6, key=f"max_cases_input_{FORM_GEN}")

    st.markdown(
        '<div class="tip-box">💡 <div>Tip: leave the description blank for full automatic page coverage, '
        'or describe one specific behavior (like "mobile number validation") to focus the run.</div></div>',
        unsafe_allow_html=True,
    )

    col_run, col_reset = st.columns([3, 1])
    with col_run:
        run_clicked = st.button("Run Tests", disabled=st.session_state.get("run_active", False), use_container_width=True)
    with col_reset:
        # Bumping "form_gen" (a plain counter, not a widget's own key) and forcing an immediate
        # rerun is what actually guarantees every field above - page, description, dropdowns, the
        # max-test-cases box - is blank the instant the page redraws, on this very click. Every
        # widget above is keyed off FORM_GEN, so the next run builds brand-new, empty widgets
        # instead of trying to overwrite ones already on screen (which is what Reset used to do,
        # and why it needed a second click / crashed outright).
        if st.button("Reset", disabled=st.session_state.get("run_active", False), use_container_width=True):
            st.session_state["form_gen"] = FORM_GEN + 1
            st.session_state["last_result"] = None    # also clear whatever report/result is on screen
            st.session_state["was_cancelled"] = False
            st.rerun()


# --------------------------------------------------------------------------------------- execution
if "run_active" not in st.session_state:
    st.session_state["run_active"] = False
    st.session_state["was_cancelled"] = False


def _build_cmd(target_page, use_rerun=False):
    cmd = [sys.executable, "run_agent.py", target_page]
    if use_rerun:
        cmd.append("--rerun")  # Resume Test Run always re-uses the latest saved set (the one just made)
    elif rerun_set:
        cmd += ["--rerun-dir", rerun_set["dir"].name]
    elif focus:
        cmd += ["--focus", focus]
        if count:
            cmd += ["--count", str(int(count))]
    if not allow_writes:
        cmd.append("--no-writes")
    if headed:
        cmd.append("--headed")
    if not use_source:
        cmd.append("--no-source")
    cmd += ["--heal", str(heal_rounds), "--no-open"]
    return cmd


if run_clicked and not st.session_state["run_active"]:
    if not page:
        st.warning("Type or pick a page first.")
    else:
        st.session_state["was_cancelled"] = False
        st.session_state["last_result"] = None
        st.session_state["last_page"] = page
        start_run(_build_cmd(page))
        st.rerun()

if st.session_state["run_active"]:
    state = st.session_state["run_state"]
    lines = state["lines"]

    top = st.columns([5, 1])
    with top[0]:
        st.markdown('<div class="section-label">Live Progress</div>', unsafe_allow_html=True)
    with top[1]:
        if st.button("Cancel Run"):
            cancel_run()
            st.session_state["was_cancelled"] = True
            st.warning("Run cancelled.")
            st.stop()
    st.markdown(f'<div class="console-box">{"<br>".join(lines[-200:])}</div>', unsafe_allow_html=True)

    if not state["done"]:
        time.sleep(1)
        st.rerun()
    else:
        st.session_state["run_active"] = False
        full_log = "\n".join(lines)

        if "STOPPED -" in full_log:
            reason = full_log.split("STOPPED -", 1)[1].strip()
            st.session_state["last_result"] = {"kind": "stopped", "reason": reason}
        else:
            html_path, csv_path = parse_report_paths(full_log)
            if csv_path and csv_path.exists():
                st.session_state["last_result"] = {
                    "kind": "finished", "html_path": html_path, "csv_path": csv_path,
                }
            else:
                st.session_state["last_result"] = {"kind": "no_report"}
        st.rerun()

elif st.session_state.get("was_cancelled"):
    st.markdown(
        '<div class="alert-error">Run cancelled. The test cases it had already generated are saved - '
        'resuming runs that same set again without asking GPT to generate them a second time.</div>',
        unsafe_allow_html=True,
    )
    if st.button("Resume Test Run"):
        st.session_state["was_cancelled"] = False
        st.session_state["last_result"] = None
        start_run(_build_cmd(st.session_state["last_page"], use_rerun=True))
        st.rerun()

elif st.session_state.get("last_result"):
    # Kept here (instead of only showing once right as the run finishes) so the report stays on
    # screen across later reruns too - e.g. clicking "Open Full Report" or the download button
    # themselves trigger a rerun, and without this the whole report used to vanish on that click.
    res = st.session_state["last_result"]
    if res["kind"] == "stopped":
        st.markdown(f'<div class="alert-error">Run stopped — {res["reason"][:400]}</div>', unsafe_allow_html=True)
    elif res["kind"] == "finished":
        st.markdown('<div class="alert-ok">Run finished.</div>', unsafe_allow_html=True)
        render_stats_and_table(res["csv_path"])
        b1, b2 = st.columns(2)
        with b1:
            if res["html_path"] and st.button("Open Full Report"):
                webbrowser.open(res["html_path"].resolve().as_uri())
        with b2:
            st.download_button("Download report.csv", data=open(res["csv_path"], "rb").read(),
                               file_name="report.csv", mime="text/csv")
    else:
        st.markdown('<div class="alert-error">The run finished, but the report could not be located '
                    'automatically — check the log above.</div>', unsafe_allow_html=True)
