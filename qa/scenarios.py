"""Step 3: generate test scenarios with the LLM, then VALIDATE them against the inventory.

The LLM never writes Python. It writes structured steps that reference element keys from
the inventory. Anything that refers to a non-existent element, uses an impossible action,
or expects behaviour the code does not implement is rejected (and sent back once for a fix).
"""
import json
import re
from datetime import datetime

from . import config
from .llm import ask_json

ACTIONS = {
    "fill": {"text"}, "clear": {"text"}, "select": {"select"}, "check": {"checkbox", "radio"},
    "uncheck": {"checkbox"}, "click": None, "press": None, "upload": {"file"}, "wait": "none",
}
ASSERTIONS = {
    "validator_fires", "validator_passes", "message_shown", "element_visible", "element_hidden",
    "element_enabled", "element_disabled", "element_value", "element_checked", "element_unchecked",
    "url_contains", "no_server_error",
}

SYSTEM_PROMPT = """You are a senior QA engineer designing UI tests for an ASP.NET WebForms page.
You get a VERIFIED INVENTORY of the page: its elements (by key), its validators, the messages its
code can show, and the code-behind of its event handlers.

Return JSON: {"scenarios": [ ... ]}. Each scenario:
{
  "title": "short description",
  "type": "positive" | "negative" | "boundary" | "ui",
  "writes_data": true/false   (true if it saves/updates/deletes a record),
  "basis": "which validator / handler / element proves the expected result",
  "steps": [ {"action": "...", "element": "<key>", "value": "..."} ],
  "assertions": [ {"type": "...", "validator": "<validator key>" | "element": "<key>", "value": "..."} ]
}

Actions: fill(element,value) clear(element) select(element,value=option label) check(element)
uncheck(element) click(element) press(element,value=key e.g. Enter) upload(element) wait(value=ms)
Assertions: validator_fires(validator) validator_passes(validator) message_shown(value=exact text)
element_visible/element_hidden/element_enabled/element_disabled/element_checked/element_unchecked(element)
element_value(element,value) url_contains(value) no_server_error()

STRICT RULES
1. Use ONLY element keys and validator keys that appear in the inventory. Never invent fields.
2. Expected results must be PROVEN by the inventory:
   - validator_fires only for a validator that exists, after clicking the button that triggers its
     validation group. A "required" validator fires ONLY when its field is empty. A "regex" validator
     fires ONLY for a NON-EMPTY value that does not match its expression (it ignores empty values).
   - message_shown only with a text copied exactly from known_messages.
   - Many pages validate in JavaScript (see client_script_validation): read those functions to learn
     which input produces which message, then assert that message with message_shown.
   - If you cannot prove an outcome, assert only no_server_error (a smoke test).
   - "fill" REPLACES the whole value of a field. Never fill the same field twice in a row; to test a
     long value, fill it once with the full string. Text longer than maxlength is cut to maxlength.
   - element_value on a dropdown uses the option LABEL you selected.
   - Do not write tests that only repeat an action and check it (select X then expect X) unless the
     page logic could change it; prefer tests whose result the code decides.
3. Elements with "visible": false are hidden at load - click the element that reveals them first,
   or do not use them.
4. Prefer elements with "in_page_source": true (they belong to this page, not the menu/master page).
5. Do not navigate away from the page (no menu/logout links). The test runner opens the page itself.
6. Cover: each validator (fires + passes), each dropdown, each main button, a valid end-to-end
   submission if the handler code shows what success looks like, and boundaries (maxlength).
7. When testing ONE field's validator with validator_fires, that field must be the ONLY invalid/empty
   thing on the page when you click the button: fill every OTHER required field in the same
   validation group with ordinary VALID data first (pick any sensible value/option). Many of these
   pages validate fields one at a time and stop at the first problem, so if you leave other required
   fields empty too, THEIR message appears instead and the one you actually meant to test never
   shows - the test will then fail even though your reasoning was correct. The field under test is
   the only exception - leave IT empty (for a required-field test) or give it the one bad value
   you're testing (for a regex/boundary test).
8. Never "upload" a file in a scenario whose point is that NO file was chosen (and never assert a
   file field is empty/"No file chosen" if an earlier step uploaded something to it) - upload and
   "no file" are opposite conditions and contradict each other in the same scenario.
"""


