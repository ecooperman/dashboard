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
#   "member" - the app has its own accounts (lists, trips, time, plans, finances, resume): a person sees it only if THAT app says they
#              have an account (asked over localhost with their own session cookie, see app/access.py);
#   "admin"  - a private app with no accounts yet (flights): only the administrators see it.
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
     "blurb": "The household budget.", "access": "member"},
    {"key": "resume", "name": "Resume", "host": "jobs.evancooperman.com", "port": 8041, "icon": "briefcase",
     "blurb": "Build your resume and keep it up to date.", "access": "member"},
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
    "finances": os.environ.get("FINANCES_API_BASE", "http://127.0.0.1:8080"),
    "resume": os.environ.get("RESUME_API_BASE", "http://127.0.0.1:8041"),  # the resume ADMIN app (8041), not the public site
}
# How often (at most) the administrator is told about new requests for access: one message for everything asked since the last
# one, instead of one per request. The People page and the home page's count are always up to date regardless.
REQUEST_DIGEST_MINUTES = max(1, int(os.environ.get("REQUEST_DIGEST_MINUTES", "5")))
APP_CHECK_TIMEOUT = 2  # seconds; an app that does not answer in time is left off the page


def app_url(app: dict) -> str:
    return f"https://{app['host']}/" if ENV == "production" else f"http://localhost:{app['port']}/"


# The central Users page (app/people.py) drives each member app's own administrator routes, as the signed-in administrator
# (their own session cookie, sent to the fixed localhost addresses above and nowhere else). Where each app's list-and-add
# route is, and how it removes someone: "action" apps have POST <route>/{id}/revoke, the others PATCH <route>/{id} with a status.
APP_USERS = {
    "lists": {"route": "/api/admin/users", "revoke": "action"},
    "trips": {"route": "/api/admin/users", "revoke": "action"},
    "time": {"route": "/api/users", "revoke": "patch"},
    "plans": {"route": "/api/users", "revoke": "patch"},
    "finances": {"route": "/api/users", "revoke": "patch"},
    "resume": {"route": "/api/users", "revoke": "patch"},
}
APP_CALL_TIMEOUT = 10  # seconds, for changes (a slow app is reported, not waited on forever)

# First and last names (the dashboard is where they live; the apps' Add-box suggestions read this file read-only). Never
# needed to sign in. Kept in the dashboard's own folder so shared_python.directory finds it next to the other apps.
PEOPLE_DB = os.environ.get("PEOPLE_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "people.db")

# Where the email tells a newly added person to go.
DASHBOARD_URL = "https://dashboard.evancooperman.com/" if ENV == "production" else f"http://localhost:{PORT}/"
