"""Step 2: build a verified inventory of the page.

For every element we record a selector that was CHECKED to match exactly one element on the
live page. Generated tests can only use elements from this inventory, so they can no longer
invent fields like `_txtEmail` that do not exist.
"""
import json
import re
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

from . import config
from .source_parser import read_source

ELEMENTS_JS = r"""
() => {
  const isVisible = (el) => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const labelFor = (el) => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const t0 = (el.getAttribute('type') || '').toLowerCase();
    if (t0 === 'checkbox' || t0 === 'radio') {
      const n = el.nextSibling;
      if (n && n.nodeType === 3 && clean(n.textContent)) return clean(n.textContent).slice(0, 60);
    }
    if (['BUTTON', 'A'].includes(el.tagName) || ['submit', 'button', 'reset', 'image'].includes(t0)) {
      const own = clean(el.tagName === 'INPUT' ? el.value : el.innerText);
      if (own) return own;
    }
    for (const a of ['aria-label', 'placeholder', 'title']) if (el.getAttribute(a)) return clean(el.getAttribute(a));
    const td = el.closest('td');
    if (td && td.previousElementSibling) { const t = clean(td.previousElementSibling.innerText); if (t && t.length < 60) return t; }
    let prev = el.previousElementSibling;
    if (prev && !['INPUT','SELECT','TEXTAREA','BUTTON'].includes(prev.tagName)) { const t = clean(prev.innerText); if (t && t.length < 60) return t; }
    const parent = el.parentElement;
    if (parent && parent.querySelectorAll('input:not([type=hidden]), select, textarea, button').length === 1) {
      const lab = parent.querySelector('label');
      const t = clean(lab ? lab.innerText : parent.innerText);
      if (t && t.length < 60 && t !== clean(el.innerText)) return t;
    }
    const field = el.closest('.form-group, .field, [class*=field]');
    if (field) { const lab = field.querySelector('label'); if (lab) return clean(lab.innerText); }
    return '';
  };
  const labelOf = (el) => labelFor(el).replace(/\s*\*\s*$/, '');
  const seen = new Set();
  const out = [];
  const nodes = document.querySelectorAll('input, select, textarea, button, a, [onclick], [role=button]');
  for (const el of nodes) {
    if (seen.has(el)) continue; seen.add(el);
    if (el.closest('script, template, noscript')) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type === 'hidden') continue;
    const text = clean(tag === 'input' ? (el.value || '') : el.innerText).slice(0, 80);
    if (tag === 'a' && !text && !el.id) continue;
    const item = {
      tag, type, id: el.id || '', name: el.getAttribute('name') || '', text,
      label: labelOf(el).slice(0, 80),
      placeholder: el.getAttribute('placeholder') || '',
      visible: isVisible(el), enabled: !el.disabled, readonly: !!el.readOnly,
      required: !!el.required, maxlength: el.maxLength > 0 ? el.maxLength : null,
      value: (tag === 'input' && type !== 'password' && type !== 'file') || tag === 'textarea' ? (el.value || '').slice(0, 80) : '',
      checked: (type === 'checkbox' || type === 'radio') ? !!el.checked : null,
      onclick: (el.getAttribute('onclick') || '').slice(0, 400),
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 300) : '',
      role: el.getAttribute('role') || '',
      options: tag === 'select' ? Array.from(el.options).slice(0, 30).map(o => ({value: o.value, label: clean(o.text)})) : null,
    };
    if (tag === 'select') item.value = el.value;
    out.push(item);
    if (out.length >= 500) break;
  }
  return out;
}
"""

VALIDATORS_JS = r"""
() => {
  const kindOf = (f) => {
    const n = typeof f === 'function' ? f.name : String(f || '');
    if (/Required/i.test(n)) return 'required';
    if (/RegularExpression/i.test(n)) return 'regex';
    if (/Compare/i.test(n)) return 'compare';
    if (/Range/i.test(n)) return 'range';
    if (/Custom/i.test(n)) return 'custom';
    return n || 'unknown';
  };
  const out = {};
  for (const v of (window.Page_Validators || [])) {
    out[v.id] = { id: v.id, kind: kindOf(v.evaluationfunction), control: v.controltovalidate || '',
      message: v.errormessage || (v.innerText || '').trim(), expression: v.validationexpression || '',
      group: v.validationGroup || '', initial_value: v.initialvalue || '', display: v.display || '' };
  }
  document.querySelectorAll('[data-val-controltovalidate]').forEach(v => {
    if (out[v.id]) return;
    out[v.id] = { id: v.id, kind: kindOf(v.getAttribute('data-val-evaluationfunction')),
      control: v.getAttribute('data-val-controltovalidate') || '',
      message: v.getAttribute('data-val-errormessage') || (v.innerText || '').trim(),
      expression: v.getAttribute('data-val-validationexpression') || '',
      group: v.getAttribute('data-val-validationgroup') || '', initial_value: v.getAttribute('data-val-initialvalue') || '',
      display: v.getAttribute('data-val-display') || '' };
  });
  return Object.values(out);
}
"""

