"""Asking for access: a signed-in person with no account in an app can ask for it, an administrator approves or declines
on the People page. The safety rules checked here: the address is always the one in the person's own verified session,
nothing opens without an administrator, only an app that plainly says "no account" can be asked for, a decline is final
(they can't ask again), and the queue can't be flooded."""
import time

import jwt
import pytest
import requests
from shared_python import notify

from app import config, people
from tests.test_home import ISSUER, CLIENT, apps_say, client, id_token, key, locked_names, names, production  # noqa: F401 - fixtures
from tests.test_people import ADMIN, Apps, Reply, apps, mail, person  # noqa: F401 - fixtures

BOB = "bob@example.com"
NOW = 1_800_000_000  # a fixed clock for the digest, in seconds


@pytest.fixture(autouse=True)
def names_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PEOPLE_DB", str(tmp_path / "people.db"))


@pytest.fixture()
def pushes(monkeypatch):
    """The administrator's own channel (push / ntfy / email), recorded instead of sent."""
    sent = []
    monkeypatch.setattr(notify, "notify", lambda title, message, link=None: sent.append((title, message, link)))
    return sent


def bob(production, key):  # noqa: F811
    return person(production, key, BOB)


def states(apps_say, **by_app):  # noqa: F811
    """What each app says about Bob's account: 200 has one, 403 has none, anything else is 'can't tell'."""
    apps_say.status = {"lists": 403, "trips": 403, "time": 403, "plans": 403, "finances": 403, **by_app}


# --- the home page offers a request only where an app plainly says "no account" -------------------------------------------


def test_apps_that_say_no_account_are_offered_as_locked_tiles_and_the_ones_they_have_are_not(production, key, apps_say):  # noqa: F811
    bob(production, key)
    states(apps_say, lists=200)
    html = production.get("/").text
    assert names(html) == ["Lists"]
    assert locked_names(html) == ["Trips", "Time", "Plans", "Finances", "Jobs"]
    assert "Flights" not in html  # a private app is never even mentioned to someone who can't have it
    assert html.count("data-request-app=") == 5


@pytest.mark.parametrize("answer", [401, 302, 404, 500, requests.ConnectionError("down"), requests.Timeout("slow")])
def test_an_app_that_cannot_say_is_never_offered_as_a_request(production, key, apps_say, answer):  # noqa: F811
    bob(production, key)
    states(apps_say, trips=answer)
    html = production.get("/").text
    assert "Trips" not in locked_names(html) and "Trips" not in names(html)  # (a real problem is shown in red instead; see test_home)
    assert locked_names(html) == ["Lists", "Time", "Plans", "Finances", "Jobs"]


def test_the_administrator_is_never_offered_a_request(production, key, apps_say):  # noqa: F811
    person(production, key, ADMIN)
    assert locked_names(production.get("/").text) == [] and apps_say.calls == []