def _compact_inventory(inv: dict) -> dict:
    els = {}
    for k, e in inv["elements"].items():
        d = {"kind": e["kind"], "label": e["label"], "visible": e["visible"], "in_page_source": e["in_page_source"]}
        if not e["enabled"] or e["readonly"]:
            d["disabled_or_readonly"] = True
        if e.get("options"):
            d["options"] = [o["label"] for o in e["options"]][:20]
        if e.get("maxlength"):
            d["maxlength"] = e["maxlength"]
        if e.get("initial_value"):
            d["initial_value"] = e["initial_value"]
        if e["kind"] in ("button", "link") and e.get("causes_validation") is not None:
            d["triggers_validation_group"] = e.get("group") if e.get("causes_validation") else None
        els[k] = d
    vals = {k: {kk: v[kk] for kk in ("kind", "control", "message", "expression", "group") if v.get(kk) not in (None, "")}
            for k, v in inv["validators"].items()}
    handlers = ""
    for name, body in inv["source"].get("handlers", {}).items():
        if len(handlers) > 9000:
            break
        handlers += f"\n// ---- {name}\n{body[:3000]}\n"
    return {"page": inv["page"], "url": inv["url"], "elements": els, "validators": vals,
            "known_messages": inv["known_messages"][:150], "code_behind_handlers": handlers or "(source not available)",
            "client_script_validation": inv["source"].get("script_rules") or "(none found)",
            "visible_page_text": inv["page_text"][:1500]}


def _value_at_click(steps, upto: int, element: str, initial: str):
    value = initial or ""
    for s in steps[:upto]:
        if s.get("element") == element:
            if s["action"] == "fill" or s["action"] == "select":
                value = str(s.get("value", ""))
            elif s["action"] == "clear" or s["action"] == "uncheck":
                value = ""
            elif s["action"] == "check":
                value = "on"
    return value


def _regex_full_match(expression: str, value: str):
    try:
        m = re.search(expression, value)
    except re.error:
        return None  # cannot evaluate this JS regex in Python - do not judge
    return bool(m and m.group(0) == value)


