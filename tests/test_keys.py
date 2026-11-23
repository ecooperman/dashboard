"""Everyone's own API keys, kept once on the dashboard and handed only to the apps, only for the person who is signed in."""
import sqlite3

import pytest
import requests
from shared_python import key_providers

from app import config, keys
from tests.test_home import apps_say, client, id_token, key, production  # noqa: F401 - fixtures

SECRET = "sk-ant-api03-THIS-IS-A-SECRET-KEY-1234"


class Checks(list):
    answer: dict = {}


@pytest.fixture(autouse=True)
def checked(monkeypatch):
    """What the services would say about a key (nothing leaves the machine)."""
    from shared_python import settings

    monkeypatch.setattr(settings, "KEY_ENCRYPTION_SECRET", "a-test-secret")  # in production the secret must be set, or nothing can be saved
    calls = Checks()
    answer = {"verdict": (True, "")}
    monkeypatch.setattr(key_providers, "verify", lambda provider, k: calls.append((provider, k)) or answer["verdict"])
    calls.answer = answer
    return calls


def person(production, key, email="ann@example.com"):  # noqa: F811
    production.cookies.set("oidc_session", id_token(key, email))
    return production


# --- storing -----------------------------------------------------------------------------------------------------------------------


def test_a_key_is_stored_encrypted_with_only_its_last_four_in_the_clear():
    keys.save("Ann@Example.com", "anthropic", SECRET, now=1_000_000)
    with sqlite3.connect(config.PEOPLE_DB) as db:
        row = db.execute("SELECT email, provider, encrypted_key, last4 FROM api_keys").fetchone()
    assert row[0] == "ann@example.com" and row[1] == "anthropic" and row[3] == "1234"
    assert SECRET not in row[2] and "SECRET" not in row[2]
    assert keys.get_plain("ann@example.com", "anthropic") == SECRET


def test_saving_again_replaces_and_each_person_and_service_has_their_own():
    keys.save("ann@example.com", "anthropic", SECRET)
    keys.save("ann@example.com", "anthropic", SECRET.replace("1234", "9999"))
    keys.save("ann@example.com", "jina", "jina-key-0123456789abcdef")
    keys.save("bob@example.com", "anthropic", "sk-bob-0123456789abcdefghij")
    assert keys.get_plain("ann@example.com", "anthropic").endswith("9999")
    assert keys.get_plain("bob@example.com", "anthropic") == "sk-bob-0123456789abcdefghij"
    assert set(keys.status("ann@example.com")) == {"anthropic", "jina"} and set(keys.status("bob@example.com")) == {"anthropic"}


def test_status_never_contains_a_key_and_removing_works():
    keys.save("ann@example.com", "anthropic", SECRET)
    assert SECRET not in repr(keys.status("ann@example.com")) and SECRET not in repr(keys.providers_view("ann@example.com"))
    assert keys.remove("ann@example.com", "anthropic") is True and keys.remove("ann@example.com", "anthropic") is False
    assert keys.get_plain("ann@example.com", "anthropic") is None


def test_something_that_cannot_be_a_key_or_an_unknown_service_is_refused():
    for bad in ("", "short", "has spaces in it and is long enough"):
        with pytest.raises(ValueError):
            keys.save("ann@example.com", "anthropic", bad)
    with pytest.raises(ValueError):
        keys.save("ann@example.com", "nonsense", SECRET)


def test_a_key_that_can_no_longer_be_decrypted_is_reported_as_unreadable_not_a_crash(monkeypatch):
    from shared_python import settings

    monkeypatch.setattr(settings, "KEY_ENCRYPTION_SECRET", "first-secret")
    keys.save("ann@example.com", "anthropic", SECRET)
    monkeypatch.setattr(settings, "KEY_ENCRYPTION_SECRET", "second-secret")
    assert keys.get_plain("ann@example.com", "anthropic") is None
    view = {v["provider"]: v for v in keys.providers_view("ann@example.com")}
    assert view["anthropic"]["has_key"] is True and view["anthropic"]["readable"] is False


# --- the Profile page's routes -----------------------------------------------------------------------------------------------------


