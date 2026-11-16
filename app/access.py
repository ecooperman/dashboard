"""Which apps a person can actually use, for the landing page.

An administrator sees every app. Anyone else sees a private app (no accounts of its own) never, and an app with
accounts (lists, trips) only if THAT app says they have one. The dashboard asks the app itself, on this machine,
by calling its own /api/me with the person's own session cookie, so each app's database stays the single source
of truth: no second copy of who has access, no extra endpoint and no extra secret. 200 means yes; anything
else (no account, removed, not signed in, a redirect, an error, a timeout) means no, so a problem hides an app
rather than showing one the person cannot use.

The session cookie is sent to the fixed localhost addresses in config.APP_API_BASE and nowhere else, and
redirects are never followed, so it cannot be bounced elsewhere."""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List

import requests
from fastapi import Request
from shared_python import auth

from . import config

log = logging.getLogger(__name__)


def _has_account(app: dict, cookie_name: str, cookie_value: str) -> bool:
    base = config.APP_API_BASE.get(app["key"])
    if not base:
        return False
    headers = {"Cookie": f"{cookie_name}={cookie_value}"} if cookie_name and cookie_value else {}
    try:
        resp = requests.get(f"{base}/api/me", headers=headers, timeout=config.APP_CHECK_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        log.warning("Could not ask %s whether someone has an account: %s", app["key"], type(exc).__name__)
        return False
    return resp.status_code == 200


def apps_for(request: Request, email: str) -> List[dict]:
    """The apps to show `email`, in the order of config.APPS, each with its address."""
    if email in config.ADMIN_EMAILS:
        shown = list(config.APPS)
    else:
        members = [a for a in config.APPS if a["access"] == "member"]
        cookie_name = auth.oidc_cookie_name() or ""
        cookie_value = request.cookies.get(cookie_name, "") if cookie_name else ""
        if members:
            with ThreadPoolExecutor(max_workers=len(members)) as pool:
                answers = list(pool.map(lambda a: _has_account(a, cookie_name, cookie_value), members))
        else:
            answers = []
        allowed = {a["key"] for a, ok in zip(members, answers) if ok}
        shown = [a for a in config.APPS if a["key"] in allowed]
    return [dict(a, url=config.app_url(a)) for a in shown]
