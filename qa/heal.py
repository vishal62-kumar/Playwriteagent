"""Step 5: heal SCRIPT_ERROR tests only.

Healing may change HOW a test reaches the expected state (its steps), never WHAT it expects
(its assertions). A change that would alter the assertions is rejected, so healing cannot
turn a real bug into a green test. APP_DEFECT and ENVIRONMENT failures are never healed.
"""
import json

from .llm import ask_json
from .scenarios import _compact_inventory, validate

HEAL_PROMPT = """You repair UI test STEPS for an ASP.NET WebForms page. A test failed because a step
could not be performed (element hidden, disabled, renamed, or a prerequisite was missing).

Return JSON: {"steps": [ ... ]} - the full corrected list of steps for the scenario, using the same
step format: {"action": "...", "element": "<key>", "value": "..."}.
- Use ONLY element keys from the inventory.
- Elements with "visible": false must be revealed first (e.g. click the trigger/tab/panel that opens them).
- Keep the intent of the scenario. Do NOT add, remove or change assertions - you only return steps.
If the scenario cannot be performed on this page, return {"steps": [], "impossible": "reason"}.
"""


def heal_scenario(sc: dict, inv: dict, failure: dict):
    """Returns (healed_scenario | None, message)."""
    failed_step = next((s for s in failure.get("steps", []) if s["status"] == "failed"), None)
    request = {
        "scenario": {k: sc[k] for k in ("title", "steps", "assertions", "basis")},
        "failed_step": failed_step["action"] if failed_step else None,
        "error": failure.get("reason"),
        "inventory": _compact_inventory(inv),
    }
    try:
        resp = ask_json(HEAL_PROMPT, json.dumps(request, ensure_ascii=False))
    except Exception as e:
        return None, f"heal request failed: {e}"
    if resp.get("impossible") or not resp.get("steps"):
        return None, f"model says the scenario is not possible: {resp.get('impossible', 'no steps returned')}"

    candidate = dict(sc, steps=resp["steps"])
    cleaned, errors, _ = validate(candidate, inv)
    if errors:
        return None, "healed steps are invalid: " + "; ".join(errors[:3])
    if cleaned["assertions"] != sc["assertions"]:
        return None, "healing would change the expected results - rejected"
    healed = dict(sc, steps=cleaned["steps"])
    healed["notes"] = list(sc.get("notes", [])) + ["steps healed after: " + (failure.get("reason") or "")[:150]]
    return healed, "steps rewritten"
