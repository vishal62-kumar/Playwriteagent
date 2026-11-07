"""Central configuration. Everything comes from .env (or real environment variables)."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name, default=None):
    value = os.getenv(name)
    return value if value not in (None, "") else default


BASE_URL = _get("APP_BASE_URL", "http://localhost:2786").rstrip("/")
LOGIN_URL = _get("APP_LOGIN_URL", BASE_URL + "/")
USERNAME = _get("APP_USERNAME")
PASSWORD = _get("APP_PASSWORD")

# Login form selectors (defaults match the Medcross login page)
LOGIN_USER_SELECTOR = _get("LOGIN_USER_SELECTOR", "#ctl00_ContentOverUpdatePanel_txtDoctorUserID")
LOGIN_PASS_SELECTOR = _get("LOGIN_PASS_SELECTOR", "#ctl00_ContentOverUpdatePanel_txtDoctorPassword")
LOGIN_BUTTON_SELECTOR = _get("LOGIN_BUTTON_SELECTOR", "#ctl00_ContentOverUpdatePanel_Buttondoctorlogin")

# Where the application source lives (for reading .aspx / .aspx.cs)
SOURCE_DIR = _get("APP_SOURCE_DIR", r"C:\medcrossproject")

OPENAI_API_KEY = _get("OPENAI_API_KEY")
OPENAI_MODEL = _get("OPENAI_MODEL", "gpt-4o")

# Timeouts (milliseconds)
ACTION_TIMEOUT = int(_get("ACTION_TIMEOUT_MS", "10000"))
NAVIGATION_TIMEOUT = int(_get("NAVIGATION_TIMEOUT_MS", "30000"))
ASSERT_TIMEOUT = int(_get("ASSERT_TIMEOUT_MS", "5000"))

HEADLESS = _get("HEADLESS", "true").lower() != "false"

# Output folders
AUTH_DIR = ROOT / ".auth"
STATE_FILE = AUTH_DIR / "state.json"
INVENTORY_DIR = ROOT / "inventory"
SCENARIO_DIR = ROOT / "scenarios"
TESTS_DIR = ROOT / "generated_tests"
REPORT_DIR = ROOT / "reports"
RESULTS_FILE = REPORT_DIR / "results.json"
SCREENSHOT_DIR = REPORT_DIR / "screenshots"
LIBRARY_DIR = ROOT / "test_library"


def page_url(page_name: str) -> str:
    """'Contacts/PracticeReferralDoctor' -> 'http://localhost:2786/Contacts/PracticeReferralDoctor.aspx'"""
    if page_name.startswith("http"):
        return page_name
    path = page_name.strip().lstrip("/")
    if not path.lower().endswith(".aspx") and "?" not in path:
        path += ".aspx"
    return f"{BASE_URL}/{path}"


def page_slug(page_name: str) -> str:
    name = re.sub(r"\.aspx.*$", "", page_name, flags=re.I)
    name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return name.lower() or "page"
