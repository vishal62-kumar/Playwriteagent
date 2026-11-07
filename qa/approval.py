"""Excel-based approval workflow.

Flow:
  1. generate_draft()   -> writes draft_review.xlsx (one row per test case, a Decision + Comment column)
  2. you open it, type Approve / Edit / Reject in Decision, and (for Edit) what to change in Comment
  3. apply_review()      -> reads the file back:
       Approve -> test case kept as-is
       Edit    -> test case is sent to the LLM once with your comment, then re-validated
       Reject  -> test case dropped (kept in the file for the record, never rendered or run)
     Anything left blank is treated as "not decided yet" and is dropped with a warning, so an
     unreviewed row can never slip into the regression suite.
"""
import json

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from . import library
from .llm import ask_json

HEADER = ["Decision", "Test ID", "Scenario", "Type", "Saves data?", "Steps", "Expected Result",
          "Why expected", "Comment / what to change"]
DECISIONS = {"approve", "edit", "reject"}
# common variants people naturally type - all map to one of the three above
DECISION_ALIASES = {
    "approved": "approve", "approve": "approve", "approved.": "approve", "yes": "approve",
    "y": "approve", "ok": "approve", "okay": "approve",
    "edit": "edit", "edited": "edit", "change": "edit", "fix": "edit",
    "reject": "reject", "rejected": "reject", "no": "reject", "n": "reject", "skip": "reject", "skipped": "reject",
}

EDIT_PROMPT = """You adjust ONE already-valid QA test case per the reviewer's comment.
Return JSON: {"steps": [...], "assertions": [...]} in the same format as the scenario given.
Keep everything the reviewer did not ask to change. Use ONLY element/validator keys from the inventory.
If the comment asks for something this page cannot do, make the closest faithful change and explain
nothing else - just return the best {"steps":[...], "assertions":[...]}.
"""


def generate_draft(run_dir, doc: dict, inv: dict):
    wb = Workbook()
    ws = wb.active
    ws.title = "Review"
    ws.append(HEADER)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EEF0F3")
    dv = DataValidation(type="list", formula1='"Approve,Edit,Reject"', allow_blank=True,
                        showErrorMessage=True, errorTitle="Invalid", error="Type Approve, Edit or Reject")
    ws.add_data_validation(dv)
    row = 2
    last_group = object()
    for sc in doc["scenarios"]:
        group = sc.get("group")
        if group and group != last_group:
            ws.append([f"— {group} —"])
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(HEADER))
            cell = ws.cell(row, 1)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DCE6F1")
            last_group = group
            row += 1
        ws.append(["", sc["id"], sc["title"], sc["type"], "YES" if sc.get("writes_data") else "",
                   library.steps_text(sc, inv), library.expected_text(sc, inv), sc.get("basis", ""), ""])
        dv.add(f"A{row}")
        wrap = Alignment(wrap_text=True, vertical="top")
        ws.cell(row, 6).alignment = wrap
        ws.cell(row, 7).alignment = wrap
        row += 1
    widths = [10, 12, 28, 10, 10, 38, 34, 24, 34]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    path = run_dir / "draft_review.xlsx"
    wb.save(path)
    (run_dir / "draft_scenarios.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_decisions(xlsx_path):
    """Returns {test_id: {"decision": "approve"/"edit"/"reject"/"", "comment": str}}."""
    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[1]:
            continue
        raw = str(row[0] or "").strip().lower()
        decision = DECISION_ALIASES.get(raw, raw if raw in DECISIONS else "")
        out[row[1]] = {"decision": decision, "comment": str(row[8] or "").strip()}
    return out


def apply_review(doc: dict, inv: dict, decisions: dict):
    """Returns (final_doc, summary) where final_doc has only approved/fixed-and-approved scenarios."""
    from .scenarios import validate

    kept, summary = [], {"approved": 0, "edited": 0, "rejected": 0, "undecided": 0, "edit_failed": 0}
    for sc in doc["scenarios"]:
        d = decisions.get(sc["id"], {"decision": "", "comment": ""})
        choice = d["decision"]
        if choice == "reject":
            summary["rejected"] += 1
            continue
        if choice == "":
            summary["undecided"] += 1
            continue
        if choice == "approve":
            kept.append(sc)
            summary["approved"] += 1
            continue
        if choice == "edit":
            if not d["comment"]:
                kept.append(sc)
                summary["approved"] += 1
                continue
            try:
                from .scenarios import _compact_inventory
                request = {"scenario": {k: sc[k] for k in ("title", "steps", "assertions")},
                           "reviewer_comment": d["comment"], "inventory": _compact_inventory(inv)}
                resp = ask_json(EDIT_PROMPT, json.dumps(request, ensure_ascii=False))
                candidate = dict(sc, steps=resp.get("steps", sc["steps"]), assertions=resp.get("assertions", sc["assertions"]))
                cleaned, errors, _ = validate(candidate, inv)
                if errors:
                    raise ValueError("; ".join(errors[:3]))
                cleaned["id"] = sc["id"]
                cleaned["notes"] = list(sc.get("notes", [])) + [f"edited per reviewer: {d['comment']}"]
                if sc.get("group"):
                    cleaned["group"] = sc["group"]
                kept.append(cleaned)
                summary["edited"] += 1
            except Exception as e:
                sc = dict(sc, notes=list(sc.get("notes", [])) + [f"requested edit could not be applied ({e}) - kept original, please re-check"])
                kept.append(sc)
                summary["edit_failed"] += 1
    final = dict(doc, scenarios=kept)
    return final, summary
