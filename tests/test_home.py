"""The dashboard: the landing page, who it shows what, and that it really is the front door
(signing in through the identity provider and handing people back to their app with a cookie that
covers every subdomain). The sign-in mechanics are tested in shared-python; this checks the wiring."""
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from shared_python import auth, settings

from app import config
from app.main import app

ISSUER = "https://login.example.com"
CLIENT = "client-123"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def client():
    return TestClient(app, follow_redirects=False)


def id_token(key, email="alice@example.com"):
    return jwt.encode(
        {"iss": ISSUER, "aud": CLIENT, "exp": int(time.time()) + 300, "email": email, "email_verified": True},
        key, algorithm="RS256",
    )


@pytest.fixture()
def production(monkeypatch, key):
    """Production as the front door: provider settings, a domain-wide cookie, allowed return hosts."""
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", CLIENT)
    monkeypatch.setattr(settings, "OIDC_CLIENT_ID", CLIENT)
    monkeypatch.setattr(settings, "OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setattr(settings, "OIDC_COOKIE_DOMAIN", ".evancooperman.com")
    monkeypatch.setattr(settings, "OIDC_RETURN_HOSTS", ".evancooperman.com")
    monkeypatch.setattr(auth, "_oidc_signing_key", lambda _t: key.public_key())
    return TestClient(app, base_url="https://dashboard.evancooperman.com", follow_redirects=False)


class AppAnswers:
    """Stands in for lists and trips answering /api/me on this machine; records every call."""

    def __init__(self):
        self.status = {"lists": 200, "trips": 200}  # or an exception instance to raise
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout, "allow_redirects": allow_redirects})
        app = "lists" if ":8030" in url else "trips" if ":8060" in url else "other"
        answer = self.status.get(app, 500)
        if isinstance(answer, Exception):
            raise answer

        class Reply:
            status_code = answer

        return Reply()


@pytest.fixture()
def apps_say(monkeypatch):
    answers = AppAnswers()
    monkeypatch.setattr(requests, "get", answers.get)
    return answers


def names(html):
    return [a["name"] for a in config.APPS if f">{a['name']}</span>" in html]


# --- the landing page --------------------------------------------------------------------


