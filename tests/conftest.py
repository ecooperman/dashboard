"""Whatever is set in the developer's shell (a real client secret, a real issuer), a test must never
reach out with it: the sign-in settings are blanked, and any HTTP call a test didn't stub fails."""
import logging

import pytest
import requests
from shared_python import oidc_login, settings

SIGN_IN_SETTINGS = (
    "CF_TEAM_DOMAIN", "CF_ACCESS_AUD", "OIDC_ISSUER", "OIDC_JWKS_URL", "OIDC_AUDIENCE", "OIDC_COOKIE", "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET", "OIDC_COOKIE_DOMAIN", "OIDC_RETURN_HOSTS", "OIDC_LOGIN_URL", "OIDC_LOGOUT_RETURN_PATH",
)


@pytest.fixture(autouse=True)
def _people_file_is_a_throwaway(monkeypatch, tmp_path):
    """Names and requests live in a SQLite file; a test must never touch the real one on this machine."""
    from app import config

    monkeypatch.setattr(config, "PEOPLE_DB", str(tmp_path / "people.db"))


@pytest.fixture(autouse=True)
def _nothing_leaves_the_machine(monkeypatch):
    for name in SIGN_IN_SETTINGS:
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "ENV", "local")
    monkeypatch.setattr(oidc_login, "_discovery_cache", {})

    def refuse(*args, **kwargs):
        raise AssertionError(f"a test made a real network call: {args[:2]}")

    for name in ("request", "get", "post"):
        monkeypatch.setattr(requests, name, refuse)
    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).disabled = False
