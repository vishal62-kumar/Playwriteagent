"""QA agent: you say WHAT to test and WHERE; it generates grounded test cases, runs them, reports, and saves them.

Interactive (asks you questions):
    agent.bat

Direct (no questions, good for scheduling):
    agent.bat Contacts/PracticeReferralDoctor --focus "mobile number validation"
    agent.bat Contacts/PracticeReferralDoctor                      # whole page
    agent.bat Contacts/PracticeReferralDoctor --rerun              # re-run the latest saved test set
    agent.bat Contacts/PracticeReferralDoctor --rerun-dir "20261103_143000_mobile_number"  # re-run a SPECIFIC saved set
    Options: --count N  --no-writes  --headed  --heal N  --no-open  --no-source

Excel approval workflow (semi-automatic: you approve/edit/reject each test case before it runs):
    agent.bat Contacts/PracticeReferralDoctor --focus "..." --draft   # writes draft_review.xlsx, stops
    (open draft_review.xlsx, fill the Decision column: Approve / Edit / Reject, save it)
    agent.bat --apply "test_library\\...\\draft_review.xlsx"           # runs only what you approved
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from qa import approval, config, heal, inventory, library, preflight, render, report, scenarios, source_parser
from qa.llm import LLMError


class Stop(Exception):
    """A step failed in a way the user must fix; message explains how."""


# ---------------------------------------------------------------------------------------- helpers
def ask(prompt, default=None):
    hint = f" [{default}]" if default not in (None, "") else ""
    try:
        value = input(f"\n{prompt}{hint}\n> ").strip()
    except EOFError:
        value = ""
    return value or (default if default is not None else "")


def ask_yes(prompt, default=False):
    value = ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not value else value.startswith("y")


_FILLER_WORDS = re.compile(r"\s*\b(page|screen|form|aspx|the)\b\s*", re.I)


def _saved_cookie_header():
    """Reuses the login session saved by a previous preflight (.auth/state.json), if there is one,
    so the live-check below sees the app the way a logged-in user does - most ASP.NET apps otherwise
    just redirect every unknown page straight to the login screen (200, not 404), which would make
    the check useless. No saved session yet -> empty string -> check falls back to unauthenticated."""
    try:
        state = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        return "; ".join(f"{c['name']}={c['value']}" for c in state.get("cookies", []))
    except Exception:
        return ""


def _url_not_404(page_name, timeout=2.5):
    """Quick check that a URL doesn't 404, using the saved login session if one exists - a real page
    normally answers with something other than 404, so this filters out clearly-wrong guesses before
    they're ever offered. Anything we can't determine (server down, timeout, odd response) is treated
    as 'maybe' rather than blocking the suggestion - this is a filter for confirmed-wrong, not a
    guarantee of confirmed-right."""
    import urllib.error
    import urllib.request
    try:
        # GET, not HEAD - some dev/test servers (and the odd real one) don't implement HEAD and
        # would otherwise answer with a misleading "not supported" status instead of a real one
        req = urllib.request.Request(config.page_url(page_name))
        cookies = _saved_cookie_header()
        if cookies:
            req.add_header("Cookie", cookies)
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return True


def correct_page_typo(page):
    """If `page` doesn't exactly match a page that has scanned successfully before, see if it's a close
    typo of one and offer to use the correct spelling instead - so 'Doctr/Hme' or 'doctorreferralentry
    page' finds 'Doctor/Home' / 'Reporting/DoctorReferralEntry'.

    Two tiers of candidates, ranked together by actual similarity (not "verified always wins"):
      1. Pages that have ACTUALLY scanned successfully before (100% safe - the URL is proven to work).
      2. Every .aspx file found under APP_SOURCE_DIR, even ones never tested - these are guesses at
         the URL (the app's folder structure doesn't always match the URL 1:1), so before any of
         these are offered they get a quick live HTTP check and ones that 404 are dropped - no point
         suggesting something that's already known to be wrong.
    When more than one candidate is a plausible match, shows up to 3 so you pick instead of it
    guessing for you.
    """
    known = library.known_page_paths()
    if page in known:
        return page
    lower_map = {k.lower(): k for k in known}
    if page.lower() in lower_map:
        return lower_map[page.lower()]  # case-only difference - just fix it silently

    # match case-insensitively (casing differences otherwise dominate the similarity score) and
    # with common filler words a person might type stripped out ("the doctor page" -> "doctor")
    cleaned = _FILLER_WORDS.sub("", page).strip() or page
    typed = cleaned.lower() if len(cleaned) >= len(page) * 0.6 else page.lower()  # don't over-strip short input
    words = {typed, page.lower()}

    def ranked(pool, verified):
        scores = {}
        for cand in pool:
            r = max(difflib.SequenceMatcher(None, w, cand.lower()).ratio() for w in words)
            if r >= 0.55:
                scores[cand] = max(scores.get(cand, 0), r)
        return [(cand, r, verified) for cand, r in scores.items()]

    source_pages = source_parser.list_all_pages(config.SOURCE_DIR)
    all_matches = ranked(known, True) + ranked(source_pages, False)
    if not all_matches:
        print(f"\nCan't find anything matching '{page}' in your tested pages or your app's source code "
              "- it doesn't look like that page exists (or the spelling's very different). Check the "
              "path and try again.")
        return None

    # de-dupe (a page can appear verified AND as a raw source-scan hit - keep the verified entry,
    # keep BOTH when it's only the two unverified spellings of the same file - we don't yet know
    # which spelling is right, so dropping one here could throw away the correct answer)
    best_by_cand = {}
    for cand, r, verified in all_matches:
        prev = best_by_cand.get(cand)
        if not prev or verified and not prev[1] or (verified == prev[1] and r > prev[0]):
            best_by_cand[cand] = (r, verified)

    ranked_all = sorted(best_by_cand.items(), key=lambda kv: kv[1][0], reverse=True)

    # live-check unverified candidates (top few only, to keep this fast) and drop ones that 404
    checked, live_checks = [], 0
    for cand, (r, verified) in ranked_all:
        if verified or live_checks >= 6:
            checked.append((cand, (r, verified)))
            continue
        live_checks += 1
        if _url_not_404(cand):
            checked.append((cand, (r, verified)))
    if not checked:
        print(f"\nCan't find anything matching '{page}' in your tested pages or your app's source code "
              "- the closest guesses from your code didn't resolve to a real page either. It doesn't "
              "look like that page exists. Check the path and try again.")
        return None

    top_ratio = checked[0][1][0]
    # only show several options when more than one is genuinely close to the best match; otherwise
    # a single confident suggestion is less friction than a list with one obvious right answer
    top = [t for t in checked if t[1][0] >= top_ratio - 0.1][:3]

    def label(cand, verified):
        return cand if verified else f"{cand}  (found in your code, not tested before)"

    if len(top) == 1:
        cand, (r, verified) = top[0]
        suffix = "" if verified else " (found in your app's code, not tested before, so I'm not 100% sure of the URL)"
        if ask_yes(f"Did you mean '{cand}'?{suffix}", default=True):
            return cand
        return _retype_or_keep(page)

    print("\nDid you mean one of these?")
    for n, (cand, (r, verified)) in enumerate(top, 1):
        print(f"  {n}. {label(cand, verified)}")
    choice = ask(f"Enter a number (1-{len(top)}), or press Enter to keep what you typed")
    if choice.isdigit() and 1 <= int(choice) <= len(top):
        return top[int(choice) - 1][0]
    if not choice:
        return page  # Enter, with nothing typed = keep it as-is
    return _retype_or_keep(page)  # typed something else ('n', 'no', 'r', ...) - none of these are right


def _retype_or_keep(page):
    """None of the suggestions were right - let the user type the page again instead of being
    stuck with the original (likely wrong) text."""
    again = ask(f"None of those? Type the page path again, or press Enter to search with '{page}' as you typed it")
    if again and again.strip() and again.strip() != page:
        return correct_page_typo(again.replace("\\", "/").replace(config.BASE_URL + "/", "").strip("/"))
    return page


def run_pytest(test_file, run_dir, node_ids=None, headed=False):
    targets = [f"{test_file}::{n}" for n in node_ids] if node_ids else [str(test_file)]
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider", "--browser", "chromium"]
    if headed:
        cmd.append("--headed")
    results_file = run_dir / "results.json"
    env = dict(os.environ, QA_RUNNER="1", PYTHONIOENCODING="utf-8", QA_RESULTS_FILE=str(results_file), QA_RUN_DIR=str(run_dir))
    if results_file.exists():
        results_file.unlink()
    proc = subprocess.run(cmd, cwd=str(config.ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not results_file.exists():
        print(proc.stdout[-3000:])
        print(proc.stderr[-2000:])
        raise Stop("pytest did not produce results - see the output above (often an import error or missing pytest-playwright).")
    results = report.load_results(results_file)
    if not results:
        print(proc.stdout[-3000:])
        raise Stop("pytest collected no tests - see the output above.")
    return results


def show_test_cases(doc, inv):
    print("\n" + "-" * 70)
    print(f"{len(doc['scenarios'])} TEST CASES")
    print("-" * 70)
    last_group = object()  # sentinel so the first scenario always prints a heading if grouped
    for n, sc in enumerate(doc["scenarios"], 1):
        group = sc.get("group")
        if group and group != last_group:
            print(f"\n === {group} ===")
            last_group = group
        flag = "  [saves data]" if sc.get("writes_data") else ""
        print(f"\n {n:>2}. {sc['id']}  {sc['title']}  ({sc['type']}){flag}")
        for line in library.steps_text(sc, inv).splitlines()[1:]:
            print(f"       {line}")
        print("       Expect: " + "; ".join(l[2:] for l in library.expected_text(sc, inv).splitlines()))
    if doc["rejected"]:
        print(f"\n ({len(doc['rejected'])} suggested test(s) were rejected because they did not match the real page"
              " - details in the report)")
    print("-" * 70)


def choose_tests(doc):
    """Lets the user drop tests before running. Returns False if the user cancels."""
    answer = ask("Run these tests?  Enter = run all  |  numbers to SKIP, e.g. 2,5  |  q = cancel")
    if answer.lower() in ("q", "quit", "n", "no", "cancel"):
        return False
    if answer:
        skip = {int(x) for x in answer.replace(" ", "").split(",") if x.isdigit()}
        doc["scenarios"] = [s for n, s in enumerate(doc["scenarios"], 1) if n not in skip]
        print(f"   Running {len(doc['scenarios'])} test(s).")
    return bool(doc["scenarios"])


# ---------------------------------------------------------------------------------------- one session
def prepare(page, focus="", count=None, saved_set=None, use_source=True, headless=True):
    """Preflight + scan + generate/reuse test cases. Returns (doc, inv, src)."""
    count = count or (6 if focus else 12)
    try:
        preflight.run_preflight(headless=headless)
    except preflight.PreflightError as e:
        raise Stop(f"Preflight: {e}")

    print(f"[2/6] Scanning {config.page_url(page)} ...")
    try:
        inv = inventory.scan(page, use_source=use_source, headless=headless)
    except inventory.ScanError as e:
        raise Stop(f"Scanning the page: {e}")
    src = inv["source"]
    print(f"      {inv['stats']['elements']} elements, {inv['stats']['validators']} ASP.NET validators, "
          f"{len(inv['known_messages'])} messages found in the code")
    print(f"      Source code: {src['aspx_path'] or 'not found (set APP_SOURCE_DIR in .env for better tests)'}")

    if saved_set:
        focus = saved_set["doc"].get("focus", focus) or ""
        print(f"[3/6] Re-using saved test set from {saved_set['when']} ({saved_set['count']} tests), checked against today's page ...")
        doc = scenarios.finalize(inv, saved_set["doc"]["scenarios"], retry=False, keep_ids=True, focus=focus)
    elif not focus:
        # no object/focus given -> automatically cover every field and action button on the page,
        # one grounded batch per object, so coverage never depends on the tester naming anything
        print("[3/6] No specific object given - generating thorough coverage for every field and button on this page ...")
        try:
            doc = scenarios.generate_full_coverage(inv, id_start=library.next_id_number(scenarios._abbr(page)))
        except LLMError as e:
            raise Stop(f"Generating test cases: {e}")
    else:
        print("[3/6] Writing test cases" + (f" for: {focus}" if focus else " for the whole page") + " ...")
        try:
            doc = scenarios.generate(inv, count=count, focus=focus,
                                     id_start=library.next_id_number(scenarios._abbr(page)))
        except LLMError as e:
            raise Stop(f"Generating test cases: {e}")
    if not doc["scenarios"]:
        raise Stop("No valid test cases could be made. Try describing the test differently. Rejected suggestions:\n  "
                   + "\n  ".join(f"{x['scenario'].get('title')}: {x['problems'][0]}" for x in doc["rejected"][:8]))
    return doc, inv, src


def execute(page, doc, inv, src, focus="", run_dir=None, allow_writes=True, headed=False,
           heal_rounds=2, use_source=True, open_report=True):
    """Render the test cases to Playwright code, run them (with healing), report, and save to the library."""
    headless = config.HEADLESS and not headed
    run_dir = run_dir or library.new_run_dir(page, focus)
    # Save the test cases BEFORE running, so they are kept even if the run fails
    library.save_test_cases(run_dir, doc, inv, focus)
    test_file = render.write(doc, inv, include_writes=allow_writes,
                             path=run_dir / f"test_{config.page_slug(page)}_{run_dir.name[:15]}.py")
    print(f"[4/6] Saved test cases to {os.path.relpath(run_dir, config.ROOT)}")

    # 5. Run + heal
    print(f"[5/6] Running {len(doc['scenarios'])} test(s) ...")
    results = run_pytest(test_file, run_dir, headed=headed)
    by_id = {r["id"]: r for r in results}

    failed = [r for r in results if r["status"] == "FAILED"]
    env_fail = [r for r in failed if r["category"] == "ENVIRONMENT"]
    if env_fail and len(env_fail) == len(failed) and len(env_fail) > 2:
        print(f"      {len(env_fail)} tests failed for ENVIRONMENT reasons - not healing. First reason: {env_fail[0]['reason']}")
        heal_rounds = 0

    heal_notes = []
    for round_no in range(1, heal_rounds + 1):
        failing = [r for r in by_id.values() if r["category"] == "SCRIPT_ERROR"]
        if not failing:
            break
        print(f"      Healing round {round_no}: {len(failing)} script error(s) ...")
        inv = inventory.scan(page, use_source=use_source, headless=headless)
        sc_by_id = {s["id"]: s for s in doc["scenarios"]}
        for r in failing:
            if r["id"] in sc_by_id:
                healed, msg = heal.heal_scenario(sc_by_id[r["id"]], inv, r)
                print(f"        {r['id']}: {msg}")
                heal_notes.append(f"{r['id']} (round {round_no}): {msg}")
                if healed:
                    sc_by_id[r["id"]] = healed
        doc["scenarios"] = [sc_by_id[s["id"]] for s in doc["scenarios"]]
        test_file = render.write(doc, inv, include_writes=allow_writes, path=test_file)
        for r in run_pytest(test_file, run_dir, node_ids=[f"test_{r['id']}" for r in failing], headed=headed):
            r["healed"] = r["status"] == "PASSED"
            by_id[r["id"]] = r

    # 6. Report + save
    final = list(by_id.values())
    notes = [f"Rejected suggestion: {json.dumps(x['scenario'].get('title', ''))} - {'; '.join(x['problems'][:2])}" for x in doc["rejected"]]
    notes += [f"{s['id']}: {n}" for s in doc["scenarios"] for n in s.get("notes", [])]
    notes += [f"Healing: {n}" for n in heal_notes]
    html_path, csv_path, counts = report.build(
        final, title=f"{len(final)} tests · source {'read' if src['found'] else 'not read'}",
        page=page, extra_notes=notes, out_dir=run_dir, focus=focus)
    library.save_test_cases(run_dir, doc, inv, focus, results=by_id)   # now with results
    library.append_repository(run_dir, doc, inv, focus, by_id)
    library.save_run_summary(run_dir, page, focus, counts)
    # keep a copy of the latest run in reports/ as well - best-effort only,
    # the real results are already safely saved in run_dir above, so this
    # convenience mirror must never be allowed to crash the run.
    try:
        config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("report.html", "report.csv"):
            shutil.copy(run_dir / name, config.REPORT_DIR / name)
        if (run_dir / "screenshots").exists():
            shutil.rmtree(config.REPORT_DIR / "screenshots", ignore_errors=True)
            shutil.copytree(run_dir / "screenshots", config.REPORT_DIR / "screenshots", dirs_exist_ok=True)
    except OSError as e:
        print(f"  (note: couldn't update the shared reports/ copy - {e}; your real report is still at {run_dir / 'report.html'})")

    print("[6/6] Report")
    report.print_summary(counts, final)
    print(f"  Report      : {html_path}")
    print(f"  Test cases  : {run_dir / 'test_cases.csv'}")
    print(f"  All tests   : {library.REPO_FILE}")
    if open_report:
        try:
            webbrowser.open(html_path.resolve().as_uri())
        except Exception:
            pass
    return run_dir


def run_session(page, focus="", count=None, allow_writes=True, saved_set=None, headed=False,
                heal_rounds=2, use_source=True, review=False, open_report=True):
    """The direct, no-approval-file flow: generate (or reuse) -> optionally list in the terminal -> run."""
    headless = config.HEADLESS and not headed
    doc, inv, src = prepare(page, focus=focus, count=count, saved_set=saved_set, use_source=use_source, headless=headless)
    focus = doc.get("focus", focus) or focus

    if review:
        show_test_cases(doc, inv)
        if not choose_tests(doc):
            print("Cancelled - nothing was run.")
            return None

    return execute(page, doc, inv, src, focus=focus, allow_writes=allow_writes, headed=headed,
                   heal_rounds=heal_rounds, use_source=use_source, open_report=open_report)


def draft_session(page, focus="", count=None, use_source=True, headed=False):
    """Generates test cases and stops - writes draft_review.xlsx for you to mark Approve/Edit/Reject."""
    headless = config.HEADLESS and not headed
    doc, inv, src = prepare(page, focus=focus, count=count, use_source=use_source, headless=headless)
    run_dir = library.new_run_dir(page, focus)
    xlsx = approval.generate_draft(run_dir, doc, inv)
    print(f"\n[4/4] {len(doc['scenarios'])} draft test case(s) written for your review:")
    print(f"  {xlsx}")
    if doc["rejected"]:
        print(f"  ({len(doc['rejected'])} suggested test(s) were dropped automatically - they did not match the real page)")
    print("\nOpen that file, fill the Decision column (Approve / Edit / Reject) for each row, save it, then run:")
    print(f'  agent.bat --apply "{xlsx}"')
    try:
        os.startfile(xlsx)  # Windows: opens it in Excel directly
    except Exception:
        pass
    return xlsx


def apply_session(xlsx_path, allow_writes=True, headed=False, heal_rounds=2, use_source=True, open_report=True):
    """Reads your Approve/Edit/Reject decisions from draft_review.xlsx and runs only what you approved."""
    from pathlib import Path

    xlsx_path = Path(xlsx_path)
    run_dir = xlsx_path.parent
    draft_file = run_dir / "draft_scenarios.json"
    if not xlsx_path.exists() or not draft_file.exists():
        raise Stop(f"Can't find the draft for {xlsx_path}. Point --apply at a draft_review.xlsx made by --draft.")
    doc = json.loads(draft_file.read_text(encoding="utf-8"))
    page, focus = doc["page"], doc.get("focus", "")

    headless = config.HEADLESS and not headed
    print(f"[1/6] Re-checking {page} is still reachable and unchanged ...")
    try:
        preflight.run_preflight(headless=headless)
        inv = inventory.scan(page, use_source=use_source, headless=headless)
    except (preflight.PreflightError, inventory.ScanError) as e:
        raise Stop(str(e))
    src = inv["source"]

    decisions = approval.read_decisions(xlsx_path)
    final_doc, summary = approval.apply_review(doc, inv, decisions)
    print(f"[2/6] Review: {summary['approved']} approved, {summary['edited']} edited, "
          f"{summary['rejected']} rejected, {summary['undecided']} left blank (skipped)")
    if summary["edit_failed"]:
        print(f"      {summary['edit_failed']} requested edit(s) could not be applied automatically - original kept, flagged in the report")
    if not final_doc["scenarios"]:
        raise Stop("Nothing was approved - mark at least one row Approve or Edit in the Excel file and run --apply again.")

    return execute(page, final_doc, inv, src, focus=focus, run_dir=run_dir, allow_writes=allow_writes,
                   headed=headed, heal_rounds=heal_rounds, use_source=use_source, open_report=open_report)


def check_pending_drafts(args, limit=5):
    """Lists your most recent not-yet-applied draft reviews (filled in or not) and lets you pick
    one to run or open - so you never have to type the --apply path yourself."""
    pending = library.find_pending_drafts()
    if not pending:
        return

    rows = []  # (run_dir, xlsx, label, filled, total)
    for run_dir in pending:
        xlsx = run_dir / "draft_review.xlsx"
        try:
            decisions = approval.read_decisions(xlsx)
            draft = json.loads((run_dir / "draft_scenarios.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        page, focus = draft.get("page", "?"), draft.get("focus", "")
        label = page + (f" ({focus})" if focus else "")
        filled = sum(1 for d in decisions.values() if d["decision"])
        rows.append((run_dir, xlsx, label, filled, len(decisions)))
        if len(rows) >= limit:
            break
    if not rows:
        return

    more = len(pending) - len(rows)
    print(f"\nYou have {len(pending)} test review{'s' if len(pending) != 1 else ''} waiting"
          + (f" (showing the {len(rows)} most recent)" if more > 0 else "") + ":")
    for n, (run_dir, xlsx, label, filled, total) in enumerate(rows, 1):
        status = f"{filled}/{total} decided" if filled else "not reviewed yet"
        print(f"  {n}. {label}  -  {status}")
    choice = ask(f"Enter a number to open/run that one, or press Enter to skip for now")
    if not (choice.isdigit() and 1 <= int(choice) <= len(rows)):
        return

    run_dir, xlsx, label, filled, total = rows[int(choice) - 1]
    if not filled:
        print(f"\n  That one hasn't been reviewed yet - opening it so you can fill in the Decision column:\n  {xlsx}")
        try:
            os.startfile(xlsx)
        except Exception:
            pass
        print(f"  Once you've saved it, run agent.bat again and pick it from this list to run it.")
        return

    if ask_yes(f"Run the review for {label} now?", default=True):
        allow_writes = ask_yes("May tests SAVE or CHANGE records in the database?", default=False)
        try:
            apply_session(str(xlsx), allow_writes=allow_writes, headed=args.headed, heal_rounds=args.heal,
                         use_source=not args.no_source, open_report=not args.no_open)
        except Stop as e:
            print(f"\nSTOPPED - {e}")


# ---------------------------------------------------------------------------------------- interactive
def interactive(args):
    print("=" * 70)
    print(" QA AGENT - tell me what to test and where")
    print("=" * 70)
    check_pending_drafts(args)
    last_page = ""
    while True:
        known = library.known_pages()
        if known:
            print("\nPages tested before: " + ", ".join(known))
        page = ask("WHERE: which page do you want to test? (path after the site address, e.g. Contacts/PracticeReferralDoctor)", last_page)
        if not page:
            print("No page given - bye.")
            return
        page = page.replace("\\", "/").replace(config.BASE_URL + "/", "").strip("/")
        page = correct_page_typo(page)
        if page is None:
            last_page = ""
            continue  # nothing matched - already told the user; go straight back to WHERE
        last_page = page

        saved_set = None
        sets = library.list_sets(page)
        if sets:
            print("\nSaved test sets for this page:")
            for n, s in enumerate(sets[:9], 1):
                res = ", ".join(f"{k.lower().replace('_', ' ')} {v}" for k, v in s["counts"].items() if v) or "not run"
                print(f"  {n}. {s['when']}  {s['count']} tests  - {s['focus'][:50]}  ({res})")
            choice = ask("Enter N for NEW test cases, or the number of a saved set to re-run it", "N")
            if choice.isdigit() and 1 <= int(choice) <= len(sets[:9]):
                saved_set = sets[int(choice) - 1]

        focus, count = "", None
        if not saved_set:
            focus = ask("WHAT: describe what you want to test, in your own words.\n"
                        "  e.g. 'mobile number validation', 'saving a doctor with missing required fields', 'the source type dropdown'\n"
                        "  Press Enter to automatically cover every field and button on the page, grouped by what each one is.")
            if focus:
                n = ask("How many test cases at most?", "6")
                count = int(n) if n.isdigit() else None
            else:
                print("  Will generate thorough coverage for every field/button automatically - no count needed.")

        allow_writes = ask_yes("May tests SAVE or CHANGE records in the database?", default=False)

        # straight to execution - no review step, no Excel, no "run these tests?" confirmation,
        # browser always stays hidden (pass --headed on the command line if you ever want it shown)
        try:
            run_session(page, focus=focus, count=count, allow_writes=allow_writes, saved_set=saved_set,
                        headed=args.headed, heal_rounds=args.heal, use_source=not args.no_source, review=False,
                        open_report=not args.no_open)
        except Stop as e:
            print(f"\nSTOPPED - {e}")
        if not ask_yes("Test something else?", default=False):
            print("Bye.")
            return


def main():
    ap = argparse.ArgumentParser(description="Playwright QA agent - say what to test and where")
    ap.add_argument("page", nargs="?", help="page path, e.g. Contacts/PracticeReferralDoctor (omit for interactive mode)")
    ap.add_argument("--focus", default="", help="what to test, in plain words (default: whole page)")
    ap.add_argument("--count", type=int, help="max number of test cases (default 6 with --focus, 12 without)")
    ap.add_argument("--rerun", action="store_true", help="re-run the latest saved test set for the page")
    ap.add_argument("--rerun-dir", default="", metavar="FOLDER",
                     help="re-run a SPECIFIC saved test set by its folder name under test_library/<page>/ "
                          "(as listed by library.list_sets - lets you pick an older set, not just the latest)")
    ap.add_argument("--heal", type=int, default=2, help="healing rounds for script errors (default 2, 0 = off)")
    ap.add_argument("--no-writes", action="store_true", help="skip tests that save/update/delete data")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--review", action="store_true", help="show the test cases and ask before running")
    ap.add_argument("--no-open", action="store_true", help="do not open the report in the browser")
    ap.add_argument("--no-source", action="store_true", help="do not read the application's source code")
    ap.add_argument("--draft", action="store_true", help="generate test cases and stop, writing draft_review.xlsx for you to approve/edit/reject")
    ap.add_argument("--apply", metavar="XLSX", help="run the test cases you approved in a draft_review.xlsx (page/focus are read from it; ignores 'page')")
    args = ap.parse_args()

    if args.apply:
        try:
            apply_session(args.apply, allow_writes=not args.no_writes, headed=args.headed,
                         heal_rounds=args.heal, use_source=not args.no_source, open_report=not args.no_open)
        except Stop as e:
            print(f"\nSTOPPED - {e}")
            sys.exit(2)
        return

    if not args.page:
        interactive(args)
        return

    # direct/scheduled runs have no one to confirm a fuzzy match with, so only fix an exact
    # case-only mismatch silently (e.g. 'doctor/home' -> 'Doctor/Home') - never guess here.
    known = library.known_page_paths()
    lower_map = {k.lower(): k for k in known}
    if args.page not in known and args.page.lower() in lower_map:
        args.page = lower_map[args.page.lower()]

    if args.draft:
        try:
            draft_session(args.page, focus=args.focus, count=args.count, use_source=not args.no_source, headed=args.headed)
        except Stop as e:
            print(f"\nSTOPPED - {e}")
            sys.exit(2)
        return

    saved_set = None
    if args.rerun_dir:
        sets = library.list_sets(args.page)
        saved_set = next((s for s in sets if s["dir"].name == args.rerun_dir), None)
        if not saved_set:
            print(f"No saved set named '{args.rerun_dir}' for {args.page} - generating new ones.")
    elif args.rerun:
        sets = library.list_sets(args.page)
        if not sets:
            print(f"No saved test sets for {args.page} yet - generating new ones.")
        else:
            saved_set = sets[0]
    try:
        run_session(args.page, focus=args.focus, count=args.count, allow_writes=not args.no_writes,
                    saved_set=saved_set, headed=args.headed, heal_rounds=args.heal,
                    use_source=not args.no_source, review=args.review, open_report=not args.no_open)
    except Stop as e:
        print(f"\nSTOPPED - {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