def test_a_waiting_request_and_a_declined_one_show_no_button(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    people.file_requests(BOB, ["lists", "trips"], "")
    people.settle_requests(BOB, ["trips"], decline=True)
    html = production.get("/").text
    assert "Requested. Waiting for an administrator." in html and "wasn't approved" in html
    assert html.count("data-request-app=") == 4  # not Lists, not Trips


def test_the_administrator_is_told_on_the_home_page_how_many_are_waiting(production, key, apps_say):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    assert "2 requests waiting" in production.get("/").text
    people.settle_requests(BOB, ["trips"])
    assert "1 request waiting" in production.get("/").text
    people.settle_requests(BOB, ["lists"], decline=True)  # turned down: no longer waiting, though it stays on file
    assert "waiting for you" not in production.get("/").text
    bob(production, key)
    assert "waiting for you" not in production.get("/").text  # only administrators are told


# --- asking ------------------------------------------------------------------------------------------------------------------


def test_asking_files_a_request_under_the_signed_in_address_and_sends_nothing_by_itself(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    resp = production.post("/api/requests", json={"apps": ["lists", "trips"], "note": "  I'm Ann's  brother "})
    assert resp.status_code == 200 and resp.json() == {"filed": ["lists", "trips"], "already": []}
    assert people.requests_for(BOB) == ["lists", "trips"]
    assert pushes == []  # the administrator hears in the next digest, not one message per request


def test_the_address_comes_from_the_session_never_from_what_was_sent(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"], "email": "ann@example.com", "user": "ann@example.com"})
    assert people.requests_for(BOB) == ["lists"] and people.requests_for("ann@example.com") == []


def test_asking_again_changes_nothing_and_is_not_announced_twice(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"], "note": "first"})
    again = production.post("/api/requests", json={"apps": ["lists", "trips"], "note": "second"}).json()
    assert again == {"filed": ["trips"], "already": ["lists"]}
    assert production.post("/api/requests", json={"apps": ["lists"]}).json() == {"filed": [], "already": ["lists"]}
    assert people.send_digest(NOW) == 1 and len(pushes) == 1
    assert "Lists, Trips" in pushes[0][1] and "first" in pushes[0][1] and "second" not in pushes[0][1]


def test_a_declined_request_cannot_be_filed_again(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"]})
    people.settle_requests(BOB, ["lists"], decline=True)
    got = production.post("/api/requests", json={"apps": ["lists"], "note": "please?"}).json()
    assert got == {"filed": [], "already": ["lists"]}
    assert people.requests_for(BOB) == []  # still declined, not waiting again
    people.send_digest(NOW)
    assert pushes == []  # nothing waiting, nothing to say


@pytest.mark.parametrize("answer", [200, 401, 500, requests.ConnectionError("x")])
def test_only_an_app_that_says_no_account_can_be_asked_for(production, key, apps_say, pushes, answer):  # noqa: F811
    bob(production, key)
    states(apps_say, lists=answer)
    resp = production.post("/api/requests", json={"apps": ["lists"]})
    assert resp.status_code == 400 and people.requests_for(BOB) == [] and pushes == []


@pytest.mark.parametrize("apps_asked", [["flights"], ["nonsense"], [], ["lists", "flights"]])
def test_other_apps_and_an_empty_ask_are_refused_and_nothing_is_filed(production, key, apps_say, pushes, apps_asked):  # noqa: F811
    bob(production, key)
    states(apps_say)
    assert production.post("/api/requests", json={"apps": apps_asked}).status_code == 400
    assert people.requests_for(BOB) == [] and pushes == []


def test_an_administrator_cannot_ask(production, key, apps_say, pushes):  # noqa: F811
    person(production, key, ADMIN)
    assert production.post("/api/requests", json={"apps": ["lists"]}).status_code == 400
    assert pushes == []


def test_a_signed_out_visitor_cannot_ask(production, pushes):
    assert production.post("/api/requests", json={"apps": ["lists"]}).status_code == 401
    assert people.waiting_count() == 0 and pushes == []


def test_an_unverified_email_cannot_ask(production, key, apps_say, pushes):  # noqa: F811
    token = jwt.encode({"iss": ISSUER, "aud": CLIENT, "exp": int(time.time()) + 300, "email": BOB, "email_verified": False}, key, algorithm="RS256")
    production.cookies.set("oidc_session", token)
    states(apps_say)
    assert production.post("/api/requests", json={"apps": ["lists"]}).status_code == 401
    assert people.waiting_count() == 0 and pushes == []


def test_a_cross_site_request_is_refused(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    resp = production.post("/api/requests", json={"apps": ["lists"]}, headers={"Sec-Fetch-Site": "same-site"})
    assert resp.status_code == 403 and people.waiting_count() == 0


def test_a_long_note_is_refused_not_quietly_cut(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    resp = production.post("/api/requests", json={"apps": ["lists"], "note": "x" * 301})
    assert resp.status_code == 400 and "at most 300" in resp.json()["detail"] and people.waiting_count() == 0


def test_the_note_is_plain_text_without_control_characters(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"], "note": "hi\x00\nthere <b>x</b>"})
    people.send_digest(NOW)
    assert pushes[0][1].endswith('"hi there <b>x</b>"')  # stored as text; the pages escape it when shown


def test_the_queue_has_a_limit_so_it_cannot_be_flooded(monkeypatch, production, key, apps_say, pushes):  # noqa: F811
    monkeypatch.setattr(people, "MAX_WAITING", 2)
    people.file_requests("a@example.com", ["lists", "trips"], "")
    bob(production, key)
    states(apps_say)
    resp = production.post("/api/requests", json={"apps": ["lists"]})
    assert resp.status_code == 429 and people.requests_for(BOB) == [] and pushes == []


def test_a_failure_to_tell_the_administrator_does_not_lose_the_request(monkeypatch, production, key, apps_say):  # noqa: F811
    def boom(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notify, "notify", boom)
    bob(production, key)
    states(apps_say)
    assert production.post("/api/requests", json={"apps": ["lists"]}).status_code == 200
    assert people.requests_for(BOB) == ["lists"]


# --- the administrator's side ----------------------------------------------------------------------------------------------


def test_only_the_administrator_sees_or_decides_requests(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists"], "hello")
    production.cookies.clear()
    assert production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]}).status_code in (401, 403)
    person(production, key, "eve@example.com")
    assert production.get("/api/people").status_code == 403
    assert production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]}).status_code == 403
    assert apps.calls == [] and mail == [] and people.requests_for(BOB) == ["lists"]


def test_the_grid_lists_waiting_requests_with_the_note_and_whether_they_were_removed_before(production, key, apps):  # noqa: F811
    apps.put("trips", BOB, status="removed")
    people.file_requests(BOB, ["lists", "trips"], "Ann's brother")
    people.file_requests("zoe@example.com", ["finances"], "")
    people.set_name(BOB, "Bob", "Hope")
    person(production, key, ADMIN)
    got = production.get("/api/people").json()["requests"]
    assert [r["email"] for r in got] == [BOB, "zoe@example.com"]
    first = got[0]
    assert (first["first_name"], first["last_name"], first["note"]) == ("Bob", "Hope", "Ann's brother")
    assert first["apps"] == [{"key": "lists", "name": "Lists", "previously": "none"}, {"key": "trips", "name": "Trips", "previously": "removed"}]


def test_a_declined_request_is_not_listed(production, key, apps):  # noqa: F811
    people.file_requests(BOB, ["lists"], "")
    people.settle_requests(BOB, ["lists"], decline=True)
    person(production, key, ADMIN)
    assert production.get("/api/people").json()["requests"] == []


def test_approving_some_lets_them_in_sends_one_email_and_leaves_the_rest_waiting(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips", "finances"], "")
    person(production, key, ADMIN)
    resp = production.post("/api/requests/decide", json={"email": "Bob@Example.com", "approve": ["lists", "finances"]})
    assert resp.status_code == 200 and resp.json() == {"added": ["Lists", "Finances"], "failed": [], "left_waiting": ["trips"]}
    assert [u["email"] for u in apps.users["lists"]] == [BOB] and [u["email"] for u in apps.users["finances"]] == [BOB]
    assert apps.users["trips"] == []
    assert all(c["params"] == {"notify": "false"} for c in apps.calls if c["method"] == "POST")
    assert len(mail) == 1 and mail[0][0] == BOB and "Lists and Finances" in mail[0][1]
    assert people.request_states(BOB) == {"trips": "pending"}  # not turned down: it is still on the list for later


def test_the_rest_can_be_approved_later_and_only_that_one_is_emailed(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]})
    assert production.get("/api/people").json()["requests"][0]["apps"] == [{"key": "trips", "name": "Trips", "previously": "none"}]
    later = production.post("/api/requests/decide", json={"email": BOB, "approve": ["trips"]}).json()
    assert later == {"added": ["Trips"], "failed": [], "left_waiting": []}
    assert len(mail) == 2 and "Trips" in mail[1][1] and "Lists" not in mail[1][1]
    assert people.request_states(BOB) == {} and production.get("/api/people").json()["requests"] == []


def test_the_person_still_sees_what_is_waiting_after_a_partial_approval(production, key, apps, apps_say, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]})
    states(apps_say, lists=200)
    html = bob(production, key).get("/").text
    assert names(html) == ["Lists"] and "Requested. Waiting for an administrator." in html  # Trips is still pending
    assert "wasn't approved" not in html


