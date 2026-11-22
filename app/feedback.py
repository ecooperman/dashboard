"""Send feedback, for every app: one inbox on the dashboard, separated by app.

Anyone with an account in an app can send the administrator a message (a problem, an idea or a question) from a small form in that app's
menu. The app passes it on to the dashboard (shared_python.feedback_relay), which keeps it here, tells the administrator, and lets them
read and tick off messages by app on the Feedback page. A person sees only their own on their Profile page. Kept small on purpose: a
handful a day each, nothing attached; a message carries the app it came from and the page they were on (a path only)."""
import logging
import sqlite3
import time
from contextlib import closing
from typing import List, Optional

from shared_python import notify

from . import config

log = logging.getLogger(__name__)

KINDS = {"problem": "Problem", "idea": "Idea", "question": "Question"}
MAX_PER_DAY = 10
MAX_BODY = 4000
DASHBOARD_KEY = "dashboard"


class TooMany(Exception):
    """Too many messages from one person today."""


def app_names() -> dict:
    names = {a["key"]: a["name"] for a in config.APPS}
    names[DASHBOARD_KEY] = "Dashboard"
    return names


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.PEOPLE_DB, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT NOT NULL, email TEXT NOT NULL, kind TEXT NOT NULL, "
        "body TEXT NOT NULL, page TEXT, created_at INTEGER NOT NULL, done_at INTEGER)"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return connection


def _view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "app": row["app"], "app_name": app_names().get(row["app"], row["app"]), "email": row["email"], "kind": row["kind"],
        "kind_label": KINDS.get(row["kind"], row["kind"]), "body": row["body"], "page": row["page"], "created_at": row["created_at"], "done_at": row["done_at"],
    }


def _page(value: Optional[str]) -> Optional[str]:
    """Only a path on the app ("/jobs.html") is kept, so a message cannot carry a stranger's link."""
    value = (value or "").strip()
    return value[:100] if value.startswith("/") and not value.startswith("//") else None


def send(app_key: str, email: str, kind: str, body: str, page: Optional[str] = None, now: Optional[float] = None) -> dict:
    """Keep a message. ValueError (with a sentence for the person) for something wrong with it, TooMany past the daily limit."""
    if app_key not in app_names():
        raise ValueError("That isn't an app.")
    kind, text = (kind or "").strip().lower(), (body or "").strip()
    if kind not in KINDS:
        raise ValueError("Choose what kind of message this is: a problem, an idea or a question.")
    if len(text) < 3:
        raise ValueError("Write a few words first.")
    if len(text) > MAX_BODY:
        raise ValueError(f"That message is {len(text):,} characters; the limit is {MAX_BODY:,}. Please shorten it.")
    now = int(time.time() if now is None else now)
    email = email.strip().lower()
    with closing(_connect()) as connection:
        sent = connection.execute("SELECT COUNT(*) FROM feedback WHERE email = ? AND created_at >= ?", (email, now - 86400)).fetchone()[0]
        if sent >= MAX_PER_DAY:
            raise TooMany(f"You have sent {MAX_PER_DAY} messages today. Please try again tomorrow, or add to your last one by email.")
        cursor = connection.execute(
            "INSERT INTO feedback (app, email, kind, body, page, created_at) VALUES (?, ?, ?, ?, ?, ?)", (app_key, email, kind, text, _page(page), now)
        )
        connection.commit()
        return _view(connection.execute("SELECT * FROM feedback WHERE id = ?", (cursor.lastrowid,)).fetchone())


def announce(message: dict) -> None:
    """Tell the administrator (ntfy, Pushover or email, whichever is set up); the Feedback page holds it either way. Never raises."""
    text = " ".join(message["body"].split())
    try:
        notify.notify(f"{message['app_name']}: {message['kind_label']} from {message['email']}", text[:500] + ("..." if len(text) > 500 else ""), f"{config.DASHBOARD_URL}feedback.html")
    except Exception as exc:  # noqa: BLE001
        log.warning("Telling the administrator about a message failed: %s", notify.describe_failure(exc))


def mine(email: str, limit: int = 20) -> List[dict]:
    with closing(_connect()) as connection:
        rows = connection.execute("SELECT * FROM feedback WHERE email = ? ORDER BY id DESC LIMIT ?", (email.strip().lower(), limit)).fetchall()
    return [_view(r) for r in rows]


def inbox() -> List[dict]:
    """Everyone's, the ones still waiting first and the newest first within each."""
    with closing(_connect()) as connection:
        rows = connection.execute("SELECT * FROM feedback ORDER BY done_at IS NOT NULL, id DESC").fetchall()
    return [_view(r) for r in rows]


def waiting_count() -> int:
    with closing(_connect()) as connection:
        return connection.execute("SELECT COUNT(*) FROM feedback WHERE done_at IS NULL").fetchone()[0]


def set_done(message_id: int, done: bool, now: Optional[float] = None) -> Optional[dict]:
    with closing(_connect()) as connection:
        cursor = connection.execute("UPDATE feedback SET done_at = ? WHERE id = ?", (int(time.time() if now is None else now) if done else None, message_id))
        connection.commit()
        if not cursor.rowcount:
            return None
        return _view(connection.execute("SELECT * FROM feedback WHERE id = ?", (message_id,)).fetchone())


def remove(message_id: int) -> bool:
    with closing(_connect()) as connection:
        cursor = connection.execute("DELETE FROM feedback WHERE id = ?", (message_id,))
        connection.commit()
        return bool(cursor.rowcount)


# --- the Jobs app's old inbox, copied over once ---------------------------------------------------------------------------------


def imported_from_jobs() -> bool:
    with closing(_connect()) as connection:
        return connection.execute("SELECT 1 FROM meta WHERE key = 'jobs_feedback_imported'").fetchone() is not None


def import_from_jobs(rows: List[dict]) -> int:
    """Copy the messages the Jobs app kept itself (its `/api/admin/messages`: kind, body, page, from_email, created_at and done_at as ISO
    UTC times) into the inbox as messages from the `resume` app, keeping who sent them, when, and whether they were dealt with. Done at
    most once: after that this does nothing, so pressing the button twice cannot duplicate them. Returns how many were copied."""
    def seconds(iso: Optional[str]) -> Optional[int]:
        if not iso:
            return None
        from datetime import datetime, timezone

        return int(datetime.fromisoformat(iso.replace("Z", "")).replace(tzinfo=timezone.utc).timestamp())

    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM meta WHERE key = 'jobs_feedback_imported'").fetchone():
            connection.rollback()
            return 0
        copied = 0
        for row in sorted(rows, key=lambda r: r.get("id", 0)):
            kind = (row.get("kind") or "").lower()
            body = (row.get("body") or "").strip()
            if kind not in KINDS or not body or not row.get("from_email"):
                continue
            connection.execute(
                "INSERT INTO feedback (app, email, kind, body, page, created_at, done_at) VALUES ('resume', ?, ?, ?, ?, ?, ?)",
                (row["from_email"].strip().lower(), kind, body[:MAX_BODY], _page(row.get("page")), seconds(row.get("created_at")) or int(time.time()), seconds(row.get("done_at"))),
            )
            copied += 1
        connection.execute("INSERT INTO meta (key, value) VALUES ('jobs_feedback_imported', ?)", (str(int(time.time())),))
        connection.commit()
        return copied
