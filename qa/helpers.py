"""Runtime helpers used by every generated test.

Each action/assertion is logged as a numbered step, and every failure raises one of three
exception types so the report can say WHY a test failed:

  EnvError   -> ENVIRONMENT : app down, not logged in, browser problem (not a test or app bug)
  StepError  -> SCRIPT_ERROR: the test could not perform a step (element missing/hidden/disabled)
  AppDefect  -> APP_DEFECT  : the app did not behave as its own code says it should
"""
import re
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import expect

from . import config
from .inventory import resolve


class EnvError(Exception):
    pass


class StepError(Exception):
    pass


class AppDefect(AssertionError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


class Steps:
    def __init__(self, page, elements: dict, validators: dict):
        self.page = page
        self.elements = elements
        self.validators = validators
        self.log = []          # [{"n", "action", "status", "detail"}]
        self.dialogs = []      # texts of alert()/confirm() boxes the app showed
        self.server_errors = []
        page.set_default_timeout(config.ACTION_TIMEOUT)
        page.set_default_navigation_timeout(config.NAVIGATION_TIMEOUT)
        page.on("dialog", self._on_dialog)
        page.on("response", self._on_response)

    # ------------------------------------------------------------------ internals
    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        try:
            dialog.accept()
        except Exception:
            pass

    def _on_response(self, response):
        try:
            if response.request.resource_type == "document" and response.status >= 500:
                self.server_errors.append(f"HTTP {response.status} from {response.url}")
        except Exception:
            pass

    def _label(self, key):
        el = self.elements.get(key, {})
        return f"'{el.get('label') or key}' ({key})"

    def _loc(self, key):
        if key not in self.elements:
            raise StepError(f"Element '{key}' is not in the page inventory - regenerate the tests.")
        return resolve(self.page, self.elements[key]["selector"]).first

    def _run(self, action: str, fn):
        step = {"n": len(self.log) + 1, "action": action, "status": "running", "detail": ""}
        self.log.append(step)
        try:
            result = fn()
            step["status"] = "passed"
            return result
        except (EnvError, StepError, AppDefect) as e:
            step["status"], step["detail"] = "failed", str(e)
            raise
        except PlaywrightTimeout as e:
            step["status"] = "failed"
            msg = f"Step {step['n']} '{action}': element did not become visible/enabled within {config.ACTION_TIMEOUT // 1000}s."
            step["detail"] = msg
            raise StepError(msg) from e
        except PlaywrightError as e:
            step["status"] = "failed"
            text = str(e).splitlines()[0]
            if "net::ERR_" in text or "Target page, context or browser has been closed" in text:
                step["detail"] = text
                raise EnvError(f"Step {step['n']} '{action}': {text}") from e
            step["detail"] = text
            raise StepError(f"Step {step['n']} '{action}': {text}") from e

    def _settle(self):
        """Wait for an ASP.NET postback (full page or UpdatePanel) to finish."""
        self.page.wait_for_timeout(300)
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=config.NAVIGATION_TIMEOUT)
            self.page.wait_for_function(
                "() => !(window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager"
                " && Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack())",
                timeout=config.NAVIGATION_TIMEOUT,
            )
        except Exception:
            pass

    def _check_logged_in(self):
        loc = self.page.locator(config.LOGIN_USER_SELECTOR)
        if loc.count() and loc.first.is_visible():
            raise EnvError("Redirected to the login page - session expired or login failed.")

    # ------------------------------------------------------------------ actions
    def goto(self, url):
        def do():
            resp = self.page.goto(url, wait_until="domcontentloaded")
            self._check_logged_in()
            if resp and resp.status >= 500 or "Server Error in" in self.page.inner_text("body"):
                raise AppDefect(f"Opening {url} shows a server error (HTTP {resp.status if resp else '?'}).")
            if resp and resp.status == 404:
                raise EnvError(f"{url} returned 404 - page not found.")
        self._run(f"open {url}", do)

    def fill(self, key, value):
        self._run(f"fill {self._label(key)} with {value!r}", lambda: self._loc(key).fill(value))

    def clear(self, key):
        self._run(f"clear {self._label(key)}", lambda: self._loc(key).fill(""))

    def select(self, key, option):
        def do():
            loc = self._loc(key)
            try:
                loc.select_option(label=option, timeout=3000)
            except Exception:
                loc.select_option(value=option)
            self._settle()  # dropdowns with AutoPostBack reload the page
        self._run(f"select {option!r} in {self._label(key)}", do)

    def check(self, key):
        self._run(f"tick {self._label(key)}", lambda: (self._loc(key).check(), self._settle()))

    def uncheck(self, key):
        self._run(f"untick {self._label(key)}", lambda: (self._loc(key).uncheck(), self._settle()))

    def click(self, key):
        self._run(f"click {self._label(key)}", lambda: (self._loc(key).click(), self._settle()))

    def press(self, key, keyname):
        self._run(f"press {keyname} in {self._label(key)}", lambda: (self._loc(key).press(keyname), self._settle()))

    def upload(self, key, filename="qa_upload_sample.txt", content="sample upload from QA agent"):
        def do():
            path = config.REPORT_DIR / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self._loc(key).set_input_files(str(path))
        self._run(f"upload {filename} to {self._label(key)}", do)

    def wait(self, ms):
        self._run(f"wait {ms} ms", lambda: self.page.wait_for_timeout(int(ms)))

    # ------------------------------------------------------------------ assertions
    def _validator_state(self, vkey):
        v = self.validators[vkey]
        sel = f'[id="{v["client_id"]}"]' if v.get("client_id") else f'[id$="_{vkey}"], [id="{vkey}"]'
        return self.page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return {exists: false};
                const s = getComputedStyle(el);
                const shown = s.visibility !== 'hidden' && s.display !== 'none' && (el.innerText || '').trim() !== '';
                const flag = el.isvalid;
                return {exists: true, invalid: flag === false || String(flag).toLowerCase() === 'false', shown};
            }""",
            sel,
        )

    def expect_validator_fires(self, vkey):
        v = self.validators[vkey]

        def do():
            deadline = time.time() + config.ASSERT_TIMEOUT / 1000
            state = {}
            while time.time() < deadline:
                state = self._validator_state(vkey)
                if state.get("invalid") or state.get("shown"):
                    return
                self.page.wait_for_timeout(200)
            where = "" if state.get("exists") else " (validator element not found on the page)"
            raise AppDefect(f"Expected validation message '{v['message']}' ({vkey}) to appear, but it did not{where}.")
        self._run(f"expect validation '{v['message'] or vkey}'", do)

    def expect_validator_passes(self, vkey):
        v = self.validators[vkey]

        def do():
            self.page.wait_for_timeout(500)
            state = self._validator_state(vkey)
            if state.get("invalid") or state.get("shown"):
                raise AppDefect(f"Validation message '{v['message']}' ({vkey}) appeared, but the input should be valid.")
        self._run(f"expect NO validation '{v['message'] or vkey}'", do)

    def expect_message(self, text):
        def do():
            want = _norm(text)
            deadline = time.time() + config.ASSERT_TIMEOUT / 1000
            while time.time() < deadline:
                if want in _norm(self.page.inner_text("body")) or any(want in _norm(d) for d in self.dialogs):
                    return
                self.page.wait_for_timeout(250)
            seen = f" Pop-ups seen: {self.dialogs}." if self.dialogs else ""
            raise AppDefect(f"Expected the message '{text}' but it never appeared.{seen}")
        self._run(f"expect message {text!r}", do)

    def _expect(self, desc, key, fn, failure):
        def do():
            try:
                fn(expect(self._loc(key)))
            except AssertionError as e:
                raise AppDefect(f"{failure} ({self._label(key)}).") from e
        self._run(desc, do)

    def expect_visible(self, key):
        self._expect(f"expect {self._label(key)} visible", key, lambda e: e.to_be_visible(timeout=config.ASSERT_TIMEOUT), "Expected element to be visible but it is not")

    def expect_hidden(self, key):
        self._expect(f"expect {self._label(key)} hidden", key, lambda e: e.to_be_hidden(timeout=config.ASSERT_TIMEOUT), "Expected element to be hidden but it is visible")

    def expect_enabled(self, key):
        self._expect(f"expect {self._label(key)} enabled", key, lambda e: e.to_be_enabled(timeout=config.ASSERT_TIMEOUT), "Expected element to be enabled but it is disabled")

    def expect_disabled(self, key):
        self._expect(f"expect {self._label(key)} disabled", key, lambda e: e.to_be_disabled(timeout=config.ASSERT_TIMEOUT), "Expected element to be disabled but it is enabled")

    def expect_value(self, key, value):
        def do():
            loc = self._loc(key)
            deadline = time.time() + config.ASSERT_TIMEOUT / 1000
            actual = None
            while time.time() < deadline:
                # dropdowns: accept the selected option's label or its value
                actual = loc.evaluate("el => el.tagName === 'SELECT' ? [el.value, (el.selectedOptions[0] || {}).text || ''] : [el.value]")
                if any(re.sub(r'\s+', ' ', a).strip() == value.strip() for a in actual):
                    return
                self.page.wait_for_timeout(200)
            raise AppDefect(f"Expected {self._label(key)} to be {value!r} but it is {actual[-1] if actual else '?'!r}.")
        self._run(f"expect {self._label(key)} = {value!r}", do)

    def expect_checked(self, key, checked=True):
        self._expect(f"expect {self._label(key)} {'ticked' if checked else 'unticked'}", key,
                     lambda e: e.to_be_checked(checked=checked, timeout=config.ASSERT_TIMEOUT),
                     f"Expected element to be {'ticked' if checked else 'unticked'}")

    def expect_url_contains(self, text):
        def do():
            if text.lower() not in self.page.url.lower():
                raise AppDefect(f"Expected URL to contain '{text}', but it is {self.page.url}.")
        self._run(f"expect URL contains {text!r}", do)

    def expect_no_server_error(self):
        def do():
            body = self.page.inner_text("body")
            if self.server_errors or "Server Error in" in body or "Runtime Error" in body:
                detail = self.server_errors[-1] if self.server_errors else body[:200].replace("\n", " ")
                raise AppDefect(f"The application raised a server error: {detail}")
        self._run("expect no server error", do)
