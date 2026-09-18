"""Browser session for Brightspace: you log in once by hand, the profile keeps you logged in.

Brightspace sits behind the university's single sign-on, so there is no password to automate here — and no
reason to. A persistent browser profile holds the SSO cookies exactly like your normal browser does; the
scripts reuse that profile and never see your credentials.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "user-data-dir"          # gitignored: holds the logged-in browser profile
STATE = ROOT / "session" / "state.json"   # gitignored: cookies for API-style requests
def base_url() -> str:
    """Read at call time, not import time — .env is loaded by the CLI after the modules import."""
    base = os.environ.get("D2L_BASE_URL", "").rstrip("/")
    if not base:
        raise SystemExit("Set D2L_BASE_URL first, e.g. in .env: D2L_BASE_URL=https://your-school.brightspace.com")
    return base


def login(timeout_min: int = 10) -> None:
    """Open a real browser window, wait until you are on the Brightspace home page, then save the session."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{base_url()}/d2l/home", wait_until="domcontentloaded")
        print("Sign in in the browser window (SSO + MFA as usual). Waiting for the Brightspace home page…")
        page.wait_for_url("**/d2l/home**", timeout=timeout_min * 60_000)
        page.wait_for_timeout(2000)
        ctx.storage_state(path=str(STATE))
        print(f"signed in; session saved to {STATE.relative_to(ROOT)}")
        ctx.close()


@contextmanager
def signed_in_page(headless: bool = True):
    """A page on the Brightspace home page, restored from the saved session. Use inside `with`.

    Note: the SSO cookies Brightspace sets are *session* cookies — they vanish when the login browser closes, so
    the persistent profile alone is not enough. `storage_state` keeps them, so that is what we restore here.
    """
    if not STATE.exists():
        raise SystemExit("No saved session — run `d2l login` first.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(storage_state=str(STATE))
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(f"{base_url()}/d2l/home", wait_until="domcontentloaded")
        if "/d2l/login" in page.url:
            browser.close()
            raise SystemExit("Session expired — run `d2l login` again.")
        try:
            yield page
        finally:
            browser.close()


def cookies() -> dict:
    """Cookies from the saved session, for plain HTTP requests."""
    if not STATE.exists():
        raise SystemExit("No saved session — run `d2l login` first.")
    state = json.loads(STATE.read_text())
    return {c["name"]: c["value"] for c in state["cookies"]}
