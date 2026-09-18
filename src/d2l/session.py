"""Browser session for Brightspace: you log in once by hand, the profile keeps you logged in.

Brightspace usually sits behind the university's single sign-on, so there is no password to automate here — and
no reason to. The saved session holds the SSO cookies exactly like your normal browser does; the scripts reuse it
and never see your credentials.
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


# Labels SSO buttons on Brightspace login pages commonly carry; D2L_SSO_BUTTON (a regex) overrides for your school
SSO_LABELS = r"single sign[- ]?on|\bsso\b|sign in with|log ?in with|microsoft|office ?365|google|okta|shibboleth|saml|institution"


def _renew(page) -> bool:
    """Get a fresh Brightspace session through the school's SSO without a password.

    Brightspace's own session times out after a few idle hours, but the identity provider's sign-in cookie (e.g.
    Microsoft's ESTSAUTHPERSISTENT, ~90 days, extended on use) signs straight back in. Some login pages redirect to
    the provider by themselves; most show an SSO button, which is pressed once the page's script is ready (UDST:
    "UDST - Single Sign On Login" — opening its initiate-login URL bare 404s). Returns False when a human is needed
    (password, MFA, account picker) or the page has no SSO button, e.g. a plain username/password form.
    """
    page.goto(f"{base_url()}/d2l/login?target=%2fd2l%2fhome", wait_until="networkidle")
    if "/d2l/home" in page.url:                       # redirected by itself
        return True
    label = re.compile(os.environ.get("D2L_SSO_BUTTON") or SSO_LABELS, re.I)
    for finder in (lambda: page.get_by_role("button", name=label), lambda: page.get_by_role("link", name=label)):
        target = finder().first
        try:
            if target.count() and target.is_visible():
                target.click()
                page.wait_for_url("**/d2l/home**", timeout=45_000)
                return True
        except Exception:
            return False
    try:                                              # no button: maybe the page is still heading to the provider
        page.wait_for_url("**/d2l/home**", timeout=15_000)
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
    `storage_state` keeps them. When Brightspace's session has timed out, it is renewed silently through the
    school's SSO, and the refreshed cookies are saved after every run — you only log in by hand when the identity
    provider itself asks again.
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
                raise SystemExit("Session expired and your university's sign-in wants you again (password/MFA) — "
                                 "run `d2l login` or use “Log in again” in the app.")
            print("Brightspace session had timed out; renewed it through the university sign-in")
            _save(ctx)
        try:
            yield page
            _save(ctx)
        finally:
            browser.close()