def test_a_person_let_into_an_app_saves_checks_and_removes_a_key(production, key, apps_say, checked):  # noqa: F811
    person(production, key)
    got = production.get("/api/keys").json()
    assert got["can_save"] is True and [k["provider"] for k in got["keys"]] == ["anthropic", "google_maps", "jina"]
    assert all(k["has_key"] is False for k in got["keys"])
    saved = production.put("/api/keys/anthropic", json={"key": f"  {SECRET}  "})
    assert saved.status_code == 200 and saved.json() == {"verified": True, "note": ""}
    assert checked == [("anthropic", SECRET)]  # trimmed, and checked with the service before it was kept
    mine = {k["provider"]: k for k in production.get("/api/keys").json()["keys"]}
    assert mine["anthropic"]["has_key"] and mine["anthropic"]["last4"] == "1234" and SECRET not in production.get("/api/keys").text
    assert production.delete("/api/keys/anthropic").status_code == 200
    assert production.delete("/api/keys/anthropic").status_code == 404


def test_a_key_the_service_refuses_is_not_kept_and_one_that_could_not_be_checked_is_kept_with_a_note(production, key, apps_say, checked):  # noqa: F811
    person(production, key)
    checked.answer["verdict"] = (False, "Anthropic didn't accept that key")
    refused = production.put("/api/keys/anthropic", json={"key": SECRET})
    assert refused.status_code == 400 and "didn't accept" in refused.json()["detail"] and keys.status("ann@example.com") == {}
    checked.answer["verdict"] = (None, "Couldn't reach Anthropic to check the key")
    saved = production.put("/api/keys/anthropic", json={"key": SECRET}).json()
    assert saved["verified"] is False and "not checked" in saved["note"] and "anthropic" in keys.status("ann@example.com")


def test_a_stranger_who_is_not_let_into_any_app_and_a_signed_out_visitor_cannot_use_keys(production, key, apps_say):  # noqa: F811
    apps_say.status = {k: 403 for k in apps_say.status}
    person(production, key, "stranger@example.com")
    assert production.get("/api/keys").status_code == 403
    assert production.put("/api/keys/anthropic", json={"key": SECRET}).status_code == 403
    production.cookies.clear()
    assert production.get("/api/keys").status_code == 401


def test_a_bad_key_and_an_unknown_service_get_a_sentence(production, key, apps_say):  # noqa: F811
    person(production, key)
    assert production.put("/api/keys/anthropic", json={"key": "nope"}).status_code == 400
    assert production.put("/api/keys/nonsense", json={"key": SECRET}).status_code == 404 and production.delete("/api/keys/nonsense").status_code == 404


# --- what an app gets ----------------------------------------------------------------------------------------------------------------


def test_an_app_gets_only_the_key_of_the_person_whose_cookie_it_forwards(production, key, apps_say):  # noqa: F811
    keys.save("ann@example.com", "anthropic", SECRET)
    keys.save("bob@example.com", "anthropic", "sk-bob-0123456789abcdefghij")
    person(production, key, "ann@example.com")
    got = production.get("/internal/keys/anthropic")
    assert got.status_code == 200 and got.json() == {"key": SECRET} and "no-store" in got.headers["cache-control"]
    assert production.get("/internal/keys").json() == {"keys": keys.status("ann@example.com")}
    assert "sk-bob" not in production.get("/internal/keys/anthropic").text
    person(production, key, "carol@example.com")
    assert production.get("/internal/keys/anthropic").status_code == 404  # signed in, nothing saved
    production.cookies.clear()
    assert production.get("/internal/keys/anthropic").status_code == 401  # nobody signed in: no key for anyone


def test_the_internet_cannot_ask_for_a_key_even_signed_in(production, key, apps_say):  # noqa: F811
    keys.save("ann@example.com", "anthropic", SECRET)
    person(production, key)
    for path in ("/internal/keys/anthropic", "/internal/keys"):
        for header in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For"):
            assert production.get(path, headers={header: "203.0.113.5"}).status_code == 404, (path, header)


def test_without_the_encryption_secret_in_production_nothing_can_be_saved_and_the_page_says_so(production, key, apps_say, monkeypatch):  # noqa: F811
    from shared_python import settings

    monkeypatch.setattr(settings, "KEY_ENCRYPTION_SECRET", None)
    person(production, key)
    assert production.get("/api/keys").json()["can_save"] is False
    assert production.put("/api/keys/anthropic", json={"key": SECRET}).status_code == 503
    assert keys.status("ann@example.com") == {}
