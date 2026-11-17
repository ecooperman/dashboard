"""The central Users page: the administrator adds people to several apps at once, ticks who can use what, and
gives people names. Each app stays the one that says who may use it; the dashboard only drives the apps' own
administrator routes AS the signed-in administrator, with their own cookie, so nobody else can use any of it."""
import pytest
import requests
from fastapi.testclient import TestClient
from shared_python import notify

from app import config, people
from app.main import app
from tests.test_home import apps_say, id_token, key, production  # noqa: F401 - fixtures

ADMIN = "admin@example.com"
PORTS = {":8030": "lists", ":8060": "trips", ":8010": "time", ":8020": "plans", ":8080": "finances"}


class Apps:
    """Stands in for the five apps' administrator Users routes on this machine; records every call."""

    def __init__(self):
        self.users = {k: [] for k in PORTS.values()}  # app -> [{"id", "email", "status", "is_admin"}]
        self.down = set()  # apps that do not answer
        self.refuse = {}  # app -> status code to answer with instead
        self.calls = []
        self.next_id = 100

    def put(self, app_key, email, status="active", is_admin=False):
        self.next_id += 1
        self.users[app_key].append({"id": self.next_id, "email": email, "status": status, "is_admin": is_admin})

    def request(self, method, url, headers=None, timeout=None, allow_redirects=True, params=None, json=None):
        key = next(v for p, v in PORTS.items() if p in url)
        path = "/" + url.split("/", 3)[3]  # everything after http://127.0.0.1:PORT
        self.calls.append({"app": key, "method": method, "path": path, "headers": headers, "params": params, "json": json,
                           "timeout": timeout, "allow_redirects": allow_redirects})
        if key in self.down:
            raise requests.ConnectionError("down")
        if key in self.refuse:
            return Reply(self.refuse[key], {"detail": "no"})
        rows = self.users[key]
        base = config.APP_USERS[key]["route"]
        if method == "GET" and path == base:
            return Reply(200, [dict(u) for u in rows])
        if method == "POST" and path == base:
            existing = next((u for u in rows if u["email"] == json["email"]), None)
            if existing and existing["status"] == "active" and config.APP_USERS[key]["revoke"] == "action":
                return Reply(409, {"detail": f"{json['email']} already has access."})  # lists and trips refuse; the others say fine
            if existing:
                existing["status"] = "active"
            else:
                self.put(key, json["email"])
            return Reply(200, {})
        tail = path[len(base) + 1:]
        if method == "POST" and tail.endswith("/revoke"):
            next(u for u in rows if u["id"] == int(tail.split("/")[0]))["status"] = "removed"
            return Reply(200, {})
        if method == "PATCH":
            next(u for u in rows if u["id"] == int(tail))["status"] = json["status"]
            return Reply(200, {})
        raise AssertionError(f"unexpected call {method} {url}")


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture()
def apps(monkeypatch):
    fake = Apps()
    monkeypatch.setattr(requests, "request", fake.request)
    return fake


@pytest.fixture(autouse=True)
def names_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PEOPLE_DB", str(tmp_path / "people.db"))


@pytest.fixture()
def mail(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body, **kw: sent.append((to, subject, body)) or True)
    return sent


@pytest.fixture()
def admin(production, key):  # noqa: F811
    production.cookies.set("oidc_session", id_token(key, ADMIN))
    return production


def person(production, key, email):  # noqa: F811
    production.cookies.set("oidc_session", id_token(key, email))
    return production


# --- who may use any of it --------------------------------------------------------------------------------------------


CALLS = [
    ("GET", "/api/people", None),
    ("POST", "/api/people", {"email": "x@example.com", "apps": ["lists"]}),
    ("PUT", "/api/people/access", {"email": "x@example.com", "app": "lists", "enabled": True}),
    ("PUT", "/api/people/name", {"email": "x@example.com", "first_name": "X"}),
]


@pytest.mark.parametrize("method,path,body", CALLS)
def test_an_ordinary_person_gets_nothing_and_no_app_is_called(production, key, apps, mail, method, path, body):  # noqa: F811
    person(production, key, "bob@example.com")
    resp = production.request(method, path, json=body)
    assert resp.status_code == 403
    assert apps.calls == [] and mail == []


