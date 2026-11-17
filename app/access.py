"""Which apps a person can actually use, for the landing page.

An administrator sees every app. Anyone else sees a private app (no accounts of its own) never, and an app with
accounts (lists, trips) only if THAT app says they have one. The dashboard asks the app itself, on this machine,
by calling its own /api/me with the person's own session cookie, so each app's database stays the single source
of truth: no second copy of who has access, no extra endpoint and no extra secret. 200 means yes. A 403 means the
app has no account for them (never had one, or it was removed): that app is shown as one they can ASK for. Anything
else (not signed in, a redirect, an error, a timeout) is "can't tell": the app is simply left off, so a problem
never shows an app the person cannot use nor offers a request for one they may already have.

The session cookie is sent to the fixed localhost addresses in config.APP_API_BASE and nowhere else, and
redirects are never followed, so it cannot be bounced elsewhere."""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple

import requests
from fastapi import Request
from shared_python import auth

from . import config

log = logging.getLogger(__name__)


def _account_state(app: dict, cookie_name: str, cookie_value: str) -> str:
    """"yes" (the app says they have an account), "no" (the app says they do not: a 403) or "unknown" (anything else: no
    answer, a redirect, an error). Only a plain "no" may be offered a way to ask for access; "unknown" shows nothing."""
    base = config.APP_API_BASE.get(app["key"])
    if not base:
        return "unknown"
    headers = {"Cookie": f"{cookie_name}={cookie_value}"} if cookie_name and cookie_value else {}
    try:
        resp = requests.get(f"{base}/api/me", headers=headers, timeout=config.APP_CHECK_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        log.warning("Could not ask %s whether someone has an account: %s", app["key"], type(exc).__name__)
        return "unknown"
    return "yes" if resp.status_code == 200 else "no" if resp.status_code == 403 else "unknown"


def account_states(request: Request, email: str) -> dict:
    """For each app with accounts: "yes", "no" or "unknown" (see _account_state). Administrators have all of them."""
    members = [a for a in config.APPS if a["access"] == "member"]
    if email in config.ADMIN_EMAILS:
        return {a["key"]: "yes" for a in members}
    cookie_name = auth.oidc_cookie_name() or ""
    cookie_value = request.cookies.get(cookie_name, "") if cookie_name else ""
    if not members:
        return {}
    with ThreadPoolExecutor(max_workers=len(members)) as pool:
        answers = list(pool.map(lambda a: _account_state(a, cookie_name, cookie_value), members))
    return {a["key"]: answer for a, answer in zip(members, answers)}


def apps_and_locked(request: Request, email: str) -> Tuple[List[dict], List[dict]]:
    """The apps to show `email` (each with its address), and the apps they could ask for: those that told us "no"."""
    if email in config.ADMIN_EMAILS:
        return [dict(a, url=config.app_url(a)) for a in config.APPS], []
    states = account_states(request, email)
    shown = [dict(a, url=config.app_url(a)) for a in config.APPS if states.get(a["key"]) == "yes"]
    locked = [dict(a) for a in config.APPS if states.get(a["key"]) == "no"]
    return shown, locked


def apps_for(request: Request, email: str) -> List[dict]:
    """The apps to show `email`, in the order of config.APPS, each with its address."""
    return apps_and_locked(request, email)[0]
