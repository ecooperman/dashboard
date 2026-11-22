"""Send feedback for every app: one inbox on the dashboard, separated by app."""
import pytest
import requests

from app import access, feedback
from tests.test_home import apps_say, client, id_token, key, production  # noqa: F401 - fixtures
from tests.test_people import admin  # noqa: F401 - fixture

BODY = {"kind": "problem", "body": "The button does nothing", "page": "/lists.html"}


@pytest.fixture(autouse=True)
def told(monkeypatch):
    """What the administrator would have been told."""
    sent = []
    monkeypatch.setattr(feedback.notify, "notify", lambda title, message, link=None: sent.append((title, message, link)))
    return sent


# --- keeping a message -----------------------------------------------------------------------------------------------------------


def test_a_message_keeps_the_app_who_sent_it_what_and_where():
    got = feedback.send("lists", "Ann@Example.com", " Problem ", "  It broke  ", "/list.html?id=3")
    assert got["app"] == "lists" and got["app_name"] == "Lists" and got["email"] == "ann@example.com"
    assert got["kind"] == "problem" and got["body"] == "It broke" and got["page"] == "/list.html?id=3" and got["done_at"] is None


def test_only_a_path_on_the_app_is_kept_as_the_page():
    assert feedback.send("lists", "a@example.com", "idea", "hello there", "https://evil.example.com/x")["page"] is None
    assert feedback.send("lists", "a@example.com", "idea", "hello there", "//evil.example.com/x")["page"] is None
    assert feedback.send("lists", "a@example.com", "idea", "hello there", "/ok.html")["page"] == "/ok.html"


def test_a_bad_message_is_refused_with_a_sentence():
    for kind, body, app_key, words in [("rant", "hello there", "lists", "kind of message"), ("idea", "  ", "lists", "few words"),
                                       ("idea", "x" * 4001, "lists", "limit"), ("idea", "hello there", "nowhere", "isn't an app")]:
        with pytest.raises(ValueError) as caught:
            feedback.send(app_key, "a@example.com", kind, body)
        assert words in str(caught.value)


def test_ten_a_day_per_person_then_the_next_day_is_fine():
    for n in range(10):
        feedback.send("lists", "a@example.com", "idea", f"message {n}", now=1_000_000 + n)
    with pytest.raises(feedback.TooMany):
        feedback.send("trips", "a@example.com", "idea", "one too many", now=1_000_100)  # the limit is per person, across apps
    feedback.send("lists", "b@example.com", "idea", "someone else is fine", now=1_000_100)
    feedback.send("lists", "a@example.com", "idea", "a day later", now=1_000_000 + 86400 + 20)


# --- sending it from an app (the relay) and from the dashboard itself ------------------------------------------------------------


def test_an_app_passes_a_message_on_with_the_persons_cookie_and_the_administrator_is_told(production, key, told):  # noqa: F811
    production.cookies.set("oidc_session", id_token(key, "ann@example.com"))
    resp = production.post("/internal/feedback", json={**BODY, "app": "trips"})
    assert resp.status_code == 200 and resp.json()["app"] == "trips" and resp.json()["email"] == "ann@example.com"
    assert told and told[0][0] == "Trips: Problem from ann@example.com" and "The button does nothing" in told[0][1]
    assert told[0][2].endswith("/feedback.html")


def test_the_internal_route_refuses_the_internet_strangers_and_unknown_apps(production, key):  # noqa: F811
    production.cookies.set("oidc_session", id_token(key, "ann@example.com"))
    assert production.post("/internal/feedback", json={**BODY, "app": "trips"}, headers={"Cf-Connecting-Ip": "203.0.113.5"}).status_code == 404
    assert production.post("/internal/feedback", json={**BODY, "app": "nowhere"}).status_code == 400
    production.cookies.clear()
    assert production.post("/internal/feedback", json={**BODY, "app": "trips"}).status_code == 401  # nobody signed in
    assert feedback.inbox() == []


def test_the_dashboards_own_pages_send_as_the_dashboard_but_only_for_people_let_into_an_app(production, key, apps_say):  # noqa: F811
    production.cookies.set("oidc_session", id_token(key, "ann@example.com"))
    assert production.post("/api/feedback", json=BODY).json()["app"] == "dashboard"
    apps_say.status = {k: 403 for k in apps_say.status}  # now no app has an account for them
    access.forget_apps()
    production.cookies.set("oidc_session", id_token(key, "nobody@example.com"))
    assert production.post("/api/feedback", json=BODY).status_code == 403


