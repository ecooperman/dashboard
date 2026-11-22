"""How many people used each app on each day: the history behind the Usage page's chart.

Each app tells the dashboard (shared_python.usage, POST /internal/active-day) the first time it sees a person on a new UTC day. The
message carries the app, the address and the day; the address is used ONLY to tell an administrator from anyone else and is never
stored: all that is kept is, per day and app, how many other people and how many administrators were there. So it is counts and dates
only, like the rest of the stats pages, and it starts when each app is next deployed (nothing before that was recorded). A message that
never arrives (the dashboard was down at that moment) is a person-day missing for good: this is a rough picture, not an audit."""
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from . import config

DAYS_SHOWN = 30


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.PEOPLE_DB, timeout=5)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS daily_active (day TEXT NOT NULL, app TEXT NOT NULL, people INTEGER NOT NULL DEFAULT 0, "
        "admins INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, app))"
    )
    return connection


def record(app_key: str, email: str, day: str, today: Optional[date] = None) -> None:
    """Add one person to `app_key`'s count for `day`. ValueError for a day that is not a recent date (a wrong clock, or someone making
    numbers up): an app only ever reports today's date."""
    today = today or datetime.utcnow().date()
    try:
        when = datetime.strptime(day, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError("That is not a date")
    if abs((when - today).days) > 2:
        raise ValueError("That day is not recent")
    is_admin = email.strip().lower() in config.ADMIN_EMAILS
    with closing(_connect()) as connection:
        connection.execute(
            "INSERT INTO daily_active (day, app, people, admins) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (day, app) DO UPDATE SET people = people + excluded.people, admins = admins + excluded.admins",
            (day, app_key, 0 if is_admin else 1, 1 if is_admin else 0),
        )
        connection.commit()


def series(include_admins: bool = False, today: Optional[date] = None, days: int = DAYS_SHOWN) -> dict:
    """The last `days` days (oldest first, today last) of people active per day: for each app, and together. `since` is the first day
    anything was recorded (None while nothing has been), so the page can say the history only starts there."""
    today = today or datetime.utcnow().date()
    labels = [(today - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]
    keys = [a["key"] for a in config.APPS if a["access"] == "member"]
    names = {a["key"]: a["name"] for a in config.APPS}
    counts: Dict[str, Dict[str, int]] = {key: {day: 0 for day in labels} for key in keys}
    with closing(_connect()) as connection:
        for day, app_key, people, admins in connection.execute("SELECT day, app, people, admins FROM daily_active WHERE day >= ?", (labels[0],)):
            if app_key in counts and day in counts[app_key]:
                counts[app_key][day] += people + (admins if include_admins else 0)
        first = connection.execute("SELECT MIN(day) FROM daily_active").fetchone()[0]
    apps: List[dict] = [{"key": key, "name": names[key], "counts": [counts[key][day] for day in labels]} for key in keys]
    return {
        "days": labels,
        "apps": apps,
        "total": [sum(app["counts"][i] for app in apps) for i in range(len(labels))],
        "since": first,
    }
