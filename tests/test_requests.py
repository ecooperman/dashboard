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
from tests.test_home import ISSUER, CLIENT, apps_say, id_token, key, locked_names, names, production  # noqa: F401 - fixtures
from tests.test_people import ADMIN, Apps, Reply, apps, mail, person  # noqa: F401 - fixtures

BOB = "bob@example.com"


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
    assert locked_names(html) == ["Trips", "Time", "Plans", "Finances"]
    assert "Flights" not in html  # a private app is never even mentioned to someone who can't have it
    assert html.count("data-request-app=") == 4


@pytest.mark.parametrize("answer", [401, 302, 404, 500, requests.ConnectionError("down"), requests.Timeout("slow")])
def test_an_app_that_cannot_say_is_left_off_rather_than_offered(production, key, apps_say, answer):  # noqa: F811
    bob(production, key)
    states(apps_say, trips=answer)
    html = production.get("/").text
    assert "Trips" not in locked_names(html) and "Trips" not in names(html)
    assert locked_names(html) == ["Lists", "Time", "Plans", "Finances"]


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
    assert html.count("data-request-app=") == 3  # not Lists, not Trips


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


def test_asking_files_a_request_under_the_signed_in_address_and_tells_the_administrator_once(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    resp = production.post("/api/requests", json={"apps": ["lists", "trips"], "note": "  I'm Ann's  brother "})
    assert resp.status_code == 200 and resp.json() == {"filed": ["lists", "trips"], "already": []}
    assert people.requests_for(BOB) == ["lists", "trips"]
    assert len(pushes) == 1
    title, message, link = pushes[0]
    assert BOB in message and "Lists, Trips" in message and "I'm Ann's brother" in message
    assert link.endswith("users.html")


def test_the_address_comes_from_the_session_never_from_what_was_sent(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"], "email": "ann@example.com", "user": "ann@example.com"})
    assert people.requests_for(BOB) == ["lists"] and people.requests_for("ann@example.com") == []


def test_asking_again_changes_nothing_and_does_not_tell_the_administrator_again(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"], "note": "first"})
    again = production.post("/api/requests", json={"apps": ["lists", "trips"], "note": "second"}).json()
    assert again == {"filed": ["trips"], "already": ["lists"]}
    assert len(pushes) == 2 and "Trips" in pushes[1][1] and "Lists" not in pushes[1][1]
    assert production.post("/api/requests", json={"apps": ["lists"]}).json() == {"filed": [], "already": ["lists"]}
    assert len(pushes) == 2


def test_a_declined_request_cannot_be_filed_again(production, key, apps_say, pushes):  # noqa: F811
    bob(production, key)
    states(apps_say)
    production.post("/api/requests", json={"apps": ["lists"]})
    people.settle_requests(BOB, ["lists"], decline=True)
    got = production.post("/api/requests", json={"apps": ["lists"], "note": "please?"}).json()
    assert got == {"filed": [], "already": ["lists"]}
    assert people.requests_for(BOB) == []  # still declined, not waiting again
    assert len(pushes) == 1


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
    assert pushes[0][1].endswith('They wrote: "hi there <b>x</b>"')  # stored as text; the pages escape it when shown


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


def test_approving_lets_them_into_what_was_ticked_sends_one_email_and_turns_down_the_rest(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips", "finances"], "")
    person(production, key, ADMIN)
    resp = production.post("/api/requests/decide", json={"email": "Bob@Example.com", "approve": ["lists", "finances"]})
    assert resp.status_code == 200 and resp.json() == {"added": ["Lists", "Finances"], "failed": []}
    assert [u["email"] for u in apps.users["lists"]] == [BOB] and [u["email"] for u in apps.users["finances"]] == [BOB]
    assert apps.users["trips"] == []
    assert all(c["params"] == {"notify": "false"} for c in apps.calls if c["method"] == "POST")
    assert len(mail) == 1 and mail[0][0] == BOB and "Lists and Finances" in mail[0][1]
    assert people.requests_for(BOB) == []
    states_now = people.request_states(BOB)
    assert states_now == {"trips": "declined"}  # approved ones are gone, the left-out one is declined for good


def test_approving_uses_the_administrators_own_session_like_ticking_a_box(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists"], "")
    person(production, key, ADMIN)
    production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]})
    cookie = f"oidc_session={production.cookies.get('oidc_session')}"
    assert apps.calls and all(c["headers"] == {"Cookie": cookie} and c["allow_redirects"] is False for c in apps.calls)


def test_declining_everything_calls_no_app_and_sends_nothing(production, key, apps, mail):  # noqa: F811
    people.file_requests(BOB, ["lists", "trips"], "")
    person(production, key, ADMIN)
    assert production.post("/api/requests/decide", json={"email": BOB, "approve": []}).json() == {"added": [], "failed": []}
    assert apps.calls == [] and mail == []
    assert people.request_states(BOB) == {"lists": "declined", "trips": "declined"}


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
    assert production.post("/api/requests/decide", json={"email": BOB, "approve": ["lists"]}).json() == {"added": [], "failed": []}
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