def test_people_see_only_their_own_messages(production, key):  # noqa: F811
    feedback.send("lists", "ann@example.com", "idea", "from ann")
    feedback.send("lists", "bob@example.com", "idea", "from bob")
    production.cookies.set("oidc_session", id_token(key, "ann@example.com"))
    assert [m["body"] for m in production.get("/api/feedback/mine").json()["messages"]] == ["from ann"]


# --- the administrator's inbox ---------------------------------------------------------------------------------------------------


def test_only_the_administrator_reads_or_settles_the_inbox(production, key):  # noqa: F811
    message = feedback.send("lists", "ann@example.com", "idea", "a message")
    production.cookies.set("oidc_session", id_token(key, "ann@example.com"))
    assert production.get("/api/feedback").status_code == 403
    assert production.post(f"/api/feedback/{message['id']}/done").status_code == 403
    assert production.delete(f"/api/feedback/{message['id']}").status_code == 403
    assert production.get("/feedback.html").status_code in (302, 307)  # sent home


def test_the_administrator_reads_ticks_off_reopens_and_deletes(admin):  # noqa: F811
    first = feedback.send("lists", "ann@example.com", "idea", "first")
    second = feedback.send("trips", "bob@example.com", "problem", "second")
    got = admin.get("/api/feedback").json()
    assert [m["body"] for m in got["messages"]] == ["second", "first"] and got["can_import_jobs"] is True  # newest first
    assert admin.post(f"/api/feedback/{second['id']}/done").json()["done_at"] is not None
    assert [m["body"] for m in admin.get("/api/feedback").json()["messages"]] == ["first", "second"]  # dealt-with ones sink
    assert admin.post(f"/api/feedback/{second['id']}/reopen").json()["done_at"] is None
    assert admin.delete(f"/api/feedback/{first['id']}").status_code == 200
    assert [m["body"] for m in admin.get("/api/feedback").json()["messages"]] == ["second"]
    assert admin.post("/api/feedback/999/done").status_code == 404 and admin.delete("/api/feedback/999").status_code == 404
    assert admin.post(f"/api/feedback/{second['id']}/bogus").status_code == 404


def test_the_menu_badge_counts_what_is_waiting(admin):  # noqa: F811
    feedback.send("lists", "ann@example.com", "idea", "one")
    done = feedback.send("lists", "ann@example.com", "idea", "two")
    feedback.set_done(done["id"], True)
    assert admin.get("/api/me").json()["waiting_feedback"] == 1


# --- copying the Jobs app's old messages over, once ------------------------------------------------------------------------------


JOBS_ROWS = [
    {"id": 1, "kind": "idea", "body": "an idea", "page": "/jobs.html", "from_email": "Ann@Example.com", "created_at": "2026-11-20T10:00:00", "done_at": None},
    {"id": 2, "kind": "problem", "body": "a problem", "page": None, "from_email": "bob@example.com", "created_at": "2026-11-20T11:00:00", "done_at": "2026-11-21T09:00:00"},
    {"id": 3, "kind": "rant", "body": "not a kind", "from_email": "bob@example.com", "created_at": "2026-11-20T12:00:00"},
]


def test_the_jobs_messages_are_copied_once_with_their_sender_time_and_state():
    assert feedback.import_from_jobs(JOBS_ROWS) == 2  # the one with an unknown kind is left out
    got = {m["body"]: m for m in feedback.inbox()}
    assert got["an idea"]["app"] == "resume" and got["an idea"]["email"] == "ann@example.com" and got["an idea"]["done_at"] is None
    assert got["a problem"]["done_at"] is not None and got["a problem"]["created_at"] < got["a problem"]["done_at"]
    assert feedback.import_from_jobs(JOBS_ROWS) == 0  # never twice
    assert len(feedback.inbox()) == 2


def test_the_import_route_asks_the_jobs_app_as_the_administrator(admin, monkeypatch):  # noqa: F811
    calls = []

    class Reply:
        status_code = 200

        def json(self):
            return JOBS_ROWS

    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None, allow_redirects=True: calls.append((url, headers)) or Reply())
    assert admin.post("/api/feedback/import-jobs").json() == {"copied": 2}
    assert calls[0][0].endswith("/api/admin/messages") and "Cookie" in calls[0][1]
    assert admin.get("/api/feedback").json()["can_import_jobs"] is False


def test_a_jobs_app_that_does_not_answer_is_a_502_and_nothing_is_marked_copied(admin, monkeypatch):  # noqa: F811
    def down(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", down)
    assert admin.post("/api/feedback/import-jobs").status_code == 502
    assert admin.get("/api/feedback").json()["can_import_jobs"] is True