CONTAINER_SEG = re.compile(r"^(ctl\d+|content\w*|maincontent\w*|\w*placeholder\w*|\w*updatepanel\w*|cph\w*|body\w*)$", re.I)
POSTBACK_OPTS = re.compile(r"WebForm_PostBackOptions\(\s*(?:&quot;|\")([^\"&]*)(?:&quot;|\")\s*,\s*(?:&quot;|\")[^\"&]*(?:&quot;|\")\s*,\s*(true|false)\s*,\s*(?:&quot;|\")([^\"&]*)(?:&quot;|\")", re.I)
CLIENT_VALIDATE = re.compile(r"Page_ClientValidate\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)")


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()
    return s[:30] or "item"


def _kind(el: dict) -> str:
    tag, typ = el["tag"], el["type"]
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "text"
    if tag == "input":
        if typ in ("checkbox", "radio"):
            return typ
        if typ in ("submit", "button", "image", "reset"):
            return "button"
        if typ == "file":
            return "file"
        return "text"
    if tag == "button" or el.get("role") == "button" or (el.get("onclick") and tag != "a"):
        return "button"
    if tag == "a":
        return "link"
    return "other"


def _key_from_client_id(client_id: str, server_ids) -> str:
    # Prefer the real server-side ID from the .aspx (e.g. txtDoctorName)
    best = None
    for sid in server_ids:
        if client_id == sid or client_id.endswith("_" + sid):
            if best is None or len(sid) > len(best):
                best = sid
    if best:
        return best
    parts = client_id.split("_")
    while len(parts) > 1 and (CONTAINER_SEG.match(parts[0]) or parts[0] == ""):
        parts.pop(0)
    return "_".join(parts) or client_id


def _selector_candidates(el: dict, kind: str):
    if el["id"]:
        yield {"by": "css", "value": f'[id="{el["id"]}"]'}
    if el["name"]:
        yield {"by": "css", "value": f'{el["tag"]}[name="{el["name"]}"]' + (f'[value="{el["value"]}"]' if kind == "radio" and el["value"] else "")}
    if kind in ("button", "link") and el["text"]:
        yield {"by": "role", "role": "button" if kind == "button" else "link", "name": el["text"]}
        yield {"by": "text", "value": el["text"]}
    if el["label"] and kind in ("text", "select", "checkbox", "radio", "file"):
        yield {"by": "label", "value": el["label"]}
    if el["placeholder"]:
        yield {"by": "placeholder", "value": el["placeholder"]}


def resolve(page, spec: dict):
    """Turns a stored selector spec into a Playwright locator (same logic used by tests)."""
    by = spec["by"]
    if by == "css":
        return page.locator(spec["value"])
    if by == "role":
        return page.get_by_role(spec["role"], name=spec["name"], exact=True)
    if by == "text":
        return page.get_by_text(spec["value"], exact=True)
    if by == "label":
        return page.get_by_label(spec["value"], exact=True)
    if by == "placeholder":
        return page.get_by_placeholder(spec["value"], exact=True)
    raise ValueError(f"Unknown selector type {by}")


def _button_validation(el: dict, src_ctrl: dict):
    """Which validation group a button triggers (None = unknown)."""
    info = {"causes_validation": None, "group": None}
    m = POSTBACK_OPTS.search(el.get("onclick", "") or "") or POSTBACK_OPTS.search(el.get("href", "") or "")
    if m:
        info["causes_validation"] = m.group(2).lower() == "true"
        info["group"] = m.group(3)
    m2 = CLIENT_VALIDATE.search(el.get("onclick", "") or "")
    if m2:
        info["causes_validation"] = True
        info["group"] = m2.group(1) or ""
    if src_ctrl:
        if "causesvalidation" in src_ctrl:
            info["causes_validation"] = src_ctrl["causesvalidation"].lower() != "false"
        if "validationgroup" in src_ctrl:
            info["group"] = src_ctrl["validationgroup"]
        elif info["causes_validation"] and info["group"] is None:
            info["group"] = ""
    return info


class ScanError(Exception):
    pass