def validate(sc: dict, inv: dict):
    """Returns (cleaned_scenario, errors, notes)."""
    errors, notes = [], []
    els, vals = inv["elements"], inv["validators"]
    steps = sc.get("steps") or []
    if not isinstance(steps, list) or not steps:
        errors.append("scenario has no steps")
        steps = []

    clean_steps = []
    clicked = False
    for i, s in enumerate(steps, 1):
        act = s.get("action")
        key = s.get("element")
        if act not in ACTIONS:
            errors.append(f"step {i}: unknown action '{act}'")
            continue
        if act == "wait":
            clean_steps.append({"action": "wait", "value": min(int(re.sub(r"\D", "", str(s.get("value", 1000))) or 1000), 5000)})
            continue
        if key not in els:
            errors.append(f"step {i}: element '{key}' does not exist on the page")
            continue
        el = els[key]
        allowed = ACTIONS[act]
        if allowed and el["kind"] not in allowed:
            errors.append(f"step {i}: cannot '{act}' a {el['kind']} element ('{key}')")
            continue
        if act in ("fill", "clear", "select", "check", "uncheck", "upload") and (not el["enabled"] or el["readonly"]):
            errors.append(f"step {i}: '{key}' is disabled/read-only")
            continue
        if not el["visible"] and not clicked:
            errors.append(f"step {i}: '{key}' is hidden when the page loads - click the control that reveals it first")
            continue
        if act == "select" and el.get("options"):
            opt = str(s.get("value", ""))
            labels = {o["label"] for o in el["options"]}
            values = {o["value"] for o in el["options"]}
            if opt not in labels and opt not in values:
                real = [o["label"] for o in el["options"] if o["value"] not in ("", "0", "-1")]
                errors.append(f"step {i}: option '{opt}' is not in '{key}'. Real options: {real[:10]}")
                continue
        if act == "fill" and clean_steps and clean_steps[-1]["action"] == "fill" and clean_steps[-1]["element"] == key:
            errors.append(f"step {i}: fills '{key}' again right after filling it - fill REPLACES the value, use one fill with the full text")
            continue
        if act == "click":
            clicked = True
        step = {"action": act, "element": key}
        if act in ("fill", "select", "press"):
            step["value"] = str(s.get("value", ""))
            if act == "press" and not step["value"]:
                step["value"] = "Enter"
        clean_steps.append(step)

    clean_asserts = []
    for a in sc.get("assertions") or []:
        t = a.get("type")
        if t not in ASSERTIONS:
            errors.append(f"unknown assertion type '{t}'")
            continue
        if t in ("validator_fires", "validator_passes"):
            vkey = a.get("validator")
            if vkey not in vals:
                errors.append(f"validator '{vkey}' does not exist")
                continue
            v = vals[vkey]
            clicks = [(i, s) for i, s in enumerate(clean_steps) if s["action"] in ("click", "press")]
            if not clicks:
                errors.append(f"{t} '{vkey}': no button is clicked, so validation never runs")
                continue
            idx, last = clicks[-1]
            btn = els.get(last["element"], {})
            if btn.get("causes_validation") is False and t == "validator_fires":
                errors.append(f"{t} '{vkey}': button '{last['element']}' does not trigger validation")
                continue
            if btn.get("group") is not None and btn.get("causes_validation") and (btn.get("group") or "") != (v.get("group") or "") and t == "validator_fires":
                errors.append(f"{t} '{vkey}': validator group '{v.get('group')}' differs from button '{last['element']}' group '{btn.get('group')}'")
                continue
            if t == "validator_fires":
                # Masking check: if another REQUIRED validator in the same group is still empty when
                # the button is clicked, many apps check fields one at a time and stop at the first
                # failure - so vkey's own message may never appear, even though vkey itself is correctly
                # set up to fail. Catch this at validation time instead of only after a real run.
                group = v.get("group") or ""
                masked_by = None
                for okey, ov in vals.items():
                    if okey == vkey or ov.get("kind") != "required":
                        continue
                    if (ov.get("group") or "") != group:
                        continue
                    ctrl = ov.get("control")
                    if not ctrl or ctrl == v.get("control") or ctrl not in els:
                        continue
                    oinit = ov.get("initial_value") or ""
                    oval = _value_at_click(clean_steps, idx, ctrl, els[ctrl].get("initial_value", ""))
                    if oval.strip() == oinit.strip():
                        masked_by = (okey, ctrl)
                        break
                if masked_by:
                    errors.append(
                        f"{t} '{vkey}': field '{masked_by[1]}' is still empty, and its own required "
                        f"validator '{masked_by[0]}' is in the same group - that will likely fire/show "
                        f"first and hide '{vkey}'. Fill '{masked_by[1]}' with valid data first, so "
                        f"'{vkey}' is the only thing left wrong on the page."
                    )
                    continue
            if v.get("control"):
                initial = v.get("initial_value") or ""
                value = _value_at_click(clean_steps, idx, v["control"], els.get(v["control"], {}).get("initial_value", ""))
                if v["kind"] == "required":
                    empty = value.strip() == initial.strip()
                    if t == "validator_fires" and not empty:
                        errors.append(f"required validator '{vkey}' cannot fire: field '{v['control']}' has value {value!r}")
                        continue
                    if t == "validator_passes" and empty:
                        errors.append(f"required validator '{vkey}' WILL fire: field '{v['control']}' is empty")
                        continue
                elif v["kind"] == "regex" and v.get("expression"):
                    if value == "":
                        if t == "validator_fires":
                            errors.append(f"regex validator '{vkey}' never fires on an empty field - use a non-empty invalid value")
                            continue
                    else:
                        ok = _regex_full_match(v["expression"], value)
                        if ok is True and t == "validator_fires":
                            errors.append(f"value {value!r} MATCHES the pattern of '{vkey}' ({v['expression']}), so it will not fire")
                            continue
                        if ok is False and t == "validator_passes":
                            errors.append(f"value {value!r} does NOT match the pattern of '{vkey}' ({v['expression']}), so it will fire")
                            continue
            clean_asserts.append({"type": t, "validator": vkey})
        elif t == "message_shown":
            text = str(a.get("value", "")).strip()
            known = inv["known_messages"]
            match = next((m for m in known if m.lower() == text.lower()), None) or \
                next((m for m in known if len(text) >= 4 and text.lower() in m.lower()), None)
            if not match:
                notes.append(f"dropped ungrounded expectation: message {text!r} does not appear in the page's code")
                continue
            if match.lower() in (inv.get("page_text") or "").lower():
                notes.append(f"dropped expectation: message {match!r} is already visible before the test acts, so it proves nothing")
                continue
            clean_asserts.append({"type": t, "value": match})
        elif t.startswith("element_"):
            key = a.get("element")
            if key not in els:
                errors.append(f"assertion on element '{key}' which does not exist")
                continue
            item = {"type": t, "element": key}
            if t == "element_value":
                want = str(a.get("value", ""))
                el = els[key]
                if el["kind"] == "select" and el.get("options"):
                    match = next((o for o in el["options"] if want in (o["label"], o["value"])), None)
                    if not match:
                        errors.append(f"element_value: '{want}' is not an option of '{key}'")
                        continue
                    want = match["label"]
                elif el["kind"] == "text":
                    fills = [st for st in clean_steps if st.get("element") == key and st["action"] in ("fill", "clear")]
                    if fills:
                        typed = fills[-1].get("value", "") if fills[-1]["action"] == "fill" else ""
                        if el.get("maxlength"):
                            typed = typed[: el["maxlength"]]
                        if want != typed:
                            errors.append(f"element_value: expects {want!r} in '{key}' but the steps leave {typed!r} there")
                            continue
                item["value"] = want
            clean_asserts.append(item)
        elif t == "url_contains":
            clean_asserts.append({"type": t, "value": str(a.get("value", ""))})
        else:
            clean_asserts.append({"type": t})

    if not any(a["type"] == "no_server_error" for a in clean_asserts):
        clean_asserts.append({"type": "no_server_error"})
    stype = sc.get("type", "ui")
    if all(a["type"] == "no_server_error" for a in clean_asserts):
        stype = "smoke"
        notes.append("no provable expected result - runs as a smoke test (no crash / server error)")

    cleaned = {
        "title": str(sc.get("title", "Untitled scenario"))[:150],
        "type": stype,
        "writes_data": bool(sc.get("writes_data", False)),
        "basis": str(sc.get("basis", ""))[:300],
        "steps": clean_steps,
        "assertions": clean_asserts,
        "notes": notes,
    }
    return cleaned, errors, notes


