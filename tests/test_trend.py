"""The daily history behind the Usage page's chart: counts per app per day, never who."""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from app import config, trend
from tests.test_home import client, key, production  # noqa: F401 - fixtures
from tests.test_people import admin  # noqa: F401 - fixture
from tests.test_stats import apps  # noqa: F401 - fixture

TODAY = date(2026, 11, 21)


@pytest.fixture(autouse=True)
def admins(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"admin@example.com"})


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_each_report_adds_one_person_to_that_apps_day():
    trend.record("lists", "ann@example.com", day(0), TODAY)
    trend.record("lists", "bob@example.com", day(0), TODAY)
    trend.record("lists", "ann@example.com", day(1), TODAY)
    trend.record("trips", "ann@example.com", day(0), TODAY)
    got = trend.series(today=TODAY, days=3)
    assert got["days"] == [day(2), day(1), day(0)]
    by_app = {a["key"]: a["counts"] for a in got["apps"]}
    assert by_app["lists"] == [0, 1, 2] and by_app["trips"] == [0, 0, 1] and by_app["time"] == [0, 0, 0]
    assert got["total"] == [0, 1, 3] and got["since"] == day(1)


def test_administrators_are_counted_apart_and_left_out_unless_asked_for():
    trend.record("lists", "Admin@Example.com", day(0), TODAY)
    trend.record("lists", "ann@example.com", day(0), TODAY)
    assert trend.series(today=TODAY, days=1)["total"] == [1]
    assert trend.series(include_admins=True, today=TODAY, days=1)["total"] == [2]


def test_no_address_is_kept_only_counts():
    trend.record("lists", "ann@example.com", day(0), TODAY)
    with sqlite3.connect(config.PEOPLE_DB) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(daily_active)")]
        rows = db.execute("SELECT * FROM daily_active").fetchall()
    assert columns == ["day", "app", "people", "admins"]
    assert "ann" not in repr(rows) and "@" not in repr(rows)


def test_a_day_that_is_not_a_recent_date_is_refused():
    for bad in ("yesterday", "2026-13-40", "", "2020-01-01", day(-5)):
        with pytest.raises(ValueError):
            trend.record("lists", "ann@example.com", bad, TODAY)
    trend.record("lists", "ann@example.com", day(2), TODAY)  # a little clock drift is fine
    trend.record("lists", "ann@example.com", day(-1), TODAY)


def test_only_apps_with_accounts_are_charted():
    trend.record("flights", "ann@example.com", day(0), TODAY)  # an administrator-only app has no accounts to count
    assert "flights" not in [a["key"] for a in trend.series(today=TODAY, days=1)["apps"]]


def test_an_app_reports_through_the_route_and_the_internet_cannot(client):
    today = datetime.utcnow().date().isoformat()
    body = {"app": "lists", "email": "ann@example.com", "day": today}
    assert client.post("/internal/active-day", json=body).status_code == 204
    assert trend.series(days=1)["total"] == [1]
    assert client.post("/internal/active-day", json=body, headers={"Cf-Connecting-Ip": "203.0.113.5"}).status_code == 404
    assert client.post("/internal/active-day", json={**body, "app": "nowhere"}).status_code == 400
    assert client.post("/internal/active-day", json={**body, "app": "flights"}).status_code == 400
    assert client.post("/internal/active-day", json={**body, "day": "2020-01-01"}).status_code == 400
    assert trend.series(days=1)["total"] == [1]  # none of the refused ones counted


def test_the_usage_overview_carries_the_trend_and_honours_the_administrator_toggle(admin, apps):  # noqa: F811
    today = datetime.utcnow().date().isoformat()
    trend.record("lists", "ann@example.com", today)
    trend.record("lists", "admin@example.com", today)
    assert admin.get("/api/stats").json()["trend"]["total"][-1] == 1
    assert admin.get("/api/stats?admins=true").json()["trend"]["total"][-1] == 2