def scan(page_name: str, use_source: bool = True, headless: bool = True) -> dict:
    url = config.page_url(page_name)
    src = read_source(config.SOURCE_DIR, page_name) if use_source else {"found": False}
    server_ids = list(src.get("controls", {}).keys()) + [v["server_id"] for v in src.get("validators", [])]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(config.STATE_FILE) if config.STATE_FILE.exists() else None)
        page = context.new_page()
        page.set_default_timeout(config.ACTION_TIMEOUT)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        status = resp.status if resp else None
        body_text = page.inner_text("body")
        if status and status >= 400:
            browser.close()
            raise ScanError(f"{url} returned HTTP {status}. Check the page name (it must match the URL path).")
        if "Server Error in" in body_text:
            browser.close()
            raise ScanError(f"{url} shows an ASP.NET 'Server Error' page:\n{body_text[:500]}")
        if page.locator(config.LOGIN_USER_SELECTOR).count() and page.locator(config.LOGIN_USER_SELECTOR).first.is_visible():
            browser.close()
            raise ScanError("The page redirected to the login screen - the saved session is not valid. Run again (preflight logs in).")

        raw = page.evaluate(ELEMENTS_JS)
        raw_validators = page.evaluate(VALIDATORS_JS)

        elements, keys_by_client_id, skipped = {}, {}, []
        for el in raw:
            kind = _kind(el)
            if kind == "other":
                continue
            chosen = None
            for spec in _selector_candidates(el, kind):
                try:
                    if resolve(page, spec).count() == 1:
                        chosen = spec
                        break
                except Exception:
                    continue
            if not chosen:
                skipped.append(el["id"] or el["name"] or el["text"] or el["label"])
                continue
            if el["id"]:
                key = _key_from_client_id(el["id"], server_ids)
            else:
                key = f"{kind}_{_slug(el['text'] or el['label'] or el['name'] or el['placeholder'])}"
            base, n = key, 2
            while key in elements:
                key, n = f"{base}_{n}", n + 1
            server_id = key if key in src.get("controls", {}) else None
            src_ctrl = src.get("controls", {}).get(server_id, {}) if server_id else {}
            item = {
                "kind": kind,
                "selector": chosen,
                "label": el["label"] or el["text"] or el["placeholder"],
                "text": el["text"],
                "client_id": el["id"],
                "visible": el["visible"],
                "enabled": el["enabled"],
                "readonly": el["readonly"],
                "initial_value": el["value"],
                "maxlength": el["maxlength"] or (int(src_ctrl["maxlength"]) if str(src_ctrl.get("maxlength", "")).isdigit() else None),
                "in_page_source": bool(server_id),
            }
            if el["checked"] is not None:
                item["checked"] = el["checked"]
            if el["options"] is not None:
                item["options"] = el["options"]
            if kind == "button" or kind == "link":
                item.update(_button_validation(el, src_ctrl))
                if src_ctrl.get("onclick"):
                    item["server_handler"] = src_ctrl["onclick"]
            if src_ctrl.get("textmode"):
                item["textmode"] = src_ctrl["textmode"]
            elements[key] = item
            if el["id"]:
                keys_by_client_id[el["id"]] = key

        # Validators: runtime (what the browser really enforces) merged with source (.aspx)
        validators = {}
        src_validators = {v["server_id"]: v for v in src.get("validators", [])}
        for v in raw_validators:
            key = _key_from_client_id(v["id"], server_ids)
            sv = src_validators.pop(key, {})
            control_key = keys_by_client_id.get(v["control"]) or _key_from_client_id(v["control"], server_ids)
            validators[key] = {
                "kind": v["kind"] if v["kind"] != "unknown" else sv.get("kind", "unknown"),
                "client_id": v["id"],
                "control": control_key if control_key in elements else None,
                "control_client_id": v["control"],
                "message": v["message"] or sv.get("message", ""),
                "expression": v["expression"] or sv.get("expression", ""),
                "group": v["group"] if v["group"] else sv.get("group", ""),
                "initial_value": v["initial_value"] or sv.get("initial_value", ""),
                "client_side": True,
            }
        for sid, sv in src_validators.items():  # validators that only run on the server
            validators[sid] = {
                "kind": sv["kind"], "client_id": None,
                "control": sv["control"] if sv["control"] in elements else None,
                "control_client_id": None, "message": sv["message"], "expression": sv["expression"],
                "group": sv["group"], "initial_value": sv["initial_value"], "client_side": False,
            }
        browser.close()

    messages = sorted({v["message"] for v in validators.values() if v["message"]} | set(src.get("messages", [])))
    inventory = {
        "page": page_name,
        "url": url,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "elements": elements,
        "validators": validators,
        "known_messages": messages,
        "page_text": body_text[:3000],
        "source": {
            "found": src.get("found", False),
            "aspx_path": src.get("aspx_path"),
            "code_behind_path": src.get("code_behind_path"),
            "handlers": src.get("handlers", {}),
            "script_files": src.get("script_files", []),
            "script_rules": src.get("script_rules", ""),
        },
        "stats": {"elements": len(elements), "validators": len(validators), "skipped_not_unique": skipped[:50]},
    }
    config.INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
    out = config.INVENTORY_DIR / f"{config.page_slug(page_name)}.json"
    out.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
    inventory["_path"] = str(out)
    return inventory


def load(page_name: str) -> dict:
    path = config.INVENTORY_DIR / f"{config.page_slug(page_name)}.json"
    return json.loads(path.read_text(encoding="utf-8"))
