"""Everyone's own API keys, kept once.

A person saves a key for a paid service (Anthropic, Google Maps, Jina) on their Profile page; the apps that need it (Trips, Jobs) ask for it here
at the moment they use it (shared_python.user_keys, over localhost, with the person's own sign-in cookie). So a key is one thing per person,
not one per app, and no app keeps one in its own database.

Keys are encrypted (shared_python.keystore, KEY_ENCRYPTION_SECRET from shared.env), never sent to a browser (only the last four characters
are kept in the clear) and never logged. Honest limit, as in keystore: this protects a copy of the database file; someone who takes over the
running server holds the secret too."""
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

from shared_python import key_providers, keystore

from . import config


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.PEOPLE_DB, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS api_keys (email TEXT NOT NULL, provider TEXT NOT NULL, encrypted_key TEXT NOT NULL, last4 TEXT NOT NULL, "
        "updated_at INTEGER NOT NULL, PRIMARY KEY (email, provider))"
    )
    return connection


def _iso(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(email: str, provider: str, raw_key: str, now: Optional[float] = None) -> None:
    """ValueError for something that cannot be a key; keystore.KeyStorageNotConfigured when the encryption secret is missing."""
    if provider not in key_providers.PROVIDERS:
        raise ValueError("That isn't a service we keep keys for")
    key = keystore.clean(raw_key)
    token = keystore.encrypt(key)
    with closing(_connect()) as connection:
        connection.execute(
            "INSERT INTO api_keys (email, provider, encrypted_key, last4, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (email, provider) DO UPDATE SET encrypted_key = excluded.encrypted_key, last4 = excluded.last4, updated_at = excluded.updated_at",
            (email.strip().lower(), provider, token, keystore.last4(key), int(time.time() if now is None else now)),
        )
        connection.commit()


def remove(email: str, provider: str) -> bool:
    with closing(_connect()) as connection:
        cursor = connection.execute("DELETE FROM api_keys WHERE email = ? AND provider = ?", (email.strip().lower(), provider))
        connection.commit()
        return bool(cursor.rowcount)


def get_plain(email: str, provider: str) -> Optional[str]:
    """The key itself, for an app that is using it right now. None when there is none, or it cannot be read (the secret changed)."""
    with closing(_connect()) as connection:
        row = connection.execute("SELECT encrypted_key FROM api_keys WHERE email = ? AND provider = ?", (email.strip().lower(), provider)).fetchone()
    return keystore.decrypt(row["encrypted_key"], provider) if row else None


def status(email: str) -> dict:
    """{provider: {"last4", "added_at"}} for what this person saved. Never a key."""
    with closing(_connect()) as connection:
        rows = connection.execute("SELECT provider, last4, updated_at FROM api_keys WHERE email = ?", (email.strip().lower(),)).fetchall()
    return {r["provider"]: {"last4": r["last4"], "added_at": _iso(r["updated_at"])} for r in rows}


def providers_view(email: str) -> list:
    """What the Profile page shows: each service, and whether this person has a key for it (and whether it can still be read)."""
    mine = status(email)
    out = []
    for p in key_providers.PROVIDERS.values():
        saved = mine.get(p.id)
        out.append({
            "provider": p.id, "label": p.label, "feature": p.feature, "description": p.description, "optional": p.optional, "get_url": p.get_url,
            "has_key": saved is not None,
            "readable": True if saved is None else get_plain(email, p.id) is not None,
            "last4": saved["last4"] if saved else None, "added_at": saved["added_at"] if saved else None,
        })
    return out