def _abbr(page_name: str) -> str:
    last = re.sub(r"\.aspx.*$", "", page_name, flags=re.I).split("/")[-1]
    caps = "".join(c for c in last if c.isupper())
    return (caps if len(caps) >= 2 else last[:4]).upper()


TESTABLE_FIELD_KINDS = {"text", "select", "checkbox", "radio", "file"}
MAX_FULL_COVERAGE_OBJECTS = 30  # bounds LLM calls/time on very large pages


def generate_full_coverage(inv: dict, per_object_count: int = 4, id_start: int = 1) -> dict:
    """Generates test coverage automatically for EVERY testable field and action button on the
    page - no object name or test count to type in. One grounded batch of scenarios is requested
    per object (so coverage doesn't depend on a single prompt happening to mention everything),
    and each resulting scenario is tagged with the object it covers so it can be shown/reviewed
    grouped under a heading for that object, rather than as one flat list.
    """
    fields = [(k, e) for k, e in inv["elements"].items()
              if e["kind"] in TESTABLE_FIELD_KINDS and e["visible"] and e["in_page_source"]]
    buttons = [(k, e) for k, e in inv["elements"].items()
               if e["kind"] == "button" and e["visible"] and e.get("causes_validation")]
    objects = fields + buttons
    truncated = len(objects) > MAX_FULL_COVERAGE_OBJECTS
    objects = objects[:MAX_FULL_COVERAGE_OBJECTS]

    next_id = id_start
    all_scenarios, all_rejected, seen_groups = [], [], []
    for i, (key, el) in enumerate(objects, 1):
        label = el["label"] or key
        is_button = el["kind"] == "button"
        focus = f"clicking the '{label}' button, trying it with different states of the fields it validates" \
            if is_button else f"the '{label}' field only (element key: {key}) - every input it can realistically receive"
        print(f"      [{i}/{len(objects)}] Asking {config.OPENAI_MODEL} about '{label}' ...")
        count = max(2, per_object_count - 2) if is_button else per_object_count
        doc = generate(inv, count=count, focus=focus, id_start=next_id)
        for sc in doc["scenarios"]:
            sc["group"] = label
        all_scenarios += doc["scenarios"]
        all_rejected += doc["rejected"]
        next_id += len(doc["scenarios"])
        if doc["scenarios"] and label not in seen_groups:
            seen_groups.append(label)

    if truncated:
        print(f"      Note: this page has more than {MAX_FULL_COVERAGE_OBJECTS} testable objects - "
              f"covered the first {MAX_FULL_COVERAGE_OBJECTS}; use --focus for the rest.")
    return {
        "page": inv["page"], "url": inv["url"], "focus": "",
        "generated_at": datetime.now().isoformat(timespec="seconds"), "model": config.OPENAI_MODEL,
        "inventory_scanned_at": inv["scanned_at"], "scenarios": all_scenarios, "rejected": all_rejected,
        "groups": seen_groups,
    }


