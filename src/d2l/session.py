"""Browser session for Brightspace: you log in once by hand, the profile keeps you logged in.

Brightspace sits behind the university's single sign-on, so there is no password to automate here — and no
reason to. A persistent browser profile holds the SSO cookies exactly like your normal browser does; the
scripts reuse that profile and never see your credentials.
"""
import os
import re
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "user-data-dir"          # gitignored: holds the logged-in browser profile
STATE = ROOT / "session" / "state.json"   # gitignored: saved cookies, restored into each run


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


def _renew(page) -> bool:
    """Get a fresh Brightspace session through UDST's Microsoft SSO, the way the login page's SSO button does.

    Brightspace's own session times out after a few idle hours, but the Microsoft sign-in cookie
    (ESTSAUTHPERSISTENT, ~90 days, extended each time it's used) signs straight back in without a password or MFA.
    Returns False when Microsoft wants a human again (password, MFA, "pick an account").
    """
    # Press the login page's SSO button once its script is ready; going to its initiate-login URL directly 404s.
    page.goto(f"{base_url()}/d2l/login?target=%2fd2l%2fhome", wait_until="networkidle")
    try:
        page.get_by_role("button", name=re.compile("single sign on", re.I)).first.click()
        page.wait_for_url("**/d2l/home**", timeout=45_000)
        return True
    except Exception:
        return False


def _save(ctx) -> None:
    """Write the refreshed cookies atomically, so a crash mid-write can't lose the login."""
    tmp = STATE.with_suffix(".tmp")
    ctx.storage_state(path=str(tmp))
    tmp.replace(STATE)


@contextmanager
def signed_in_page(headless: bool = True):
    """A page on the Brightspace home page, restored from the saved session. Use inside `with`.

    The SSO cookies Brightspace sets are *session* cookies, so the persistent profile alone is not enough;
    `storage_state` keeps them. When Brightspace's session has timed out, it is renewed silently through Microsoft
    SSO, and the refreshed cookies are saved after every run — you only log in by hand when Microsoft itself asks.
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
            if not _renew(page):
                browser.close()
                raise SystemExit("Session expired and Microsoft wants you to sign in again (password/MFA) — "
                                 "run `d2l login` or use “Log in again” in the app.")
            print("Brightspace session had timed out; renewed it through Microsoft sign-in")
            _save(ctx)
        try:
            yield page
            _save(ctx)
        finally:
            browser.close()

