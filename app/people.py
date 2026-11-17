"""The central Users page: one place to let people into the apps, tick which ones, and give them a name.

Each app keeps its own list of who may use it (default-deny, checked by the app on every request), and that stays the
single source of truth. This module only drives those lists from one page: it reads each app's own administrator route
and, to tick or untick a box, calls the same add / remove routes the app's own Users page uses. All of it is done AS THE
SIGNED-IN ADMINISTRATOR, by passing their session cookie to the app's address on this machine, so every app still checks
for itself that the caller is an administrator; the dashboard holds no extra secret and no extra power. Redirects are never
followed, so the cookie cannot be bounced elsewhere.

First and last names live here, in a tiny SQLite file of their own (never needed to sign in)."""
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Dict, List, Optional, Tuple

import requests
from shared_python import notify

from . import config

log = logging.getLogger(__name__)

NAME_MAX = 60
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AppProblem(Exception):
    """An app could not do what was asked; the message says why, in words for the administrator."""


# --- names -----------------------------------------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.PEOPLE_DB, timeout=5)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS names (email TEXT PRIMARY KEY, first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '')"
    )
    return connection


def clean_name(value: Optional[str]) -> str:
    """A name as typed: control characters out, runs of spaces folded. Too long is an error, not a quiet cut."""
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", value or "")).strip()
    if len(text) > NAME_MAX:
        raise ValueError(f"A name can be at most {NAME_MAX} characters")
    return text


def get_names() -> Dict[str, Tuple[str, str]]:
    with closing(_connect()) as connection:
        return {email: (first, last) for email, first, last in connection.execute("SELECT email, first_name, last_name FROM names")}


def get_name(email: str) -> Tuple[str, str]:
    return get_names().get(email.strip().lower(), ("", ""))


def set_name(email: str, first: Optional[str], last: Optional[str]) -> Tuple[str, str]:
    """Save someone's name (both empty clears it). Returns what was stored."""
    email = email.strip().lower()
    first, last = clean_name(first), clean_name(last)
    with closing(_connect()) as connection, connection:
        if first or last:
            connection.execute(
                "INSERT INTO names (email, first_name, last_name) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET first_name = excluded.first_name, last_name = excluded.last_name",
                (email, first, last),
            )
        else:
            connection.execute("DELETE FROM names WHERE email = ?", (email,))
    return first, last


# --- driving the apps --------------------------------------------------------------------------------------------------


def member_apps() -> List[dict]:
    return [a for a in config.APPS if a["access"] == "member" and a["key"] in config.APP_USERS]


def _call(app: dict, method: str, suffix: str = "", cookie: Optional[Tuple[str, str]] = None, **kwargs) -> requests.Response:
    base = config.APP_API_BASE[app["key"]]
    headers = {"Cookie": f"{cookie[0]}={cookie[1]}"} if cookie and cookie[0] and cookie[1] else {}
    try:
        resp = requests.request(
            method, f"{base}{config.APP_USERS[app['key']]['route']}{suffix}", headers=headers,
            timeout=config.APP_CALL_TIMEOUT, allow_redirects=False, **kwargs,
        )
    except requests.RequestException as exc:
        log.warning("Could not reach %s: %s", app["key"], type(exc).__name__)
        raise AppProblem(f"{app['name']} didn't answer") from exc
    if resp.status_code in (301, 302, 303, 307, 308, 401):
        raise AppProblem(f"{app['name']} didn't accept your sign-in")
    if resp.status_code == 403:
        raise AppProblem(f"{app['name']} says you aren't an administrator there")
    return resp


def _detail(app: dict, resp: requests.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    return f"{app['name']}: {detail}" if isinstance(detail, str) else f"{app['name']} refused ({resp.status_code})"


def list_in_app(app: dict, cookie) -> List[dict]:
    resp = _call(app, "GET", cookie=cookie)
    if resp.status_code != 200:
        raise AppProblem(_detail(app, resp))
    return [u for u in resp.json() if isinstance(u, dict) and u.get("email")]


def overview(cookie) -> dict:
    """Everyone with an account in any app, and for each app whether they may use it: "active", "removed", "none", or
    "admin" (an administrator, whose access can't be changed from here). An app that did not answer is listed under
    `problems` and its column is "unknown" for everyone, never guessed."""
    apps = member_apps()

    def ask(app):
        try:
            return app["key"], list_in_app(app, cookie), None
        except AppProblem as exc:
            return app["key"], None, str(exc)

    with ThreadPoolExecutor(max_workers=max(len(apps), 1)) as pool:
        answers = list(pool.map(ask, apps))
    problems = {key: why for key, _users, why in answers if why}
    state: Dict[str, Dict[str, str]] = {}
    for key, users, why in answers:
        for user in users or []:
            email = user["email"].strip().lower()
            if email.endswith(".invalid"):
                continue
            state.setdefault(email, {})[key] = "admin" if user.get("is_admin") else ("active" if user.get("status") == "active" else "removed")
    names = get_names()
    people = []
    for email in sorted(state, key=lambda e: (names.get(e, ("", ""))[1].lower() or e, e)):
        first, last = names.get(email, ("", ""))
        access = {a["key"]: ("unknown" if a["key"] in problems else state[email].get(a["key"], "none")) for a in apps}
        people.append({"email": email, "first_name": first, "last_name": last, "access": access})
    return {"apps": [{"key": a["key"], "name": a["name"]} for a in apps], "people": people, "problems": problems}


def grant(app: dict, email: str, cookie) -> str:
    """Let `email` use the app (a removed person is restored). Returns "added" or "already"; raises AppProblem.
    Asks first: some apps answer "fine" when the person already had access, and they must not be told again."""
    current = next((u for u in list_in_app(app, cookie) if u["email"].strip().lower() == email), None)
    if current is not None and current.get("status") == "active":
        return "already"
    resp = _call(app, "POST", cookie=cookie, params={"notify": "false"}, json={"email": email})
    if resp.status_code == 409 and "already" in _detail(app, resp):
        return "already"
    if resp.status_code != 200:
        raise AppProblem(_detail(app, resp))
    return "added"


def revoke(app: dict, email: str, cookie) -> str:
    """Take away `email`'s access (their data stays; ticking the box again restores it). Returns "removed" or "none"."""
    target = next((u for u in list_in_app(app, cookie) if u["email"].strip().lower() == email), None)
    if target is None:
        return "none"
    if target.get("is_admin"):
        raise AppProblem(f"{app['name']}: an administrator's access can't be changed here")
    if config.APP_USERS[app["key"]]["revoke"] == "action":
        resp = _call(app, "POST", f"/{target['id']}/revoke", cookie=cookie)
    else:
        resp = _call(app, "PATCH", f"/{target['id']}", cookie=cookie, json={"status": "removed"})
    if resp.status_code != 200:
        raise AppProblem(_detail(app, resp))
    return "removed"


def tell_them(email: str, admin_email: str, app_names: List[str]) -> None:
    """One email for everything they were just let into (instead of one per app). Best effort: never raises."""
    if not app_names:
        return
    which = ", ".join(app_names[:-1]) + (" and " if len(app_names) > 1 else "") + app_names[-1]
    lines = [
        f"{admin_email} gave you access to {which}.",
        "",
        f"Open it here: {config.DASHBOARD_URL}",
        "",
        f"Sign in with this email address (it's the one you were let in as): {email}",
    ]
    try:
        notify.send_email(email, f"{admin_email} gave you access to {which}", "\n".join(lines))
    except Exception as exc:  # noqa: BLE001 - never fail the change because the email did
        log.warning("Telling the new person by email failed: %s", notify.describe_failure(exc))