def generate(inv: dict, count: int = 12, focus: str = "", id_start: int = 1) -> dict:
    compact = _compact_inventory(inv)
    if focus:
        ask = (f"The tester wants to test ONLY this:\n  \"{focus}\"\n"
               f"Design up to {count} scenarios that cover exactly that request (positive, negative and boundary cases "
               f"for it). Do not add scenarios about other parts of the page. If part of the request is not possible "
               f"on this page, skip that part.")
    else:
        ask = f"Design {count} test scenarios covering the whole page."
    user = f"{ask}\n\nINVENTORY:\n{json.dumps(compact, indent=1, ensure_ascii=False)}"
    print(f"      Asking {config.OPENAI_MODEL} for {count} scenarios ...")
    raw = ask_json(SYSTEM_PROMPT, user).get("scenarios", [])
    return finalize(inv, raw, retry=True, id_start=id_start, focus=focus)


def finalize(inv: dict, raw_scenarios: list, retry: bool = False, id_start: int = 1, keep_ids: bool = False,
             focus: str = "") -> dict:
    good, bad = [], []
    for sc in raw_scenarios:
        cleaned, errors, _ = validate(sc, inv)
        (bad if errors else good).append((sc, cleaned, errors))

    if bad and retry:
        print(f"      {len(bad)} scenario(s) failed validation - asking the model to fix them ...")
        fix_request = "These scenarios are INVALID for the inventory. Fix each one (or replace it) and return {\"scenarios\": [...]}.\n\n"
        for sc, _, errors in bad:
            fix_request += json.dumps({"scenario": sc, "problems": errors}, ensure_ascii=False) + "\n"
        fix_request += "\nINVENTORY:\n" + json.dumps(_compact_inventory(inv), ensure_ascii=False)
        try:
            fixed = ask_json(SYSTEM_PROMPT, fix_request).get("scenarios", [])
        except Exception as e:
            print(f"      Could not get fixes: {e}")
            fixed = []
        still_bad = []
        for sc in fixed:
            cleaned, errors, _ = validate(sc, inv)
            (still_bad if errors else good).append((sc, cleaned, errors))
        for _, _, errors in bad:
            errors.append("(model was asked to fix this; see accepted scenarios for its replacement)")
        bad = bad + still_bad

    abbr = _abbr(inv["page"])
    scenarios = []
    for n, (orig, cleaned, _) in enumerate(good, id_start):
        cleaned["id"] = orig["id"] if keep_ids and orig.get("id") else f"TC_{abbr}_{n:03d}"
        if orig.get("group"):
            cleaned["group"] = orig["group"]  # preserve full-coverage grouping across re-runs/edits
        scenarios.append(cleaned)
    doc = {
        "page": inv["page"], "url": inv["url"], "focus": focus,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": config.OPENAI_MODEL,
        "inventory_scanned_at": inv["scanned_at"],
        "scenarios": scenarios,
        "rejected": [{"scenario": sc, "problems": errors} for sc, _, errors in bad],
    }
    return doc


def save(doc: dict):
    config.SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SCENARIO_DIR / f"{config.page_slug(doc['page'])}.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load(page_name: str) -> dict:
    path = config.SCENARIO_DIR / f"{config.page_slug(page_name)}.json"
    return json.loads(path.read_text(encoding="utf-8"))