def test_approve_some_and_decline_others_in_one_go_and_the_third_stays_waiting(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips", "finances"], "")
    person(production, key, ADMIN)
    got = production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"], "decline": ["trips"]}).json()
    assert got["added"] == ["Lists"] and got["left_waiting"] == ["finances"]
    assert people.request_states(BOB) == {"trips": "declined", "finances": "pending"}


def test_approving_uses_the_administrators_own_session_like_ticking_a_box(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists"], "")
    person(production, key, ADMIN)
    production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]})
    cookie = f"oidc_session={production.cookies.get('oidc_session')}"
    assert apps.calls and all(c["headers"] == {"X-Brassworks-Probe": "1", "Cookie": cookie} and c["allow_redirects"] is False for c in apps.calls)


def test_declining_calls_no_app_sends_nothing_and_is_final_for_just_those_apps(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips", "finances"], "")
    person(production, key, ADMIN)
    got = production.post("/api/requests/decide", json={"email": BOB, "decline": ["lists", "trips"]}).json()
    assert got == {"added": [], "failed": [], "left_waiting": ["finances"]}
    assert apps.calls == [] and mail == []
    assert people.request_states(BOB) == {"lists": "declined", "trips": "declined", "finances": "pending"}


@pytest.mark.parametrize("body", [
    {"approve": [], "decline": []},                       # nothing chosen
    {"approve": ["lists"], "decline": ["lists"]},         # both at once
    {"decline": ["finances"]},                            # an app they never asked for
    {"approve": ["lists"], "decline": ["flights"]},       # not an app with people
])
def test_a_confused_decision_is_refused_and_changes_nothing(production, key, apps, mail, body):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    assert production.post("/api/requests/decide", json={"email": BOB, **body}).status_code == 400
    assert apps.calls == [] and mail == [] and people.request_states(BOB) == {"lists": "pending", "trips": "pending"}


def test_an_app_that_is_down_keeps_that_request_waiting_and_the_rest_go_through(production, key, apps, mail):  # noqa: F811
    apps.down.add("trips")
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    got = production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists", "trips"]}).json()
    assert got["added"] == ["Lists"] and got["failed"] == ["Trips didn't answer"]
    assert people.requests_for(BOB) == ["trips"]  # still waiting, not lost and not declined
    assert len(mail) == 1 and "Trips" not in mail[0][1]


def test_someone_already_let_in_meanwhile_is_not_added_or_emailed_twice(production, key, apps, mail):  # noqa: F811
    apps.put("lists", BOB)
    people.file_requests(BOB, ["lists"], "")
    person(production, key, ADMIN)
    assert production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]}).json() == {"added": [], "failed": [], "left_waiting": []}
    assert mail == [] and people.requests_for(BOB) == []
    assert [c for c in apps.calls if c["method"] == "POST"] == []


