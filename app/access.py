"""Which apps a person can actually use, for the landing page.

An administrator sees every app. Anyone else sees a private app (no accounts of its own) never, and an app with
accounts (lists, trips) only if THAT app says they have one. The dashboard asks the app itself, on this machine,
by calling its own /api/me with the person's own session cookie, so each app's database stays the single source
of truth: no second copy of who has access, no extra endpoint and no extra secret. 200 means yes. A 403 means the
app has no account for them (never had one, or it was removed): that app is shown as one they can ASK for. Anything
else is "can't tell": a 401 or a redirect (the app does not recognise their sign-in) leaves the app off; a real problem (an error
answer, a timeout, no answer) shows the app in red with a way to reach the administrators, so a hiccup (an app restarting during a
deploy) is never mistaken for "you don't have access", and an app they may already have is never offered as a request.

The session cookie is sent to the fixed localhost addresses in config.APP_API_BASE and nowhere else, and
redirects are never followed, so it cannot be bounced elsewhere."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple
from urllib.parse import quote

import requests
from fastapi import Request
from shared_python import auth, probe

from . import config

log = logging.getLogger(__name__)


def _account_state(app: dict, cookie_name: str, cookie_value: str) -> str:
    """What the app says about this person's account:
      "yes"     - 200: they have one;
      "no"      - 403: it has none for them (never had one, or it was removed), so they may ASK for it;
      "unknown" - 401 or a redirect: the app does not recognise their sign-in (nothing to show, nothing to ask for);
      "error"   - anything else (no answer, a timeout, a 5xx, a missing route): the app is having a problem, and we cannot
                  say whether they have access. The page shows it as a problem rather than guessing or quietly leaving it off."""
    base = config.APP_API_BASE.get(app["key"])
    if not base:
        return "error"
    # The probe header keeps this check from counting as the person having been in that app (shared_python.probe).
    headers = {probe.PROBE_HEADER: "1"}
    if cookie_name and cookie_value:
        headers["Cookie"] = f"{cookie_name}={cookie_value}"
    try:
        resp = requests.get(f"{base}/api/me", headers=headers, timeout=config.APP_CHECK_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        log.warning("Could not ask %s whether someone has an account: %s", app["key"], type(exc).__name__)
        return "error"
    if resp.status_code == 200:
        return "yes"
    if resp.status_code == 403:
        return "no"
    if resp.status_code == 401 or 300 <= resp.status_code < 400:
        return "unknown"
    log.warning("%s answered %s when asked whether someone has an account", app["key"], resp.status_code)
    return "error"


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


def apps_and_locked(request: Request, email: str) -> Tuple[List[dict], List[dict], List[dict]]:
    """For `email`: the apps to show (each with its address), the apps they could ask for (those that said "no"), and the
    apps that could not be checked because of a problem (shown in red, with a way to reach the administrators)."""
    if email in config.ADMIN_EMAILS:
        return [dict(a, url=config.app_url(a)) for a in config.APPS], [], []
    states = account_states(request, email)
    shown = [dict(a, url=config.app_url(a)) for a in config.APPS if states.get(a["key"]) == "yes"]
    locked = [dict(a) for a in config.APPS if states.get(a["key"]) == "no"]
    broken = [dict(a) for a in config.APPS if states.get(a["key"]) == "error"]
    return shown, locked, broken


def customer_service_link(email: str, broken: List[dict]) -> str:
    """A mailto: to every administrator (config.ADMIN_EMAILS), with what went wrong filled in; "" when there are none."""
    to = ",".join(quote(a, safe="@") for a in sorted(config.ADMIN_EMAILS))
    if not to:
        return ""
    apps = ", ".join(a["name"] for a in broken)
    subject = "Brassworks: I can't see my apps"
    body = f"Hi,\n\nThe dashboard couldn't check my access to {apps}, and reloading didn't help.\n\nI'm signed in as {email}.\n"
    return f"mailto:{to}?subject={quote(subject)}&body={quote(body)}"


def apps_for(request: Request, email: str) -> List[dict]:
    """The apps to show `email`, in the order of config.APPS, each with its address."""
    return apps_and_locked(request, email)[0]


# --- the apps list for every app's navigation ---------------------------------------------------------------------------------------
# Every app's sidebar ends with the apps this person may open (shared-assets theme.js asks GET /api/apps on the dashboard). That would put
# the check above on every page anyone opens, so the answer is remembered per person: for a day at most, and dropped the moment the
# dashboard itself changes that person's access (forget_apps). A change made somewhere the dashboard does not see (an app's own Users page,
# an in-app invite) shows when the day is up. An administrator's answer is every app, which costs nothing, so it is not remembered.
APPS_CACHE_SECONDS = 24 * 60 * 60  # only a safety net: the answer is dropped whenever access changes (see above)
_apps_cache: dict = {}  # email -> (when, [{"key", "name", "icon", "url"}])


def remembered_count() -> int:
    return len(_apps_cache)


def forget_apps(email: str = None) -> None:
    """Drop what is remembered about one person (or everyone, with no address). Called whenever the dashboard changes someone's access
    (adding them, ticking or unticking an app, approving a request), so the change shows at once instead of when the time is up."""
    if email is None:
        _apps_cache.clear()
    else:
        _apps_cache.pop(email.strip().lower(), None)


def nav_apps(request: Request, email: str, now: float = None) -> List[dict]:
    """The apps `email` may open, for the navigation: just what a link needs, in the order of config.APPS."""
    now = time.time() if now is None else now
    if email not in config.ADMIN_EMAILS:
        found = _apps_cache.get(email)
        if found and now - found[0] < APPS_CACHE_SECONDS:
            return found[1]
    shown, _locked, broken = apps_and_locked(request, email)
    apps = [{"key": a["key"], "name": a["name"], "icon": a["icon"], "url": a["url"]} for a in shown]
    if email not in config.ADMIN_EMAILS and not broken:  # an app that did not answer is not remembered as "no access": ask again next time
        _apps_cache[email] = (now, apps)
    return apps
