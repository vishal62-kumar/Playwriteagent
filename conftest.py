"""pytest hooks for the QA agent.

- Re-uses the logged-in session saved by preflight (.auth/state.json) - no per-test login.
- Provides the `qa` fixture (step helpers) to generated tests.
- Records every test with Status, Category and a human-readable Reason, plus a screenshot on failure.
- Writes reports/results.json and (when run directly with pytest) reports/report.html + report.csv.
"""
import json
import os
import re
from datetime import datetime

import pytest

from qa import config as qa_config

CATEGORY_HELP = {
    "PASSED": "Test passed",
    "APP_DEFECT": "The application did not behave as its own code/validators say it should - investigate the app",
    "SCRIPT_ERROR": "The test could not perform a step (element missing, hidden or disabled) - the test needs healing",
    "ENVIRONMENT": "App not reachable, not logged in, or browser problem - not a test or app bug",
    "ASSERTION_FAILED": "An assertion in a hand-written / legacy test failed",
    "SKIPPED": "Test was skipped",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "scenario(**meta): metadata of a generated QA scenario")
    config._qa_results = []


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    args = dict(browser_context_args)
    if qa_config.STATE_FILE.exists():
        args["storage_state"] = str(qa_config.STATE_FILE)
    return args


@pytest.fixture
def qa(page, request):
    from qa.helpers import Steps

    steps = Steps(page, getattr(request.module, "ELEMENTS", {}), getattr(request.module, "VALIDATORS", {}))
    request.node._qa = steps
    yield steps


def _classify(excinfo):
    from playwright.sync_api import Error as PlaywrightError

    from qa.helpers import AppDefect, EnvError, StepError

    e = excinfo.value
    text = str(e).strip()
    if isinstance(e, EnvError):
        cat = "ENVIRONMENT"
    elif isinstance(e, AppDefect):
        cat = "APP_DEFECT"
    elif isinstance(e, StepError):
        cat = "SCRIPT_ERROR"
    elif any(s in text for s in ("net::ERR_", "browser has been closed", "Executable doesn't exist")):
        cat = "ENVIRONMENT"
    elif isinstance(e, AssertionError):
        cat = "ASSERTION_FAILED"
    elif isinstance(e, PlaywrightError) and ("Timeout" in text or "waiting for" in text):
        cat = "SCRIPT_ERROR"
        text = "Element not found / not actionable: " + text
    else:
        cat = "SCRIPT_ERROR"
        text = f"{type(e).__name__}: {text}"
    reason = re.sub(r"\s+", " ", text.split("Call log:")[0])[:500]
    return cat, reason


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and (rep.failed or rep.skipped)):
        marker = item.get_closest_marker("scenario")
        meta = dict(marker.kwargs) if marker else {}
        qa = getattr(item, "_qa", None)
        row = {
            "test": item.name,
            "nodeid": item.nodeid,
            "file": os.path.relpath(str(item.path), str(qa_config.ROOT)),
            "id": meta.get("id", item.name.replace("test_", "", 1)),
            "title": meta.get("title", item.function.__doc__ or ""),
            "type": meta.get("type", ""),
            "basis": meta.get("basis", ""),
            "expected": meta.get("expected", ""),
            "writes_data": meta.get("writes_data", False),
            "duration": round(rep.duration, 2),
            "steps": qa.log if qa else [],
            "dialogs": qa.dialogs if qa else [],
            "screenshot": "",
        }
        if rep.passed:
            row.update(status="PASSED", category="PASSED", reason="")
        elif rep.skipped:
            reason = rep.longrepr[2] if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
            row.update(status="SKIPPED", category="SKIPPED", reason=reason.replace("Skipped: ", ""))
        else:
            if rep.when == "setup":
                cat, reason = "ENVIRONMENT", "Test setup failed: " + str(call.excinfo.value).splitlines()[0][:400]
            else:
                cat, reason = _classify(call.excinfo)
            row.update(status="FAILED", category=cat, reason=reason)
            page = item.funcargs.get("page") if hasattr(item, "funcargs") else None
            if page:
                try:
                    base = os.environ.get("QA_RUN_DIR") or str(qa_config.REPORT_DIR)
                    shot_dir = os.path.join(base, "screenshots")
                    os.makedirs(shot_dir, exist_ok=True)
                    shot = os.path.join(shot_dir, f"{re.sub(r'[^A-Za-z0-9_]+', '_', item.name)}.png")
                    page.screenshot(path=shot, full_page=True)
                    row["screenshot"] = os.path.relpath(shot, base).replace("\\", "/")
                except Exception:
                    pass
        item.config._qa_results.append(row)


def pytest_sessionfinish(session, exitstatus):
    results = getattr(session.config, "_qa_results", None)
    if results is None:
        return
    qa_config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = os.environ.get("QA_RESULTS_FILE") or str(qa_config.RESULTS_FILE)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"finished_at": datetime.now().isoformat(timespec="seconds"), "results": results}, f, indent=2, ensure_ascii=False)
    if not os.environ.get("QA_RUNNER"):  # run directly with `pytest` -> build the report here
        from qa import report

        report.build(results, title="Test run")