def test_approving_an_app_they_did_not_ask_for_is_refused_and_changes_nothing(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists"], "")
    person(production, key, ADMIN)
    resp = production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists", "finances"]})
    assert resp.status_code == 400
    assert [c for c in apps.calls if c["method"] != "GET"] == [] and mail == [] and people.requests_for(BOB) == ["lists"]


@pytest.mark.parametrize("who,approve", [("nobody@example.com", ["lists"]), (BOB, ["flights"])])
def test_deciding_for_nobody_waiting_or_a_non_app_is_an_error(production, key, apps, mail, who, approve):  # noqa: F811
    people.file_requests(BOB, ["lists"], "")
    person(production, key, ADMIN)
    assert production.post("/api/requests/decide", json={"email": who, "approve": approve}).status_code in (400, 404)
    assert [c for c in apps.calls if c["method"] != "GET"] == [] and mail == []


def test_a_declined_person_who_is_later_added_by_hand_gets_in_and_the_old_decline_goes(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists"], "")
    people.settle_requests(BOB, ["lists"], decline=True)
    person(production, key, ADMIN)
    production.put("/api/people/access", json={"email": BOB, "app": "lists", "enabled": True})
    assert people.request_states(BOB) == {} and [u["email"] for u in apps.users["lists"]] == [BOB]


def test_adding_someone_on_the_people_page_finishes_their_waiting_requests(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    production.post("/api/people", json={"email": BOB, "apps": ["lists"]})
    assert people.request_states(BOB) == {"trips": "pending"}  # Lists is finished; Trips is still waiting


def test_a_failed_add_leaves_the_request_waiting(production, key, apps, mail):  # noqa: F811
    apps.down.add("lists")
    people.file_requests(BOB, ["lists"], "")
    person(production, key, ADMIN)
    production.post("/api/people", json={"email": BOB, "apps": ["lists"]})
    assert people.requests_for(BOB) == ["lists"]


def test_the_apps_in_a_request_come_in_the_usual_order_not_alphabetically(production, key, apps):  # noqa: F811
    people.file_requests(BOB, ["finances", "plans", "lists"], "")
    person(production, key, ADMIN)
    got = production.get("/api/people").json()["requests"][0]["apps"]
    assert [a["key"] for a in got] == ["lists", "plans", "finances"]


# --- the digest: one message for everything asked since the last one --------------------------------------------------------


def file(email, apps, note=""):
    people.file_requests(email, apps, note)


def test_everything_asked_since_the_last_digest_is_one_message(pushes):
    file("ann@example.com", ["lists", "trips", "time"], "I know Evan")
    file(BOB, ["lists"])
    file("cy@example.com", ["finances", "plans"])
    assert people.send_digest(NOW) == 3
    assert len(pushes) == 1
    title, message, link = pushes[0]
    assert title == "Brassworks: 3 people asked for access"
    assert message.splitlines() == [
        'ann@example.com: Lists, Trips, Time - "I know Evan"', f"{BOB}: Lists", "cy@example.com: Plans, Finances"]
    assert link.endswith("users.html")


def test_one_person_is_worded_in_the_singular_and_names_are_shown(pushes):
    people.set_name("ann@example.com", "Ann", "Lee")
    file("ann@example.com", ["lists"])
    people.send_digest(NOW)
    assert pushes[0][0] == "Brassworks: 1 person asked for access"
    assert pushes[0][1] == "Ann Lee (ann@example.com): Lists"


def test_nothing_new_means_no_message(pushes):
    assert people.send_digest(NOW) == 0
    assert pushes == []


def test_a_request_is_announced_once_and_a_later_one_goes_in_the_next_digest(pushes):
    file("ann@example.com", ["lists"])
    people.send_digest(NOW)
    file(BOB, ["trips"])
    assert people.send_digest(NOW + 60) == 0  # too soon: waits for the interval
    assert len(pushes) == 1
    assert people.send_digest(NOW + config.REQUEST_DIGEST_MINUTES * 60) == 1
    assert len(pushes) == 2 and BOB in pushes[1][1] and "ann@example.com" not in pushes[1][1]
    assert people.send_digest(NOW + 10_000) == 0 and len(pushes) == 2  # both are announced now


def test_the_interval_is_remembered_in_the_database_so_a_restart_cannot_send_early(pushes):
    file("ann@example.com", ["lists"])
    people.send_digest(NOW)
    file(BOB, ["lists"])
    # a restart is a fresh process: nothing in memory, only the file
    assert people.send_digest(NOW + 30) == 0 and pushes and len(pushes) == 1
    assert people.waiting_count() == 2  # and the People page counts both regardless


def test_one_decided_before_the_digest_is_left_out(pushes):
    file("ann@example.com", ["lists", "trips"])
    people.settle_requests("ann@example.com", ["lists"])  # approved
    file(BOB, ["lists"])
    people.settle_requests(BOB, ["lists"], decline=True)
    assert people.send_digest(NOW) == 1
    assert pushes[0][1] == "ann@example.com: Trips"


def test_a_long_queue_is_cut_to_twenty_people_and_says_how_many_more(pushes):
    for number in range(25):
        file(f"p{number:02d}@example.com", ["lists"])
    assert people.send_digest(NOW) == 25
    lines = pushes[0][1].splitlines()
    assert pushes[0][0] == "Brassworks: 25 people asked for access"
    assert len(lines) == 21 and lines[-1] == "...and 5 more"


def test_requests_that_were_already_announced_one_by_one_are_not_announced_again(pushes, tmp_path):
    """A people.db made before the digest existed (no announced_at column): its requests were already told of."""
    import sqlite3

    connection = sqlite3.connect(config.PEOPLE_DB)
    connection.execute(
        "CREATE TABLE access_requests (email TEXT NOT NULL, app TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL, PRIMARY KEY (email, app))")
    connection.execute("INSERT INTO access_requests VALUES ('old@example.com', 'lists', '', 'pending', 100)")
    connection.commit()
    connection.close()
    assert people.send_digest(NOW) == 0 and pushes == []
    assert people.waiting_count() == 1  # still waiting for a decision, just not announced again
    file(BOB, ["lists"])
    assert people.send_digest(NOW + 10_000) == 1 and BOB in pushes[0][1] and "old@example.com" not in pushes[0][1]


def test_a_failing_channel_does_not_break_the_digest(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notify, "notify", boom)
    file("ann@example.com", ["lists"])
    assert people.send_digest(NOW) == 1  # logged, not raised; the People page still lists them
    assert people.waiting_count() == 1


def test_a_note_cannot_break_out_of_its_line(pushes):
    file("ann@example.com", ["lists"], "line one\nline two <script>")
    people.send_digest(NOW)
    assert len(pushes[0][1].splitlines()) == 1  # the note is cleaned to one line when filed


def test_the_digest_runs_from_start_up_and_stops_at_shut_down(monkeypatch, pushes):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.main.DIGEST_CHECK_SECONDS", 0.05)
    file("ann@example.com", ["lists"])
    with TestClient(app):
        deadline = time.time() + 3
        while not pushes and time.time() < deadline:
            time.sleep(0.05)
    assert len(pushes) == 1


def _remembered(email):
    from app import access

    access._apps_cache[email] = (time.time(), [])  # what the menu's list would hold for them
    return access._apps_cache


def test_the_menus_apps_for_a_person_are_forgotten_the_moment_the_administrator_changes_their_access(production, key, apps, mail):  # noqa: F811
    cache = _remembered(BOB)
    cache["someone.else@example.com"] = (time.time(), [])
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    assert production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]}).status_code == 200  # approving a request
    assert BOB not in cache and "someone.else@example.com" in cache  # theirs only

    _remembered(BOB)
    assert production.put("/api/people/access", json={"email": BOB, "app": "finances", "enabled": True}).status_code == 200  # ticking
    assert BOB not in cache
    _remembered(BOB)
    assert production.put("/api/people/access", json={"email": BOB, "app": "finances", "enabled": False}).status_code == 200  # unticking
    assert BOB not in cache
    _remembered(BOB)
    assert production.post("/api/people", json={"email": BOB, "apps": ["time"]}).status_code == 200  # adding them to apps
    assert BOB not in cache


