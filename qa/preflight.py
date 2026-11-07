"""Step 1: make sure the app is reachable and we can log in, then save the logged-in session.

Every generated test re-uses the saved session (.auth/state.json), so tests never log in
themselves and a login problem is reported once, clearly, instead of failing every test.
"""
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

from . import config


class PreflightError(Exception):
    pass


def check_server(url: str = None, timeout: int = 10):
    url = url or config.BASE_URL
    try:
        urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.HTTPError as e:
        # The server answered (even 401/403/500 means it is up)
        if e.code >= 500:
            raise PreflightError(
                f"The app at {url} is running but returns HTTP {e.code} on its home page. "
                "Fix the server error first."
            )
    except Exception as e:
        raise PreflightError(
            f"Cannot reach the app at {url} ({e.__class__.__name__}: {e}).\n"
            "  -> Start the Medcross application (Visual Studio / IIS Express) and open "
            f"{url} in your browser to confirm it loads, then run the agent again."
        )


def login_and_save_state(headless: bool = True):
    if not config.USERNAME or not config.PASSWORD:
        raise PreflightError(
            "APP_USERNAME / APP_PASSWORD are not set.\n"
            "  -> Add these two lines to your .env file:\n"
            "       APP_USERNAME=your-login\n"
            "       APP_PASSWORD=your-password"
        )

    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(config.ACTION_TIMEOUT)
        page.goto(config.LOGIN_URL, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT)

        user = page.locator(config.LOGIN_USER_SELECTOR)
        if user.count() == 0 or not user.first.is_visible():
            # Already logged in (or the login page changed). Accept only if we can see we are not on a login form.
            if page.locator("input[type=password]").count() > 0:
                raise PreflightError(
                    "Found a password field but not the configured username field "
                    f"({config.LOGIN_USER_SELECTOR}). Update LOGIN_USER_SELECTOR in .env."
                )
        else:
            user.first.fill(config.USERNAME)
            page.locator(config.LOGIN_PASS_SELECTOR).first.fill(config.PASSWORD)
            login_url = page.url
            page.locator(config.LOGIN_BUTTON_SELECTOR).first.click()
            try:
                # Logged in when the username box is gone (or we navigated away)
                page.wait_for_function(
                    """([sel, startUrl]) => {
                        const el = document.querySelector(sel);
                        const hidden = !el || el.offsetParent === null;
                        return hidden || location.href !== startUrl;
                    }""",
                    arg=[config.LOGIN_USER_SELECTOR, login_url],
                    timeout=45000,
                )
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                # The wait above can time out even on a successful login if the app is just slow
                # to redirect - before declaring failure, check the page as it stands right now:
                # if the login form is actually gone, the login worked, just later than we waited.
                still_on_login = page.locator(config.LOGIN_USER_SELECTOR).count() > 0 and \
                    page.locator(config.LOGIN_USER_SELECTOR).first.is_visible()
                if not still_on_login:
                    pass  # fall through - login succeeded, just slowly
                else:
                    text = page.inner_text("body")[:400].replace("\n", " ")
                    browser.close()
                    raise PreflightError(
                        "Login did not succeed - still on the login page after 45s.\n"
                        f"  Page says: {text}\n"
                        "  -> Check APP_USERNAME / APP_PASSWORD in .env."
                    )
            if page.locator(config.LOGIN_USER_SELECTOR).first.is_visible() if page.locator(config.LOGIN_USER_SELECTOR).count() else False:
                browser.close()
                raise PreflightError("Login form is still visible after logging in. Check the credentials in .env.")

        context.storage_state(path=str(config.STATE_FILE))
        browser.close()
    return config.STATE_FILE


def run_preflight(headless: bool = True):
    print(f"[1/6] Preflight: checking {config.BASE_URL} ...")
    check_server()
    print("      Server is up. Logging in ...")
    login_and_save_state(headless=headless)
    print(f"      Logged in. Session saved to {config.STATE_FILE.relative_to(config.ROOT)}")
