"""Step 6: the report - one row per test with status, category and the reason it failed."""
import csv
import html
import json
from datetime import datetime

from . import config

ORDER = ["APP_DEFECT", "SCRIPT_ERROR", "ENVIRONMENT", "ASSERTION_FAILED", "SKIPPED", "PASSED"]
LABEL = {
    "PASSED": "Passed", "APP_DEFECT": "App defect", "SCRIPT_ERROR": "Script error",
    "ENVIRONMENT": "Environment", "ASSERTION_FAILED": "Assertion failed", "SKIPPED": "Skipped",
}
MEANING = {
    "APP_DEFECT": "The app did not do what its own validators/code say it should. Investigate the application.",
    "SCRIPT_ERROR": "The test could not perform a step (element missing, hidden or disabled). The test needs healing, not the app.",
    "ENVIRONMENT": "App down, not logged in, or browser problem. Fix the environment and re-run.",
    "ASSERTION_FAILED": "An assertion in a legacy / hand-written test failed (cause not classified).",
    "SKIPPED": "Not executed.",
    "PASSED": "Behaved as expected.",
}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--muted:#5d6678;--line:#e3e6eb;
--pass:#1f7a4d;--defect:#b42318;--script:#b54708;--env:#475467;--skip:#667085}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:24px 32px;background:var(--card);border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:20px}.sub{color:var(--muted)}.focus{margin-top:8px;padding:8px 12px;background:#eef4ff;border-radius:6px;display:inline-block}
main{padding:24px 32px;max-width:1300px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;min-width:130px;cursor:pointer}
.card b{display:block;font-size:24px}.card.active{outline:2px solid var(--ink)}
.legend{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin-bottom:20px;color:var(--muted)}
.legend div{margin:2px 0}
details.legend summary{cursor:pointer;list-style:revert}
details.legend ul{margin:8px 0 0 0;padding-left:20px}
details.legend li{margin:2px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#eef0f3;font-weight:600;font-size:13px}
tr.row{cursor:pointer}tr.row:hover{background:#fafbfc}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff;white-space:nowrap}
.PASSED{background:var(--pass)}.APP_DEFECT{background:var(--defect)}.SCRIPT_ERROR{background:var(--script)}
.ENVIRONMENT{background:var(--env)}.ASSERTION_FAILED{background:var(--defect)}.SKIPPED{background:var(--skip)}
.reason{color:var(--defect)}.muted{color:var(--muted)}
tr.detail td{background:#fbfbfc}tr.detail{display:none}tr.detail.open{display:table-row}
ol.steps{margin:6px 0;padding-left:20px}ol.steps li.failed{color:var(--defect);font-weight:600}
ol.steps li.passed::marker{color:var(--pass)}
.shot{max-width:640px;border:1px solid var(--line);border-radius:6px;margin-top:8px}
code{background:#eef0f3;padding:1px 4px;border-radius:4px}
"""

JS = """
document.querySelectorAll('tr.row').forEach(r=>r.addEventListener('click',()=>r.nextElementSibling.classList.toggle('open')));
document.querySelectorAll('.card').forEach(c=>c.addEventListener('click',()=>{
  const cat=c.dataset.cat; const on=!c.classList.contains('active');
  document.querySelectorAll('.card').forEach(x=>x.classList.remove('active')); if(on) c.classList.add('active');
  document.querySelectorAll('tr.row').forEach(r=>{const show=!on||cat==='ALL'||r.dataset.cat===cat;
    r.style.display=show?'':'none'; if(!show) r.nextElementSibling.classList.remove('open');});
}));
"""


def _e(s):
    return html.escape(str(s or ""))


def build(results: list, title: str = "Test run", page: str = "", extra_notes: list = None, out_dir=None, focus: str = ""):
    out_dir = out_dir or config.REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: (ORDER.index(r["category"]) if r["category"] in ORDER else 9, r["id"]))
    counts = {c: sum(1 for r in results if r["category"] == c) for c in ORDER}
    total = len(results)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    cards = f'<div class="card" data-cat="ALL"><span class="muted">Total</span><b>{total}</b></div>'
    for c in ORDER:
        if counts[c]:
            cards += f'<div class="card" data-cat="{c}"><span class="pill {c}">{LABEL[c]}</span><b>{counts[c]}</b></div>'
    legend = "".join(f"<div><span class='pill {c}'>{LABEL[c]}</span> {MEANING[c]}</div>" for c in ORDER if counts[c] and c != "PASSED")

    rows = ""
    for r in results:
        steps = "".join(
            f"<li class='{_e(s['status'])}'>{_e(s['action'])}" + (f" <br><span class='reason'>{_e(s['detail'])}</span>" if s.get("detail") else "") + "</li>"
            for s in r.get("steps", [])
        )
        shot = f"<img class='shot' src='{_e(r['screenshot'])}' alt='screenshot at failure'>" if r.get("screenshot") else ""
        dialogs = f"<div><b>Pop-ups shown by the app:</b> {_e('; '.join(r['dialogs']))}</div>" if r.get("dialogs") else ""
        rows += (
            f"<tr class='row' data-cat='{r['category']}'><td><code>{_e(r['id'])}</code></td><td>{_e(r['title'])}"
            f"<div class='muted'>{_e(r.get('type'))}{' · writes data' if r.get('writes_data') else ''}{' · healed' if r.get('healed') else ''}</div></td>"
            f"<td><span class='pill {r['category']}'>{LABEL.get(r['category'], r['category'])}</span></td>"
            f"<td class='{'reason' if r['status'] == 'FAILED' else 'muted'}'>{_e(r['reason']) or '—'}</td><td>{r['duration']}s</td></tr>"
            f"<tr class='detail'><td></td><td colspan='4'>"
            f"<div><b>Expected:</b> {_e(r.get('expected')) or '—'}</div>"
            f"<div><b>Why this is expected:</b> {_e(r.get('basis')) or '—'}</div>"
            f"<div><b>Steps:</b><ol class='steps'>{steps or '<li>no steps recorded</li>'}</ol></div>{dialogs}"
            f"<div class='muted'>{_e(r['nodeid'])}</div>{shot}</td></tr>"
        )
    notes = "".join(f"<li>{_e(n)}</li>" for n in (extra_notes or []))
    # Collapsed by default - these are housekeeping notes about scenarios that were filtered out or
    # downgraded before the run (rejected suggestions, smoke-test fallbacks), not test results, so
    # they'd otherwise clutter the top of the report. Still here to expand if you want to check them.
    notes_html = f"<details class='legend'><summary><b>Generation notes</b> ({len(extra_notes)})</summary><ul>{notes}</ul></details>" if notes else ""
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Report {_e(page)}</title><style>{CSS}</style></head><body>
<header><h1>QA report{(' - ' + _e(page)) if page else ''}</h1><div class="sub">{_e(title)} · {stamp} · click a row for steps and screenshot, click a card to filter</div>{('<div class="focus"><b>What was tested:</b> ' + _e(focus) + '</div>') if focus else ''}</header>
<main><div class="cards">{cards}</div><div class="legend">{legend or 'All tests passed.'}</div>{notes_html}
<table><thead><tr><th>ID</th><th>Scenario</th><th>Result</th><th>Reason</th><th>Time</th></tr></thead><tbody>{rows}</tbody></table>
</main><script>{JS}</script></body></html>"""
    html_path = out_dir / "report.html"
    html_path.write_text(doc, encoding="utf-8")

    csv_path = out_dir / "report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig opens cleanly in Excel
        w = csv.writer(f)
        w.writerow(["Test ID", "Scenario", "Type", "Status", "Category", "Reason", "Expected", "Basis", "Failed Step", "Duration (s)"])
        for r in results:
            failed = next((s["action"] for s in r.get("steps", []) if s["status"] == "failed"), "")
            w.writerow([r["id"], r["title"], r.get("type", ""), r["status"], r["category"], r["reason"],
                        r.get("expected", ""), r.get("basis", ""), failed, r["duration"]])
    return html_path, csv_path, counts


def print_summary(counts: dict, results: list):
    print("\n" + "=" * 70)
    print("TEST RESULTS")
    print("=" * 70)
    for c in ORDER:
        if counts.get(c):
            print(f"  {LABEL[c]:<18} {counts[c]}")
    failed = [r for r in results if r["status"] == "FAILED"]
    if failed:
        print("\nFailures:")
        for r in sorted(failed, key=lambda r: ORDER.index(r["category"]) if r["category"] in ORDER else 9):
            print(f"  [{r['category']}] {r['id']} - {r['title'][:60]}")
            print(f"      why: {r['reason'][:200]}")
    print("=" * 70)


def load_results(path=None):
    path = path or config.RESULTS_FILE
    return json.loads(open(path, encoding="utf-8").read())["results"]
