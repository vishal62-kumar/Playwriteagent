"""Reads the application's own source (.aspx + code-behind) to learn what the page is SUPPOSED to do.

This is what makes the expected results trustworthy: a test may only expect a validation
error if a validator really exists, and may only expect a message if that text appears
in the code.
"""
import os
import re
from pathlib import Path

SKIP_DIRS = {"bin", "obj", ".git", ".vs", "packages", "node_modules"}

TAG_RE = re.compile(r"<asp:(\w+)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)/?>", re.I)
HTML_TAG_RE = re.compile(r"<(input|select|textarea|button)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)/?>", re.I)
ATTR_RE = re.compile(r"([\w:\-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")

VALIDATOR_TYPES = {
    "requiredfieldvalidator": "required",
    "regularexpressionvalidator": "regex",
    "comparevalidator": "compare",
    "rangevalidator": "range",
    "customvalidator": "custom",
}

KEEP_ATTRS = {
    "id", "text", "maxlength", "textmode", "readonly", "enabled", "visible", "causesvalidation",
    "validationgroup", "onclick", "onclientclick", "autopostback", "placeholder", "type", "cssclass",
    "controltovalidate", "errormessage", "validationexpression", "initialvalue", "operator",
    "valuetocompare", "controltocompare", "minimumvalue", "maximumvalue", "display", "required",
}


def list_all_pages(source_dir: str, limit: int = 4000):
    """Every .aspx file under source_dir, as candidate page-path strings for typo-matching.

    For each file we offer TWO candidate spellings: its full path relative to source_dir, and the
    same with the first folder dropped - because in this app the top-level folder (e.g. 'practice',
    'admin') is often a separate IIS application root and doesn't appear in the URL at all (e.g.
    source .../admin/Doctor/Home.aspx is actually served at /Doctor/Home.aspx). Offering both means
    whichever is right has a chance to match; since these are only ever used as *suggestions* that
    still get scanned normally afterward, a wrong guess just surfaces the usual 404, nothing worse.
    """
    out = set()
    if not source_dir or not os.path.isdir(source_dir):
        return out
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for f in files:
            if not f.lower().endswith(".aspx"):
                continue
            rel = Path(root, f).relative_to(source_dir).with_suffix("")
            parts = rel.parts
            out.add("/".join(parts))
            if len(parts) > 1:
                out.add("/".join(parts[1:]))
            if len(out) >= limit:
                return out
    return out


def find_page_files(source_dir: str, page_name: str):
    """Returns (aspx_path, code_behind_path) or (None, None)."""
    if not source_dir or not os.path.isdir(source_dir):
        return None, None
    rel = re.sub(r"\.aspx.*$", "", page_name, flags=re.I).strip("/\\")
    want_tail = [p.lower() for p in re.split(r"[\\/]", rel) if p]
    want_tail[-1] += ".aspx"

    best = None
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for f in files:
            if f.lower() != want_tail[-1]:
                continue
            path = Path(root) / f
            parts = [p.lower() for p in path.parts]
            score = 1 + sum(1 for a, b in zip(reversed(parts), reversed(want_tail)) if a == b)
            if best is None or score > best[0]:
                best = (score, path)
    if not best:
        return None, None
    aspx = best[1]
    for ext in (".cs", ".vb"):
        cb = Path(str(aspx) + ext)
        if cb.exists():
            return str(aspx), str(cb)
    return str(aspx), None


def _attrs(raw: str) -> dict:
    out = {}
    for m in ATTR_RE.finditer(raw):
        key = m.group(1).lower()
        if key in KEEP_ATTRS:
            out[key] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def _strip_comments(text: str) -> str:
    text = re.sub(r"<%--.*?--%>", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def parse_aspx(text: str) -> dict:
    text = _strip_comments(text)
    controls, validators = {}, []
    for m in TAG_RE.finditer(text):
        kind = m.group(1)
        a = _attrs(m.group(2))
        sid = a.get("id")
        if not sid:
            continue
        vtype = VALIDATOR_TYPES.get(kind.lower())
        if vtype:
            validators.append({
                "server_id": sid,
                "kind": vtype,
                "control": a.get("controltovalidate", ""),
                "message": a.get("errormessage") or a.get("text") or "",
                "expression": a.get("validationexpression", ""),
                "group": a.get("validationgroup", ""),
                "initial_value": a.get("initialvalue", ""),
                "details": {k: v for k, v in a.items() if k in ("operator", "valuetocompare", "controltocompare", "minimumvalue", "maximumvalue", "type")},
            })
        else:
            controls[sid] = {"asp_type": kind, **{k: v for k, v in a.items() if k != "id"}}
    for m in HTML_TAG_RE.finditer(text):
        a = _attrs(m.group(2))
        if a.get("id") and a["id"] not in controls:
            controls[a["id"]] = {"asp_type": "html-" + m.group(1).lower(), **{k: v for k, v in a.items() if k != "id"}}
    return {"controls": controls, "validators": validators}


def _method_body(text: str, name: str, max_len: int = 3000):
    # C#: void Name(...) { ... }
    m = re.search(r"void\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", text)
    if m:
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        return text[m.start():i][:max_len]
    # VB.NET: Sub Name(...) ... End Sub
    m = re.search(r"Sub\s+" + re.escape(name) + r"\s*\(.*?End\s+Sub", text, re.S | re.I)
    return m.group(0)[:max_len] if m else None


MSG_LINE_HINT = re.compile(r"\.Text\b|alert|Message|Msg|Startup|Response\.Write|Show|Notify|toastr|Error|Success", re.I)
STRING_RE = re.compile(r"\"((?:[^\"\\]|\\.){3,200})\"")
JS_ALERT_RE = re.compile(r"alert\(\s*\\?['\"](.{3,200}?)\\?['\"]\s*\)")
NOT_MESSAGE = re.compile(r"^\s*(select|insert|update|delete|exec|<script|function|window\.|\$\(|~/|http)", re.I)


def parse_code_behind(text: str, handler_names) -> dict:
    snippets = {}
    for name in ["Page_Load", *handler_names]:
        body = _method_body(text, name)
        if body:
            snippets[name] = body
    search_space = "\n".join(snippets.values()) or text
    messages = set()
    for line in search_space.splitlines():
        if not MSG_LINE_HINT.search(line):
            continue
        for m in JS_ALERT_RE.finditer(line):
            messages.add(m.group(1).strip())
        for m in STRING_RE.finditer(line):
            s = m.group(1).strip()
            if "alert(" in s:
                continue
            if NOT_MESSAGE.search(s) or not re.search(r"[A-Za-z]{2}", s):
                continue
            if " " not in s and not s.istitle():
                continue  # looks like an id / key, not a user message
            messages.add(s)
    return {"handlers": snippets, "messages": sorted(messages)}


SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
INLINE_SCRIPT_RE = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.I | re.S)
JS_STRING_RE = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"")  # every string, so quotes pair correctly
JS_FUNC_RE = re.compile(r"function\s+(\w+)\s*\([^)]*\)\s*\{|(\w+)\s*[:=]\s*function\s*\([^)]*\)\s*\{")
RULE_FUNC_NAME = re.compile(r"valid|save|submit|check|verify", re.I)


