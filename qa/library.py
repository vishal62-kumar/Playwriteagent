"""Test-case library: every run is saved so test cases can be reviewed and re-run later.

test_library/
  test_case_repository.csv                 <- every test case ever generated (open in Excel)
  <page>/<date_time>_<what-was-tested>/
      test_cases.csv                       <- readable test cases: steps, expected result, last result
      scenarios.json                       <- machine-readable test cases (used for re-runs)
      test_<page>_<stamp>.py               <- the executable Playwright test file
      report.html / report.csv             <- results of this run
      screenshots/
      run.json                             <- summary of the run
"""
import csv
import json
import re
from datetime import datetime
from pathlib import Path

from . import config

REPO_FILE = config.LIBRARY_DIR / "test_case_repository.csv"
REPO_HEADER = ["Run", "Date", "Page", "What was tested", "Test ID", "Scenario", "Type", "Steps",
               "Expected Result", "Why expected", "Writes data", "Result", "Category", "Reason"]


def _slug(text, n=40):
    return (re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()[:n]) or "full_page"


def new_run_dir(page: str, focus: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = config.LIBRARY_DIR / config.page_slug(page) / f"{stamp}_{_slug(focus, 30)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_id_number(abbr: str) -> int:
    """Test IDs never repeat across runs: continue after the highest TC_<ABBR>_NNN already saved."""
    highest = 0
    pattern = re.compile(rf"^TC_{re.escape(abbr)}_(\d+)$")
    for f in config.LIBRARY_DIR.glob("*/*/scenarios.json"):
        try:
            for s in json.loads(f.read_text(encoding="utf-8")).get("scenarios", []):
                m = pattern.match(s.get("id", ""))
                if m:
                    highest = max(highest, int(m.group(1)))
        except Exception:
            continue
    return highest + 1


def step_text(step: dict, inv: dict) -> str:
    el = inv["elements"].get(step.get("element"), {}) if inv else {}
    name = f"'{el.get('label') or step.get('element')}'"
    a, v = step["action"], step.get("value")
    return {
        "fill": f"Enter {v!r} in {name}",
        "clear": f"Clear {name}",
        "select": f"Select {v!r} in {name}",
        "check": f"Tick {name}",
        "uncheck": f"Untick {name}",
        "click": f"Click {name}",
        "press": f"Press {v} in {name}",
        "upload": f"Upload a sample file to {name}",
        "wait": f"Wait {v} ms",
    }.get(a, a)


def steps_text(sc: dict, inv: dict) -> str:
    lines = [f"1. Open {inv['url'] if inv else 'the page'}"]
    lines += [f"{i}. {step_text(s, inv)}" for i, s in enumerate(sc["steps"], 2)]
    return "\n".join(lines)


def expected_text(sc: dict, inv: dict) -> str:
    from .render import _describe

    return "\n".join(f"- {_describe(a, inv)}" for a in sc["assertions"])


def save_test_cases(run_dir: Path, doc: dict, inv: dict, focus: str, results: dict = None):
    (run_dir / "scenarios.json").write_text(json.dumps(dict(doc, focus=focus), indent=2, ensure_ascii=False), encoding="utf-8")
    with open(run_dir / "test_cases.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Test ID", "Scenario", "Type", "Steps", "Expected Result", "Why expected", "Writes data", "Result", "Category", "Reason"])
        for sc in doc["scenarios"]:
            r = (results or {}).get(sc["id"], {})
            w.writerow([sc["id"], sc["title"], sc["type"], steps_text(sc, inv), expected_text(sc, inv), sc.get("basis", ""),
                        "yes" if sc.get("writes_data") else "no", r.get("status", "not run"), r.get("category", ""), r.get("reason", "")])


def append_repository(run_dir: Path, doc: dict, inv: dict, focus: str, results: dict):
    REPO_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = not REPO_FILE.exists()
    with open(REPO_FILE, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(REPO_HEADER)
        for sc in doc["scenarios"]:
            r = results.get(sc["id"], {})
            w.writerow([run_dir.name, datetime.now().strftime("%Y-%m-%d %H:%M"), doc["page"], focus or "(whole page)",
                        sc["id"], sc["title"], sc["type"], steps_text(sc, inv), expected_text(sc, inv), sc.get("basis", ""),
                        "yes" if sc.get("writes_data") else "no", r.get("status", "not run"), r.get("category", ""), r.get("reason", "")])


def save_run_summary(run_dir: Path, page: str, focus: str, counts: dict):
    (run_dir / "run.json").write_text(json.dumps({
        "page": page, "focus": focus, "finished_at": datetime.now().isoformat(timespec="seconds"), "counts": counts,
    }, indent=2), encoding="utf-8")


def list_sets(page: str):
    """Saved test sets for a page, newest first: [{dir, focus, count, when, counts}]"""
    base = config.LIBRARY_DIR / config.page_slug(page)
    out = []
    for d in sorted(base.glob("*/"), reverse=True):
        f = d / "scenarios.json"
        if not f.exists():
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        summary = {}
        if (d / "run.json").exists():
            summary = json.loads((d / "run.json").read_text(encoding="utf-8")).get("counts", {})
        out.append({"dir": d, "focus": doc.get("focus") or "(whole page)", "count": len(doc.get("scenarios", [])),
                    "when": d.name[:15], "counts": summary, "doc": doc})
    return out


def known_pages():
    return sorted({d.name for d in config.LIBRARY_DIR.glob("*/") if d.is_dir()})


def known_page_paths():
    """The exact page paths (real casing/slashes, e.g. 'Reporting/DoctorReferralEntry') that have
    successfully scanned before - safe to suggest as a typo-correction since each one is only written
    here after a real, working scan (never a guess)."""
    if not config.LIBRARY_DIR.exists():
        return []
    paths = set()
    for f in list(config.LIBRARY_DIR.glob("*/*/scenarios.json")) + list(config.LIBRARY_DIR.glob("*/*/draft_scenarios.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8")).get("page")
        except Exception:
            continue
        if p:
            paths.add(p)
    return sorted(paths)


def find_pending_drafts():
    """Run folders with a draft_review.xlsx that hasn't been applied yet (no report.html alongside it),
    newest first. Used to offer 'you have a review waiting - run it now?' without the user typing a path."""
    if not config.LIBRARY_DIR.exists():
        return []
    out = [xlsx.parent for xlsx in config.LIBRARY_DIR.glob("*/*/draft_review.xlsx")
           if not (xlsx.parent / "report.html").exists()]
    out.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return out
