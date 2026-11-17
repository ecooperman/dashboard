import os

HOST = "127.0.0.1"
PORT = 8090

# "local" (default) or "production" (set once, droplet-wide, via systemd's DefaultEnvironment -
# see DEPLOYMENT.md). Drives which shared-assets host the templates point at and whether the
# links to the other apps are the real addresses or localhost ports.
ENV = os.environ.get("APP_ENV", "local")
SHARED_ASSETS_BASE = "https://static.evancooperman.com" if ENV == "production" else "http://127.0.0.1:8070"

# Locally there is no sign-in unless a session exists, so requests are treated as coming from this
# address (never used in production). Same convention as the other apps.
DEV_USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "dev@localhost.test").strip().lower()

# Administrators (comma-separated emails): they see every app on the landing page. Everyone else
# sees only the apps marked shared. Unset locally, the dev user is one.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
if ENV != "production":
    ADMIN_EMAILS.add(DEV_USER_EMAIL)

# The apps the landing page links to. `access` says who sees one:
#   "member" - the app has its own accounts (lists, trips, time, plans): a person sees it only if THAT app says they have an
#              account (asked over localhost with their own session cookie, see app/access.py);
#   "admin"  - a private app with no accounts yet (finances, flights): only the administrators see it.
# Administrators see every app.
APPS = [
    {"key": "lists", "name": "Lists", "host": "lists.evancooperman.com", "port": 8030, "icon": "list",
     "blurb": "Gift ideas, shopping lists, anything worth keeping track of.", "access": "member"},
    {"key": "trips", "name": "Trips", "host": "trips.evancooperman.com", "port": 8060, "icon": "compass",
     "blurb": "Plan trips: places, activities, stays and the days between.", "access": "member"},
    {"key": "time", "name": "Time", "host": "time.evancooperman.com", "port": 8010, "icon": "calendar",
     "blurb": "Time management.", "access": "member"},
    {"key": "plans", "name": "Plans", "host": "plans.evancooperman.com", "port": 8020, "icon": "users",
     "blurb": "Places and ideas to do, saved from around the web.", "access": "member"},
    {"key": "finances", "name": "Finances", "host": "finances.evancooperman.com", "port": 8080, "icon": "wallet",
     "blurb": "The household budget.", "access": "admin"},
    {"key": "flights", "name": "Flights", "host": "flights.evancooperman.com", "port": 8050, "icon": "plane",
     "blurb": "Flight market agents.", "access": "admin"},
]

# Where the dashboard asks a "member" app whether someone has an account: the app's own process on this machine
# (never through the internet), the same addresses finances uses.
APP_API_BASE = {
    "lists": os.environ.get("LISTS_API_BASE", "http://127.0.0.1:8030"),
    "trips": os.environ.get("TRIPS_API_BASE", "http://127.0.0.1:8060"),
    "time": os.environ.get("TIME_API_BASE", "http://127.0.0.1:8010"),
    "plans": os.environ.get("PLANS_API_BASE", "http://127.0.0.1:8020"),
}
APP_CHECK_TIMEOUT = 2  # seconds; an app that does not answer in time is left off the page


def app_url(app: dict) -> str:
    return f"https://{app['host']}/" if ENV == "production" else f"http://localhost:{app['port']}/"