@pytest.mark.parametrize("method,path,body", CALLS)
def test_a_signed_out_visitor_gets_nothing(production, apps, method, path, body):
    assert production.request(method, path, json=body).status_code in (401, 403)
    assert apps.calls == []


def test_the_page_is_for_administrators_and_everyone_else_goes_home(production, key):  # noqa: F811
    assert production.get("/users.html").status_code == 302
    person(production, key, "bob@example.com")
    resp = production.get("/users.html")
    assert resp.status_code == 302 and resp.headers["location"] == "/"
    person(production, key, ADMIN)
    page = production.get("/users.html")
    assert page.status_code == 200 and "Add someone" in page.text


def test_the_home_page_links_to_it_only_for_administrators(production, key, apps_say):  # noqa: F811
    person(production, key, "bob@example.com")
    assert "/users.html" not in production.get("/").text
    person(production, key, ADMIN)
    assert "/users.html" in production.get("/").text


# --- the grid ----------------------------------------------------------------------------------------------------------


def test_the_grid_merges_every_app_and_says_who_may_use_what(admin, apps):
    apps.put("lists", ADMIN, is_admin=True)
    apps.put("lists", "ann@example.com")
    apps.put("trips", "ann@example.com", status="removed")
    apps.put("finances", "bob@example.com")
    apps.put("lists", "legacy-owner@lists.invalid")  # a placeholder is never a person
    got = admin.get("/api/people").json()
    assert [a["key"] for a in got["apps"]] == ["lists", "trips", "time", "plans", "finances"]
    rows = {p["email"]: p["access"] for p in got["people"]}
    assert set(rows) == {ADMIN, "ann@example.com", "bob@example.com"}
    assert rows["ann@example.com"] == {"lists": "active", "trips": "removed", "time": "none", "plans": "none", "finances": "none"}
    assert rows[ADMIN]["lists"] == "admin" and rows["bob@example.com"]["finances"] == "active"
    assert got["problems"] == {}


def test_an_app_that_does_not_answer_is_reported_and_never_guessed(admin, apps):
    apps.put("lists", "ann@example.com")
    apps.put("trips", "ann@example.com")
    apps.down.add("trips")
    got = admin.get("/api/people").json()
    assert "Trips didn't answer" in got["problems"]["trips"]
    ann = got["people"][0]
    assert ann["access"]["trips"] == "unknown" and ann["access"]["lists"] == "active"


def test_an_app_that_says_you_are_not_its_administrator_is_a_problem_not_a_crash(admin, apps):
    apps.refuse["time"] = 403
    assert "aren't an administrator" in admin.get("/api/people").json()["problems"]["time"]


def test_names_show_up_and_people_are_ordered_by_last_name(admin, apps):
    apps.put("lists", "zed@example.com")
    apps.put("lists", "amy@example.com")
    people.set_name("zed@example.com", "Zed", "Adams")
    people.set_name("amy@example.com", "Amy", "Young")
    got = admin.get("/api/people").json()["people"]
    assert [(p["email"], p["first_name"], p["last_name"]) for p in got] == [
        ("zed@example.com", "Zed", "Adams"), ("amy@example.com", "Amy", "Young")]


# --- everything goes through the administrator's own session ---------------------------------------------------------


def test_the_apps_are_called_on_this_machine_with_only_the_administrators_cookie_and_no_redirects(admin, key, apps, mail):  # noqa: F811
    admin.get("/api/people")
    admin.post("/api/people", json={"email": "new@example.com", "apps": ["lists", "finances"]})
    assert apps.calls
    cookie = f"oidc_session={id_token(key, ADMIN)}"
    for call in apps.calls:
        assert call["headers"] == {"Cookie": cookie}
        assert call["allow_redirects"] is False
    # only the fixed localhost addresses: nothing the request said decides where they go
    assert {c["app"] for c in apps.calls} == {"lists", "trips", "time", "plans", "finances"}


def test_an_app_that_bounces_to_a_sign_in_is_a_problem_and_nothing_is_followed(admin, apps):
    apps.refuse["plans"] = 302
    assert "didn't accept your sign-in" in admin.get("/api/people").json()["problems"]["plans"]