def test_locally_the_dev_user_sees_every_app_at_its_local_address(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "dev@localhost.test" in resp.text
    assert names(resp.text) == [a["name"] for a in config.APPS]
    assert "http://localhost:8030/" in resp.text and "http://localhost:8060/" in resp.text


def test_a_signed_out_visitor_gets_a_sign_in_button_and_no_apps(production):
    resp = production.get("/")
    assert resp.status_code == 200 and 'href="/auth/login"' in resp.text
    assert names(resp.text) == []


def test_an_ordinary_signed_in_person_sees_the_apps_that_say_they_have_an_account(production, key, apps_say):
    production.cookies.set("oidc_session", id_token(key, "alice@example.com"))
    html = production.get("/").text
    assert names(html) == ["Lists", "Trips"]
    assert "https://lists.evancooperman.com/" in html and "localhost" not in html
    assert "finances" not in html.lower().replace("your apps", "")


def test_the_administrator_sees_every_app(production, key):
    production.cookies.set("oidc_session", id_token(key, "Admin@Example.com"))  # case does not matter
    assert names(production.get("/").text) == [a["name"] for a in config.APPS]


def test_an_email_is_escaped_in_the_page(production, key, apps_say):
    production.cookies.set("oidc_session", id_token(key, "x<script>alert(1)</script>@example.com"))
    html = production.get("/").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_a_sign_in_that_isnt_configured_is_an_error_not_a_sign_in_page(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    resp = TestClient(app, follow_redirects=False).get("/")
    assert resp.status_code == 503


def test_pages_are_never_cached(client):
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/style.css").headers["cache-control"] == "no-store"


def test_the_apps_stylesheet_and_script_are_served(client):
    assert client.get("/style.css").status_code == 200 and client.get("/home.js").status_code == 200


def test_the_version_is_reported(client):
    assert "version" in client.get("/api/version").json()


# --- /api/me (the shared header's badge) ----------------------------------------------------


def test_me_is_a_401_when_signed_out(production):
    assert production.get("/api/me").status_code == 401


def test_me_says_who_and_where_log_out_goes(production, key):
    production.cookies.set("oidc_session", id_token(key, "admin@example.com"))
    assert production.get("/api/me").json() == {"email": "admin@example.com", "is_admin": True, "logout_url": "/auth/logout"}


# --- it really is the front door -------------------------------------------------------------


class Provider:
    def __init__(self, key):
        self.key, self.nonce = key, None

    def get(self, url, **kw):
        return _Reply({"authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/oauth/token",
                       "end_session_endpoint": ISSUER + "/oidc/logout", "jwks_uri": ISSUER + "/keys"})

    def post(self, url, data=None, **kw):
        claims = {"iss": ISSUER + "/", "aud": CLIENT, "exp": int(time.time()) + 3600, "email": "alice@example.com",
                  "email_verified": True, "nonce": self.nonce}
        return _Reply({"id_token": jwt.encode(claims, self.key, algorithm="RS256")})


class _Reply:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_signing_in_here_returns_the_person_to_their_app_with_a_cookie_every_app_can_read(production, key, monkeypatch):
    provider = Provider(key)
    monkeypatch.setattr(requests, "get", provider.get)
    monkeypatch.setattr(requests, "post", provider.post)
    start = production.get("/auth/login", params={"next": "https://lists.evancooperman.com/list.html?id=3"})
    query = {k: v[0] for k, v in parse_qs(urlparse(start.headers["location"]).query).items()}
    assert query["redirect_uri"] == "https://dashboard.evancooperman.com/auth/callback"
    provider.nonce = query["nonce"]
    back = production.get("/auth/callback", params={"code": "c", "state": query["state"]})
    assert back.headers["location"] == "https://lists.evancooperman.com/list.html?id=3"
    cookie = next(c for c in back.headers.get_list("set-cookie") if c.startswith("oidc_session=")).lower()
    assert "domain=.evancooperman.com" in cookie and "httponly" in cookie and "secure" in cookie


def test_it_will_not_send_anyone_to_an_address_outside_the_domain(production, key, monkeypatch):
    provider = Provider(key)
    monkeypatch.setattr(requests, "get", provider.get)
    monkeypatch.setattr(requests, "post", provider.post)
    query = {k: v[0] for k, v in parse_qs(urlparse(production.get("/auth/login", params={"next": "https://evil.example.com/"}).headers["location"]).query).items()}
    provider.nonce = query["nonce"]
    back = production.get("/auth/callback", params={"code": "c", "state": query["state"]})
    assert back.headers["location"] == "/"


def test_there_are_no_sign_in_routes_until_the_provider_is_configured(client):
    assert client.get("/auth/login").status_code == 404


# --- the name and the small touches -------------------------------------------------------------


def test_the_page_is_called_brassworks(production, key, apps_say):
    production.cookies.set("oidc_session", id_token(key))
    html = production.get("/").text
    assert "<title>Brassworks</title>" in html and "<h1>Brassworks</h1>" in html


def test_the_sign_in_button_says_lets_jam_and_goes_to_the_sign_in(production):
    html = production.get("/").text
    assert "Let's jam" in html and 'href="/auth/login"' in html and "Brassworks" in html


def test_the_signed_out_page_says_goodbye_and_offers_to_sign_in_again(production):
    resp = production.get("/signed-out")
    assert resp.status_code == 200 and "See you, space cowboy" in resp.text and 'href="/auth/login"' in resp.text


def test_someone_still_signed_in_is_sent_home_not_told_they_are_signed_out(production, key):
    production.cookies.set("oidc_session", id_token(key))
    resp = production.get("/signed-out")
    assert resp.status_code == 302 and resp.headers["location"] == "/"


def test_logging_out_lands_on_the_goodbye_page_when_configured(production, key, monkeypatch):
    monkeypatch.setattr(settings, "OIDC_LOGOUT_RETURN_PATH", "/signed-out")
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Reply({"end_session_endpoint": ISSUER + "/oidc/logout"}))
    target = urlparse(production.get("/auth/logout").headers["location"])
    assert parse_qs(target.query)["post_logout_redirect_uri"] == ["https://dashboard.evancooperman.com/signed-out"]


def test_locally_the_goodbye_page_shows_when_there_is_no_real_session(client):
    """Locally everyone is the dev user, but only a real session cookie should hide the goodbye page."""
    resp = client.get("/signed-out")
    assert resp.status_code == 200 and "See you, space cowboy" in resp.text


def test_an_expired_session_cookie_still_gets_the_goodbye_page(production, key):
    production.cookies.set("oidc_session", id_token(key).replace(id_token(key).split(".")[2], "bad"))
    assert production.get("/signed-out").status_code == 200


# --- a person sees only the apps they have been let into ----------------------------------------------------------------


def home_as(production, key, email="alice@example.com"):
    production.cookies.set("oidc_session", id_token(key, email))
    return production.get("/").text


def test_someone_let_into_only_lists_sees_only_lists(production, key, apps_say):
    apps_say.status = {"lists": 200, "trips": 403}
    assert names(home_as(production, key)) == ["Lists"]


def test_someone_let_into_only_trips_sees_only_trips(production, key, apps_say):
    apps_say.status = {"lists": 403, "trips": 200}
    assert names(home_as(production, key)) == ["Trips"]


def test_someone_let_into_nothing_is_told_so_and_shown_no_apps(production, key, apps_say):
    apps_say.status = {"lists": 403, "trips": 403}
    html = home_as(production, key)
    assert names(html) == [] and "You don't have access to any apps yet" in html and "invited" in html


@pytest.mark.parametrize("answer", [401, 403, 404, 500, 503, 302, 204, requests.ConnectionError("down"), requests.Timeout("slow")])
def test_anything_but_a_yes_hides_the_app_so_a_problem_never_shows_one_the_person_cannot_use(production, key, apps_say, answer):
    apps_say.status = {"lists": answer, "trips": 200}
    assert names(home_as(production, key)) == ["Trips"]


def test_private_apps_are_never_offered_to_anyone_but_an_administrator(production, key, apps_say):
    html = home_as(production, key)
    assert "Finances" not in html and "Time" not in names(html) and "Plans" not in names(html) and "Flights" not in names(html)
    assert not any(":8010" in c["url"] or ":8020" in c["url"] or ":8080" in c["url"] for c in apps_say.calls)  # nothing to ask them


def test_an_administrator_sees_every_app_without_anything_being_asked(production, key, apps_say):
    apps_say.status = {"lists": 403, "trips": 403}
    assert names(home_as(production, key, "admin@example.com")) == [a["name"] for a in config.APPS]
    assert apps_say.calls == []


def test_the_apps_are_asked_on_this_machine_with_the_persons_own_cookie_and_redirects_are_never_followed(production, key, apps_say):
    token = id_token(key, "alice@example.com")
    production.cookies.set("oidc_session", token)
    production.get("/")
    assert sorted(c["url"] for c in apps_say.calls) == ["http://127.0.0.1:8030/api/me", "http://127.0.0.1:8060/api/me"]
    for call in apps_say.calls:
        assert call["headers"] == {"Cookie": f"oidc_session={token}"}
        assert call["allow_redirects"] is False and call["timeout"] == config.APP_CHECK_TIMEOUT


def test_nobody_elses_cookie_or_extra_cookies_are_forwarded(production, key, apps_say):
    production.cookies.set("oidc_session", id_token(key, "alice@example.com"))
    production.cookies.set("theme", "dark")
    production.cookies.set("other_site", "secret")
    production.get("/")
    for call in apps_say.calls:
        assert list(call["headers"]) == ["Cookie"] and call["headers"]["Cookie"].startswith("oidc_session=")
        assert "secret" not in call["headers"]["Cookie"] and "theme" not in call["headers"]["Cookie"]


def test_a_signed_out_visitor_causes_no_calls_to_the_apps(production, apps_say):
    production.get("/")
    assert apps_say.calls == []
