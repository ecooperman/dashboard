"""The stats pages: administrators only, each app asked AS the administrator (their own cookie, no redirects), the answers put
together without guessing at an app that did not answer, and administrators left out of the totals unless asked."""
from datetime import datetime, timedelta

import pytest
import requests

from app import config, people, stats
from tests.test_home import key, production  # noqa: F401 - fixtures
from tests.test_people import ADMIN, PORTS, Reply, admin, person as sign_in_as  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 21, 12, 0, 0)


def ago(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def account(email, last=None, status="active", days=0, **counts):
    return {"email": email, "status": status, "created_at": ago(100), "last_seen_at": last, "active_days": days, "counts": counts, "last_activity_at": last}


class StatsApps:
    """Stands in for every app's /api/admin/stats on this machine; records each call."""

    def __init__(self):
        self.answers = {key: {"accounts": []} for key in PORTS.values()}
        self.down, self.status, self.calls = set(), {}, []

    def request(self, method, url, headers=None, timeout=None, allow_redirects=True, **kw):
        key = next(v for p, v in PORTS.items() if p in url)
        self.calls.append({"app": key, "method": method, "url": url, "headers": headers, "allow_redirects": allow_redirects})
        assert url.endswith("/api/admin/stats"), url
        if key in self.down:
            raise requests.ConnectionError("down")
        return Reply(self.status.get(key, 200), self.answers[key])


@pytest.fixture()
def apps(monkeypatch):
    fake = StatsApps()
    monkeypatch.setattr(requests, "request", fake.request)
    return fake


def fill(apps):
    apps.answers["lists"]["accounts"] = [
        account("owner@example.com", ago(0, 1), days=40, lists=9),  # an administrator
        account("bob@example.com", ago(2), days=5, lists=2),
        account("carol@example.com", ago(20), days=2, lists=1),
        account("dana@example.com", None, lists=0),  # has an account, never used it
        account("gone@example.com", ago(1), status="removed", lists=7),  # removed: not counted
    ]
    apps.answers["trips"]["accounts"] = [account("bob@example.com", ago(1), days=3, trips=4), account("owner@example.com", ago(0), days=9, trips=6)]


@pytest.fixture(autouse=True)
def an_owner(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {ADMIN, "owner@example.com"})


# --- who may see any of it -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/stats", "/api/stats/person?email=bob@example.com"])
def test_an_ordinary_person_gets_nothing_and_no_app_is_called(production, key, apps, path):  # noqa: F811
    sign_in_as(production, key, "bob@example.com")
    assert production.get(path).status_code == 403
    assert apps.calls == []


@pytest.mark.parametrize("path", ["/api/stats", "/api/stats/person?email=bob@example.com"])
def test_a_signed_out_visitor_gets_nothing(production, apps, path):
    assert production.get(path).status_code in (401, 403)
    assert apps.calls == []


@pytest.mark.parametrize("path", ["/stats.html", "/person.html"])
def test_the_pages_are_for_administrators_and_everyone_else_goes_home(production, key, path):  # noqa: F811
    assert production.get(path).status_code == 302
    sign_in_as(production, key, "bob@example.com")
    resp = production.get(path)
    assert resp.status_code == 302 and resp.headers["location"] == "/"


def test_an_administrator_gets_the_pages(admin):
    assert admin.get("/stats.html").status_code == 200 and "Usage" in admin.get("/stats.html").text
    assert admin.get("/person.html").status_code == 200


# --- asking the apps -------------------------------------------------------------------------------------------------------


def test_every_app_is_asked_as_the_administrator_with_their_own_cookie_and_no_redirects_followed(admin, apps):
    assert admin.get("/api/stats").status_code == 200
    assert sorted(c["app"] for c in apps.calls) == sorted(PORTS.values())
    for call in apps.calls:
        assert call["method"] == "GET" and call["url"].startswith("http://127.0.0.1:") and call["allow_redirects"] is False
        assert call["headers"]["Cookie"].startswith("oidc_session=")


# --- putting the answers together -------------------------------------------------------------------------------------------


def test_the_overview_counts_active_people_by_app_and_leaves_administrators_and_removed_people_out(apps):
    fill(apps)
    got = stats.overview(("c", "v"), now=NOW)
    lists = next(a for a in got["apps"] if a["key"] == "lists")
    assert (lists["accounts"], lists["active_7d"], lists["active_30d"], lists["never_used"]) == (3, 1, 2, 1)  # bob, carol, dana; not the owner or the removed
    assert lists["totals"] == {"lists": 3} and lists["last_activity_at"] == ago(2)
    trips = next(a for a in got["apps"] if a["key"] == "trips")
    assert trips["accounts"] == 1 and trips["totals"] == {"trips": 4}
    assert got["people"] == {"with_an_account": 3, "in_several_apps": 1, "active_7d": 1, "active_30d": 2, "never_used": 1}


def test_administrators_are_counted_when_asked(apps):
    fill(apps)
    got = stats.overview(("c", "v"), include_admins=True, now=NOW)
    lists = next(a for a in got["apps"] if a["key"] == "lists")
    assert lists["accounts"] == 4 and lists["totals"] == {"lists": 12} and lists["active_7d"] == 2
    assert got["people"]["with_an_account"] == 4


def test_an_app_that_is_down_or_not_updated_yet_is_named_and_left_out_of_the_sums_not_guessed(apps):
    fill(apps)
    apps.down.add("time")
    apps.status["plans"] = 404
    apps.status["finances"] = 403
    got = stats.overview(("c", "v"), now=NOW)
    by = {a["key"]: a for a in got["apps"]}
    assert by["time"]["state"] == "problem" and "didn't answer" in by["time"]["why"] and "accounts" not in by["time"]
    assert by["plans"]["state"] == "not_reporting" and "next deploy" in by["plans"]["why"]
    assert by["finances"]["state"] == "problem" and "administrator" in by["finances"]["why"]
    assert by["lists"]["state"] == "ok" and got["reporting"] == 3
    assert {n["name"] for n in got["not_reporting"]} == {"Time", "Plans", "Finances"}


def test_an_answer_that_cannot_be_read_is_a_problem_not_a_crash(apps):
    apps.answers["lists"] = {"nonsense": True}
    got = stats.overview(("c", "v"), now=NOW)
    assert next(a for a in got["apps"] if a["key"] == "lists")["state"] == "problem"


def test_the_overview_route_leaves_administrators_out_unless_asked(admin, apps, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {ADMIN, "owner@example.com"})  # after the sign-in fixture, which sets its own
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    apps.answers["lists"]["accounts"] = [account("owner@example.com", now, lists=5), account("bob@example.com", now, lists=1)]
    lists = lambda body: next(a for a in body["apps"] if a["key"] == "lists")  # noqa: E731
    assert lists(admin.get("/api/stats").json())["accounts"] == 1
    assert lists(admin.get("/api/stats?admins=true").json())["accounts"] == 2


# --- one person --------------------------------------------------------------------------------------------------------------


def test_a_person_is_shown_across_every_app_with_and_without_an_account(admin, apps, monkeypatch):
    fill(apps)
    people.set_name("bob@example.com", "Bob", "Smith")
    people.file_requests("bob@example.com", ["finances"], None)
    body = admin.get("/api/stats/person", params={"email": "Bob@Example.com"}).json()
    assert body["email"] == "bob@example.com" and (body["first_name"], body["last_name"]) == ("Bob", "Smith") and body["is_admin"] is False
    by = {a["key"]: a for a in body["apps"]}
    assert by["lists"]["account"]["counts"] == {"lists": 2} and by["lists"]["account"]["active_days"] == 5
    assert by["trips"]["account"]["counts"] == {"trips": 4}
    assert by["time"]["account"] is None and by["time"]["state"] == "ok"  # asked, and the app says they have no account
    assert by["finances"]["request"] == "pending"
    assert body["apps_with_an_account"] == 2 and body["active_days"] == 8 and body["last_activity_at"] is not None


def test_a_person_whose_app_did_not_answer_is_not_said_to_have_no_account(admin, apps):
    fill(apps)
    apps.down.add("lists")
    by = {a["key"]: a for a in admin.get("/api/stats/person", params={"email": "bob@example.com"}).json()["apps"]}
    assert by["lists"]["state"] == "problem" and by["lists"]["account"] is None


def test_the_person_route_wants_an_address(admin, apps):
    assert admin.get("/api/stats/person", params={"email": "nonsense"}).status_code == 400
    assert apps.calls == []


# --- the menu and the profile page ----------------------------------------------------------------------------------------------


def test_every_signed_in_page_has_the_menu_and_the_admin_pages_no_longer_carry_ad_hoc_links(admin):
    for path in ("/", "/profile.html", "/users.html", "/stats.html", "/person.html"):
        page = admin.get(path)
        assert page.status_code == 200 and 'id="app-nav-mount"' in page.text and "/nav.js" in page.text, path
        assert "Back to your apps" not in page.text and "Back to People" not in page.text, path


def test_your_name_is_on_the_profile_page_not_the_home_page(admin):
    assert 'id="profile-name"' in admin.get("/profile.html").text
    assert 'id="profile-name"' not in admin.get("/").text and "Manage who can use what" not in admin.get("/").text


def test_the_profile_page_sends_a_signed_out_visitor_to_the_front_page(production):
    resp = production.get("/profile.html")
    assert resp.status_code == 302 and resp.headers["location"] == "/"