# --- adding someone ----------------------------------------------------------------------------------------------------


def test_adding_someone_to_ticked_apps_sends_one_email_and_saves_the_name(admin, apps, mail):
    resp = admin.post("/api/people", json={
        "email": "  New@Example.com ", "first_name": " Nina ", "last_name": "North", "apps": ["lists", "trips", "finances"]})
    assert resp.status_code == 200
    assert resp.json() == {"email": "new@example.com", "added": ["Lists", "Trips", "Finances"], "already": [], "failed": []}
    assert [u["email"] for u in apps.users["lists"]] == ["new@example.com"] and apps.users["time"] == []
    posts = [c for c in apps.calls if c["method"] == "POST"]
    assert all(c["params"] == {"notify": "false"} for c in posts)  # the apps stay quiet: one email below, not one per app
    assert len(mail) == 1 and mail[0][0] == "new@example.com"
    assert "Lists, Trips and Finances" in mail[0][1] and ADMIN in mail[0][2]
    assert people.get_name("new@example.com") == ("Nina", "North")


def test_someone_who_already_has_an_app_is_not_told_again_and_a_removed_person_is_restored(admin, apps, mail):
    apps.put("lists", "ann@example.com")
    apps.put("trips", "ann@example.com", status="removed")
    got = admin.post("/api/people", json={"email": "ann@example.com", "apps": ["lists", "trips"]}).json()
    assert got["already"] == ["Lists"] and got["added"] == ["Trips"]
    assert apps.users["trips"][0]["status"] == "active"
    assert len(mail) == 1 and "Trips" in mail[0][1] and "Lists" not in mail[0][1]


def test_one_app_failing_does_not_stop_the_others_or_hide_the_failure(admin, apps, mail):
    apps.down.add("trips")
    got = admin.post("/api/people", json={"email": "new@example.com", "apps": ["lists", "trips"]}).json()
    assert got["added"] == ["Lists"] and got["failed"] == ["Trips didn't answer"]
    assert "Lists" in mail[0][1] and "Trips" not in mail[0][1]  # told only about what really happened


@pytest.mark.parametrize("body,fragment", [
    ({"email": "not-an-email", "apps": ["lists"]}, "email"),
    ({"email": "legacy-owner@lists.invalid", "apps": ["lists"]}, "email"),
    ({"email": "a@example.com", "apps": []}, "Tick at least one"),
    ({"email": "a@example.com", "apps": ["flights"]}, "isn't an app"),
    ({"email": "a@example.com", "apps": ["lists"], "first_name": "x" * 61}, "at most 60"),
])
def test_a_bad_request_changes_nothing(admin, apps, mail, body, fragment):
    resp = admin.post("/api/people", json=body)
    assert resp.status_code == 400 and fragment in resp.json()["detail"]
    assert [c for c in apps.calls if c["method"] != "GET"] == [] and mail == []


def test_leaving_the_name_empty_keeps_the_one_already_there(admin, apps, mail):
    people.set_name("ann@example.com", "Ann", "Lee")
    admin.post("/api/people", json={"email": "ann@example.com", "apps": ["lists"]})
    assert people.get_name("ann@example.com") == ("Ann", "Lee")


# --- ticking and unticking ----------------------------------------------------------------------------------------------


def test_ticking_a_box_lets_them_in_and_tells_them(admin, apps, mail):
    apps.put("lists", "ann@example.com")
    resp = admin.put("/api/people/access", json={"email": "ann@example.com", "app": "finances", "enabled": True})
    assert resp.json() == {"result": "added"}
    assert apps.users["finances"][0]["email"] == "ann@example.com"
    assert len(mail) == 1 and "Finances" in mail[0][1]