def test_the_menus_apps_are_forgotten_even_when_an_app_would_not_answer(production, key, apps, mail):  # noqa: F811
    cache = _remembered(BOB)
    apps.down.add("lists")
    person(production, key, ADMIN)
    assert production.put("/api/people/access", json={"email": BOB, "app": "lists", "enabled": True}).status_code == 502
    assert BOB not in cache


def test_an_app_can_tell_the_dashboard_that_someones_access_changed(client):
    from app import access

    cache = _remembered(BOB)
    cache["someone.else@example.com"] = (time.time(), [])
    assert client.post("/internal/access-changed", json={"email": "Bob@Example.com"}).status_code == 204
    assert BOB not in cache and "someone.else@example.com" in cache  # theirs only, whatever the capitalisation


def test_that_route_refuses_anything_that_came_through_the_internet(client):
    cache = _remembered(BOB)
    for header in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For"):
        resp = client.post("/internal/access-changed", json={"email": BOB}, headers={header: "203.0.113.5"})
        assert resp.status_code == 404
    assert BOB in cache  # untouched


def test_the_administrator_can_clear_everyones_remembered_apps_by_hand(production, key):  # noqa: F811
    cache = _remembered(BOB)
    cache["someone.else@example.com"] = (time.time(), [])
    person(production, key, ADMIN)
    assert production.post("/api/apps/forget", json={"email": "Bob@Example.com"}).json() == {"cleared": 1}  # one person's
    assert BOB not in cache and "someone.else@example.com" in cache
    assert production.post("/api/apps/forget", json={}).json() == {"cleared": 1}  # everyone's
    assert cache == {}


def test_clearing_the_remembered_apps_is_for_administrators_only(production, key):  # noqa: F811
    cache = _remembered(BOB)
    assert production.post("/api/apps/forget", json={}).status_code in (401, 403)  # signed out
    person(production, key, BOB)
    assert production.post("/api/apps/forget", json={}).status_code == 403  # an ordinary person
    assert BOB in cache
