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
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Dict, List, Optional, Tuple

import requests
from shared_python import notify, probe

from . import config

log = logging.getLogger(__name__)

NAME_MAX = 60
NOTE_MAX = 300
DIGEST_PEOPLE_MAX = 20  # people listed in one digest; the rest are "and N more" (the Users page has them all)
MAX_WAITING = 100  # requests waiting at once: anyone can sign up, so the queue (and the administrator's page) is bounded
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AppProblem(Exception):
    """An app could not do what was asked; the message says why, in words for the administrator."""


# --- names -----------------------------------------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.PEOPLE_DB, timeout=5)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS names (email TEXT PRIMARY KEY, first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '')"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS access_requests (email TEXT NOT NULL, app TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL, announced_at INTEGER, PRIMARY KEY (email, app))"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if "announced_at" not in {row[1] for row in connection.execute("PRAGMA table_info(access_requests)")}:
        # A file made before the digest existed: its requests were all announced one by one, so none is announced again.
        connection.execute("ALTER TABLE access_requests ADD COLUMN announced_at INTEGER")
        connection.execute("UPDATE access_requests SET announced_at = created_at")
        connection.commit()
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


# --- requests for access ---------------------------------------------------------------------------------------------------
# A signed-in person (so a verified email, taken from their session and never from the request) can ask for an app they have no
# account in. The request is only a row here; nothing opens until an administrator approves it, and approving goes through the
# same grant() as ticking a box. A declined request stays on file, so a person cannot ask again and again.


def clean_note(value: Optional[str]) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", value or "")).strip()
    if len(text) > NOTE_MAX:
        raise ValueError(f"A note can be at most {NOTE_MAX} characters")
    return text


def request_states(email: str) -> Dict[str, str]:
    """app key -> "pending" or "declined", for this person's own requests."""
    with closing(_connect()) as connection:
        return dict(connection.execute("SELECT app, status FROM access_requests WHERE email = ?", (email.strip().lower(),)))


def file_requests(email: str, app_keys: List[str], note: Optional[str]) -> Tuple[List[str], List[str]]:
    """Ask for each app that has no request from this person yet. Returns (filed, already): `already` are the apps whose
    request is on file (waiting or declined), which are left exactly as they are. Raises ValueError for a bad note and
    OverflowError when too many requests are already waiting."""
    email = email.strip().lower()
    note = clean_note(note)
    filed, already = [], []
    with closing(_connect()) as connection, connection:
        have = {app for (app,) in connection.execute("SELECT app FROM access_requests WHERE email = ?", (email,))}
        waiting = connection.execute("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'").fetchone()[0]
        for key in dict.fromkeys(app_keys):
            if key in have:
                already.append(key)
                continue
            if waiting >= MAX_WAITING:
                raise OverflowError("Too many requests are waiting right now. Please try again later.")
            connection.execute(
                "INSERT INTO access_requests (email, app, note, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (email, key, note, int(time.time())),
            )
            waiting += 1
            filed.append(key)
    return filed, already


def waiting_count() -> int:
    with closing(_connect()) as connection:
        return connection.execute("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'").fetchone()[0]


def waiting_requests(overview_people: List[dict]) -> List[dict]:
    """The pending requests, one entry per person, oldest first: {email, first_name, last_name, note, apps: [{key, name,
    previously}]}. `previously` is "removed" when that app took their access away before (the administrator should know),
    else "none"; it comes from the administrator's own overview of every app, so nothing extra is asked of the apps."""
    names = get_names()
    known = {p["email"]: p["access"] for p in overview_people}
    app_names = {a["key"]: a["name"] for a in member_apps()}
    grouped: Dict[str, dict] = {}
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT email, app, note, created_at FROM access_requests WHERE status = 'pending' ORDER BY created_at, email, app"
        ).fetchall()
    for email, app, note, _created in rows:
        if app not in app_names:
            continue  # an app that no longer takes requests
        first, last = names.get(email, ("", ""))
        entry = grouped.setdefault(email, {"email": email, "first_name": first, "last_name": last, "note": note, "apps": []})
        entry["note"] = entry["note"] or note
        previously = "removed" if known.get(email, {}).get(app) == "removed" else "none"
        entry["apps"].append({"key": app, "name": app_names[app], "previously": previously})
    order = list(app_names)  # the apps in their usual order, not alphabetical
    for entry in grouped.values():
        entry["apps"].sort(key=lambda a: order.index(a["key"]))
    return list(grouped.values())


def settle_requests(email: str, app_keys: List[str], decline: bool = False) -> None:
    """A request is finished: the person was let in (the row goes) or turned down (the row stays, marked declined)."""
    email = email.strip().lower()
    with closing(_connect()) as connection, connection:
        for key in app_keys:
            if decline:
                connection.execute("UPDATE access_requests SET status = 'declined' WHERE email = ? AND app = ?", (email, key))
            else:
                connection.execute("DELETE FROM access_requests WHERE email = ? AND app = ?", (email, key))