@pytest.mark.parametrize("app_key,method,suffix", [
    ("lists", "POST", "/revoke"), ("trips", "POST", "/revoke"),
    ("time", "PATCH", ""), ("plans", "PATCH", ""), ("finances", "PATCH", ""),
])
def test_unticking_removes_access_the_way_each_app_does_it(admin, apps, mail, app_key, method, suffix):
    apps.put(app_key, "ann@example.com")
    got = admin.put("/api/people/access", json={"email": "Ann@Example.com", "app": app_key, "enabled": False})
    assert got.json() == {"result": "removed"} and apps.users[app_key][0]["status"] == "removed"
    change = [c for c in apps.calls if c["method"] != "GET"][0]
    assert change["method"] == method and change["path"].endswith(f"/{apps.users[app_key][0]['id']}{suffix}")
    assert mail == []  # taking access away says nothing to them


def test_an_administrators_access_cannot_be_unticked(admin, apps):
    apps.put("lists", "second@example.com", is_admin=True)
    resp = admin.put("/api/people/access", json={"email": "second@example.com", "app": "lists", "enabled": False})
    assert resp.status_code == 502 and "administrator" in resp.json()["detail"]
    assert apps.users["lists"][0]["status"] == "active"


def test_unticking_someone_the_app_does_not_know_is_a_quiet_no_op(admin, apps):
    assert admin.put("/api/people/access", json={"email": "ghost@example.com", "app": "lists", "enabled": False}).json() == {"result": "none"}


def test_an_app_failing_is_reported_as_an_error_and_the_box_is_not_assumed_changed(admin, apps, mail):
    apps.down.add("lists")
    resp = admin.put("/api/people/access", json={"email": "ann@example.com", "app": "lists", "enabled": True})
    assert resp.status_code == 502 and "Lists didn't answer" in resp.json()["detail"] and mail == []


def test_only_the_five_apps_can_be_named_flights_has_no_people(admin, apps):
    assert admin.put("/api/people/access", json={"email": "a@example.com", "app": "flights", "enabled": True}).status_code == 400


# --- names --------------------------------------------------------------------------------------------------------------


def test_the_administrator_can_set_and_clear_anyones_name(admin):
    assert admin.put("/api/people/name", json={"email": "Ann@Example.com", "first_name": "  Ann  ", "last_name": "Lee"}).json() == {
        "first_name": "Ann", "last_name": "Lee"}
    assert people.get_name("ann@example.com") == ("Ann", "Lee")
    admin.put("/api/people/name", json={"email": "ann@example.com", "first_name": "", "last_name": ""})
    assert people.get_name("ann@example.com") == ("", "")


def test_a_name_has_no_control_characters_or_stray_spaces(admin):
    got = admin.put("/api/people/name", json={"email": "a@example.com", "first_name": "A\x00n\nn   a", "last_name": "L"}).json()
    assert got["first_name"] == "A n n a"


def test_anyone_signed_in_sets_only_their_own_name_through_the_profile(production, key):  # noqa: F811
    person(production, key, "bob@example.com")
    assert production.get("/api/profile").json() == {"first_name": "", "last_name": ""}
    assert production.put("/api/profile", json={"first_name": "Bob", "last_name": "Hope", "email": "ann@example.com"}).status_code == 200
    assert people.get_name("bob@example.com") == ("Bob", "Hope")
    assert people.get_name("ann@example.com") == ("", "")  # an email in the body is ignored: it is always their own
    assert production.get("/api/profile").json() == {"first_name": "Bob", "last_name": "Hope"}


def test_the_profile_needs_a_sign_in(production):
    assert production.get("/api/profile").status_code == 401
    assert production.put("/api/profile", json={"first_name": "X"}).status_code == 401


def test_a_cross_site_change_is_refused(admin, apps):
    resp = admin.put("/api/people/name", json={"email": "a@example.com", "first_name": "X"}, headers={"Sec-Fetch-Site": "same-site"})
    assert resp.status_code == 403 and people.get_name("a@example.com") == ("", "")


@pytest.mark.parametrize("app_key", ["lists", "time"])
def test_someone_who_already_has_an_app_is_never_added_or_emailed_again_whatever_the_app_answers(admin, apps, mail, app_key):
    apps.put(app_key, "ann@example.com")
    got = admin.put("/api/people/access", json={"email": "ann@example.com", "app": app_key, "enabled": True}).json()
    assert got == {"result": "already"} and mail == []
    assert [c for c in apps.calls if c["method"] == "POST"] == []
