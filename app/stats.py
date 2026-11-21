"""The stats pages: a rough sense of whether and how the apps are being used (administrators only).

Each app answers its own administrator-only `GET /api/admin/stats` (shared_python.usage): for every account, when they were last
there, on how many days, and counts of what they keep (numbers and dates, never any content). This module asks every app in
parallel AS THE SIGNED-IN ADMINISTRATOR (their own session cookie, fixed localhost addresses, no redirects followed: the same way the
People page drives the apps) and puts the answers together two ways: `overview` (every app side by side, and across apps) and
`person` (one person across every app). An app that is down, or has not been updated to report yet, is named and left out of the
sums, never guessed.

"Active" means the person's last activity is inside the window, where last activity is the later of when they were last seen and the
newest thing they made there (so it also counts what happened before last-seen tracking began). By default administrators are left
out of the totals: this is for seeing how everyone else uses the apps, and the administrator's own use would swamp it."""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from . import config, people

log = logging.getLogger(__name__)

STATS_ROUTE = "/api/admin/stats"
WINDOWS = (7, 30)  # days: "this week" and "this month"


def _when(value: Optional[str]) -> Optional[datetime]:
    """An app's timestamp ("2026-11-21T09:30:00Z", UTC) as a naive UTC datetime; None for nothing or anything unreadable."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ") if value else None
    except (TypeError, ValueError):
        return None


def ask(app: dict, cookie: Tuple[str, str]) -> dict:
    """One app's answer: {"key", "name", "state", "why", "accounts"}. `state` is "ok", "not_reporting" (the app has no stats route yet:
    it has not been deployed with it), or "problem" (it did not answer, did not accept the sign-in, or said something unreadable)."""
    out = {"key": app["key"], "name": app["name"], "state": "problem", "why": "", "accounts": []}
    headers = {"Cookie": f"{cookie[0]}={cookie[1]}"} if cookie and cookie[0] and cookie[1] else {}
    try:
        resp = requests.request("GET", f"{config.APP_API_BASE[app['key']]}{STATS_ROUTE}", headers=headers, timeout=config.APP_CALL_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        log.warning("Could not ask %s for its stats: %s", app["key"], type(exc).__name__)
        out["why"] = f"{app['name']} didn't answer"
        return out
    if resp.status_code in (404, 405):
        out.update(state="not_reporting", why=f"{app['name']} doesn't report stats yet (it needs its next deploy)")
    elif resp.status_code in (301, 302, 303, 307, 308, 401):
        out["why"] = f"{app['name']} didn't accept your sign-in"
    elif resp.status_code == 403:
        out["why"] = f"{app['name']} says you aren't an administrator there"
    elif resp.status_code != 200:
        out["why"] = f"{app['name']} answered {resp.status_code}"
    else:
        try:
            accounts = [a for a in resp.json()["accounts"] if isinstance(a, dict) and a.get("email")]
        except (ValueError, KeyError, TypeError):
            out["why"] = f"{app['name']} gave an answer that couldn't be read"
            return out
        out.update(state="ok", accounts=accounts)
    return out


def ask_all(cookie: Tuple[str, str]) -> List[dict]:
    apps = people.member_apps()
    with ThreadPoolExecutor(max_workers=max(len(apps), 1)) as pool:
        return list(pool.map(lambda a: ask(a, cookie), apps))


def _is_admin(account: dict) -> bool:
    return account["email"].strip().lower() in config.ADMIN_EMAILS


def _active_within(account: dict, days: int, now: datetime) -> bool:
    last = _when(account.get("last_activity_at"))
    return last is not None and now - last <= timedelta(days=days)


def _summary(answer: dict, include_admins: bool, now: datetime) -> dict:
    """One app's row of the overview."""
    row = {"key": answer["key"], "name": answer["name"], "state": answer["state"], "why": answer["why"]}
    if answer["state"] != "ok":
        return row
    accounts = [a for a in answer["accounts"] if a.get("status") == "active" and (include_admins or not _is_admin(a))]
    totals: Dict[str, int] = {}
    for account in accounts:
        for label, count in (account.get("counts") or {}).items():
            totals[label] = totals.get(label, 0) + int(count or 0)
    lasts = [t for t in (_when(a.get("last_activity_at")) for a in accounts) if t]
    row.update(
        accounts=len(accounts),
        active_7d=sum(_active_within(a, 7, now) for a in accounts),
        active_30d=sum(_active_within(a, 30, now) for a in accounts),
        never_used=sum(1 for a in accounts if not a.get("last_activity_at")),
        active_days=sum(int(a.get("active_days") or 0) for a in accounts),
        totals=totals,
        last_activity_at=max(lasts).strftime("%Y-%m-%dT%H:%M:%SZ") if lasts else None,
    )
    return row