def requests_for(email: str) -> List[str]:
    """The apps this person has a PENDING request for."""
    with closing(_connect()) as connection:
        return [app for (app,) in connection.execute("SELECT app FROM access_requests WHERE email = ? AND status = 'pending'", (email.strip().lower(),))]


def digest_message(rows: List[tuple], names: Dict[str, Tuple[str, str]], app_names: Dict[str, str]) -> Tuple[str, str]:
    """(title, body) for one digest. `rows` are (email, app, note) in the order they were filed; one line per person."""
    grouped: Dict[str, dict] = {}
    for email, app, note in rows:
        entry = grouped.setdefault(email, {"apps": [], "note": ""})
        entry["apps"].append(app_names.get(app, app))
        entry["note"] = entry["note"] or note
    order = list(app_names.values())  # the apps in their usual order, not alphabetical
    for entry in grouped.values():
        entry["apps"].sort(key=lambda name: order.index(name) if name in order else len(order))
    lines = []
    for email, entry in list(grouped.items())[:DIGEST_PEOPLE_MAX]:
        first, last = names.get(email, ("", ""))
        who = f"{(first + ' ' + last).strip()} ({email})" if (first or last) else email
        lines.append(f"{who}: {', '.join(entry['apps'])}" + (f' - "{entry["note"]}"' if entry["note"] else ""))
    if len(grouped) > DIGEST_PEOPLE_MAX:
        lines.append(f"...and {len(grouped) - DIGEST_PEOPLE_MAX} more")
    count = len(grouped)
    return f"Brassworks: {count} {'person' if count == 1 else 'people'} asked for access", "\n".join(lines)


def send_digest(now: Optional[float] = None) -> int:
    """Tell the administrator about the requests nobody has been told of yet, in ONE message for all of them (so a person asking
    for five apps, or ten people signing up in a row, is one email, not fifty). Does nothing until `config.REQUEST_DIGEST_MINUTES`
    have passed since the last time it ran, which is kept in the database, so a restart can neither skip nor repeat a digest.
    Returns how many people were announced. Best effort: a failed send is logged, never raised (the Users page still lists them)."""
    now = int(time.time() if now is None else now)
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")  # take the requests and mark them in one go, so one filed meanwhile is not lost
        row = connection.execute("SELECT value FROM meta WHERE key = 'last_digest'").fetchone()
        if row and now - int(row[0]) < config.REQUEST_DIGEST_MINUTES * 60:
            connection.rollback()
            return 0
        connection.execute("INSERT INTO meta (key, value) VALUES ('last_digest', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(now),))
        rows = connection.execute(
            "SELECT email, app, note FROM access_requests WHERE status = 'pending' AND announced_at IS NULL ORDER BY created_at, email, app"
        ).fetchall()
        connection.execute("UPDATE access_requests SET announced_at = ? WHERE status = 'pending' AND announced_at IS NULL", (now,))
        connection.commit()
    if not rows:
        return 0
    title, body = digest_message(rows, get_names(), {a["key"]: a["name"] for a in member_apps()})
    try:
        notify.notify(title, body, f"{config.DASHBOARD_URL}users.html")
    except Exception as exc:  # noqa: BLE001
        log.warning("Telling the administrator about the requests failed: %s", notify.describe_failure(exc))
    return len({email for email, _app, _note in rows})


# --- driving the apps --------------------------------------------------------------------------------------------------


def member_apps() -> List[dict]:
    return [a for a in config.APPS if a["access"] == "member" and a["key"] in config.APP_USERS]


def _call(app: dict, method: str, suffix: str = "", cookie: Optional[Tuple[str, str]] = None, **kwargs) -> requests.Response:
    base = config.APP_API_BASE[app["key"]]
    headers = {probe.PROBE_HEADER: "1"}  # the dashboard acting for the administrator is not the administrator using that app
    if cookie and cookie[0] and cookie[1]:
        headers["Cookie"] = f"{cookie[0]}={cookie[1]}"
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
    lines = [f"{admin_email} gave you access to {which}."]
    if config.ENV == "production":
        # Whether this address already has an account decides the link: sign in, or create an account with this address filled in.
        lines += notify.sign_in_lines(email, config.DASHBOARD_URL, login_url=f"{config.DASHBOARD_URL}auth/login")
    else:
        lines += ["", f"Open it here: {config.DASHBOARD_URL}", "", f"Sign in with this email address (it's the one you were let in as): {email}"]
    try:
        notify.send_email(email, f"{admin_email} gave you access to {which}", "\n".join(lines))
    except Exception as exc:  # noqa: BLE001 - never fail the change because the email did
        log.warning("Telling the new person by email failed: %s", notify.describe_failure(exc))