def _web_root(aspx_path: Path) -> Path:
    for parent in aspx_path.parents:
        try:
            if any(f.name.lower() == "web.config" for f in parent.iterdir() if f.is_file()):
                return parent
        except OSError:
            break
    return aspx_path.parent


def page_scripts(aspx_path: str, aspx_text: str, max_files: int = 6):
    """Local JavaScript the page loads (CDN files are skipped) + inline <script> blocks."""
    aspx = Path(aspx_path)
    scripts = []
    for src in SCRIPT_SRC_RE.findall(_strip_comments(aspx_text)):
        if re.match(r"^(https?:)?//", src, re.I) or "<%" in src:
            continue
        src = src.split("?")[0].split("#")[0]
        base = _web_root(aspx) if src.startswith("~/") or src.startswith("/") else aspx.parent
        path = (base / src.lstrip("~/").lstrip("/")).resolve()
        if path.exists() and path.suffix.lower() == ".js" and not re.search(r"\.min\.js$", path.name, re.I):
            scripts.append((str(path), path.read_text(encoding="utf-8", errors="ignore")[:400000]))
        if len(scripts) >= max_files:
            break
    inline = "\n".join(INLINE_SCRIPT_RE.findall(aspx_text))
    if inline.strip():
        scripts.append(("<inline scripts in .aspx>", inline))
    return scripts


def parse_js(text: str) -> dict:
    """User-facing messages + the validation/save functions (the page's real rules)."""
    messages = set()
    for m in JS_STRING_RE.finditer(text):
        s = (m.group(1) or m.group(2) or "").strip()
        if not 6 <= len(s) <= 200 or " " not in s or not s[0].isupper() or re.search(r"[<>{}$#=;]|\\[nt]", s):
            continue
        messages.add(s.replace("\\'", "'").replace('\\"', '"'))
    rules = []
    for m in JS_FUNC_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if not RULE_FUNC_NAME.search(name):
            continue
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        rules.append(text[m.start():i][:5000])
    bindings = [ln.strip()[:200] for ln in text.splitlines() if re.search(r"\$\(['\"]#\w+['\"]\)\.on\(\s*['\"](click|change|submit)", ln)]
    return {"messages": messages, "rules": rules, "bindings": bindings[:60]}


def read_source(source_dir: str, page_name: str) -> dict:
    aspx_path, cb_path = find_page_files(source_dir, page_name)
    if not aspx_path:
        return {"found": False}
    aspx_text = Path(aspx_path).read_text(encoding="utf-8", errors="ignore")
    parsed = parse_aspx(aspx_text)
    handlers = sorted({c.get("onclick") for c in parsed["controls"].values() if c.get("onclick") and re.match(r"^\w+$", c["onclick"])})
    code = {"handlers": {}, "messages": []}
    if cb_path:
        code = parse_code_behind(Path(cb_path).read_text(encoding="utf-8", errors="ignore"), handlers)

    js_files, js_messages, js_rules, js_bindings = [], set(), [], []
    for path, text in page_scripts(aspx_path, aspx_text):
        js = parse_js(text)
        js_files.append(path)
        js_messages |= js["messages"]
        js_rules += js["rules"]
        js_bindings += js["bindings"]
    rules_text = ""
    if js_bindings:
        rules_text += "// event bindings\n" + "\n".join(js_bindings) + "\n\n"
    for r in js_rules:
        if len(rules_text) > 14000:
            break
        rules_text += r + "\n\n"
    return {"found": True, "aspx_path": aspx_path, "code_behind_path": cb_path, **parsed, **code,
            "messages": sorted(set(code["messages"]) | js_messages),
            "script_files": js_files, "script_rules": rules_text}