def overview(cookie: Tuple[str, str], include_admins: bool = False, now: Optional[datetime] = None, answers: Optional[List[dict]] = None) -> dict:
    """Every app side by side, and the people across all of them."""
    now = now or datetime.utcnow()
    answers = answers if answers is not None else ask_all(cookie)
    per_person: Dict[str, dict] = {}  # email -> {"apps": n, "last": datetime or None}
    for answer in answers:
        if answer["state"] != "ok":
            continue
        for account in answer["accounts"]:
            if account.get("status") != "active" or (_is_admin(account) and not include_admins):
                continue
            entry = per_person.setdefault(account["email"].strip().lower(), {"apps": 0, "last": None})
            entry["apps"] += 1
            last = _when(account.get("last_activity_at"))
            if last and (entry["last"] is None or last > entry["last"]):
                entry["last"] = last
    everyone = list(per_person.values())
    return {
        "include_admins": include_admins,
        "apps": [_summary(a, include_admins, now) for a in answers],
        "people": {
            "with_an_account": len(everyone),
            "in_several_apps": sum(1 for p in everyone if p["apps"] > 1),
            "active_7d": sum(1 for p in everyone if p["last"] and now - p["last"] <= timedelta(days=7)),
            "active_30d": sum(1 for p in everyone if p["last"] and now - p["last"] <= timedelta(days=30)),
            "never_used": sum(1 for p in everyone if p["last"] is None),
        },
        "waiting_requests": people.waiting_count(),
        "reporting": sum(1 for a in answers if a["state"] == "ok"),
        "not_reporting": [{"name": a["name"], "why": a["why"]} for a in answers if a["state"] != "ok"],
    }


def person(email: str, cookie: Tuple[str, str], answers: Optional[List[dict]] = None) -> dict:
    """One person across every app: for each, whether they have an account and what the app says about them."""
    email = email.strip().lower()
    answers = answers if answers is not None else ask_all(cookie)
    asked = people.request_states(email)
    first, last = people.get_name(email)
    apps, lasts, days = [], [], 0
    for answer in answers:
        row = {"key": answer["key"], "name": answer["name"], "state": answer["state"], "why": answer["why"], "account": None,
               "request": asked.get(answer["key"], "")}
        if answer["state"] == "ok":
            account = next((a for a in answer["accounts"] if a["email"].strip().lower() == email), None)
            if account is not None:
                row["account"] = {k: account.get(k) for k in ("status", "created_at", "last_seen_at", "active_days", "counts", "last_activity_at")}
                row["account"]["counts"] = account.get("counts") or {}
                days += int(account.get("active_days") or 0)
                if _when(account.get("last_activity_at")):
                    lasts.append(_when(account.get("last_activity_at")))
        apps.append(row)
    return {
        "email": email, "first_name": first, "last_name": last, "is_admin": email in config.ADMIN_EMAILS,
        "apps": apps,
        "apps_with_an_account": sum(1 for a in apps if a["account"] and a["account"]["status"] == "active"),
        "last_activity_at": max(lasts).strftime("%Y-%m-%dT%H:%M:%SZ") if lasts else None,
        "active_days": days,
    }
