import asyncio
import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from shared_python import auth, csrf, oidc_login

from . import access, config, people, stats

# The front door for every app: /auth/login, /auth/callback and /auth/logout (shared_python.oidc_login)
# sign people in through the identity provider and set the session cookie for the whole parent
# domain; every other app only checks that cookie. The landing page is the home page. The only data it keeps is people's first and last names (app/people.py).
# Settings (the client id and secret, the cookie domain, the allowed return hosts) come from this
# app's own env file on the droplet - see DEPLOYMENT.md and shared-python's README.


def _get_git_sha() -> str:
    """Short commit hash the running app was deployed from (the deploy pulls a real checkout)."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


GIT_SHA = _get_git_sha()

app = FastAPI(title="dashboard")
csrf.install(app)  # a change must come from this app's own pages, not another app or site (shared_python.csrf)
auth.warm_up_on_start(app)  # fetch the sign-in keys in the background at start-up, so nobody's first page waits for them
templates = Jinja2Templates(directory="templates")


DIGEST_CHECK_SECONDS = 30  # how often to look whether a digest is due (people.send_digest decides; usually it does nothing)


async def _send_digests_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(people.send_digest)  # SQLite and SMTP block, so off the event loop
        except Exception:  # noqa: BLE001 - a bad tick must never stop the next ones
            logging.getLogger(__name__).exception("Sending the requests digest failed")
        await asyncio.sleep(DIGEST_CHECK_SECONDS)


def send_digests_on_start(application: FastAPI) -> None:
    """Tell the administrator about new access requests in one periodic digest (see people.send_digest), from start-up to shut-down."""
    previous = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner_app):
        task = asyncio.create_task(_send_digests_forever())
        try:
            async with previous(inner_app) as state:
                yield state
        finally:
            task.cancel()

    application.router.lifespan_context = lifespan


send_digests_on_start(app)


def signed_in_email(request: Request) -> str:
    """The signed-in person's normalized email (401 if there isn't one)."""
    email = auth.request_email(request, config.DEV_USER_EMAIL).strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=401, detail="Not signed in")
    return email


@app.get("/api/version")
def get_version():
    return {"version": GIT_SHA}


@app.get("/api/me")
def me(request: Request):
    """Who is signed in, for the shared page header (Global.mountUserBadge)."""
    email = signed_in_email(request)
    is_admin = email in config.ADMIN_EMAILS
    out = {"email": email, "is_admin": is_admin, "logout_url": oidc_login.logout_url(request)}
    if is_admin:
        out["waiting_requests"] = people.waiting_count()  # for the menu's badge on People
    return out


@app.get("/api/apps")
async def apps_for_nav(request: Request):
    """The apps this person may open, for the sidebar every app shows (shared-assets theme.js). Read from the apps' own pages, so it
    answers cross-origin for the addresses in config.nav_origins() only (with credentials: the cookie is the sign-in). Remembered for a
    few minutes per person (app/access.py) and forgotten when an administrator changes their access, so opening pages does not ask every app every time."""
    headers = {"Cache-Control": "private, no-store", "Vary": "Origin"}
    origin = request.headers.get("origin", "")
    if origin in config.nav_origins():
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    try:
        email = signed_in_email(request)
    except HTTPException:
        return JSONResponse({"detail": "Not signed in"}, status_code=401, headers=headers)  # with the headers, so the page can read the 401
    apps = await asyncio.to_thread(access.nav_apps, request, email)  # asking the apps blocks, so off the event loop
    return JSONResponse({"apps": apps}, headers=headers)


class AccessChanged(BaseModel):
    email: str


@app.post("/internal/access-changed", status_code=204)
def access_changed(body: AccessChanged, request: Request):
    """An app tells us that someone's access to it just changed (shared_python.access_events), so their menu's list of apps is asked
    for afresh. Only for the apps on this machine: anything that arrived through the internet (Cloudflare marks every such request)
    is refused as if the route did not exist. All it does is drop one remembered answer."""
    if any(header in request.headers for header in ("cf-connecting-ip", "cf-ray", "x-forwarded-for")):
        raise HTTPException(status_code=404, detail="Not Found")
    access.forget_apps(body.email)


# --- the central Users page and people's names ---------------------------------------------------------------------------
# Administrators only (config.ADMIN_EMAILS); each app ALSO checks for itself that the caller is its administrator, because
# the dashboard acts through the administrator's own session cookie (app/people.py).


def admin_email(request: Request) -> str:
    email = signed_in_email(request)
    if email not in config.ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Only an administrator can do that")
    return email


def session_cookie(request: Request):
    name = auth.oidc_cookie_name() or ""
    return name, (request.cookies.get(name, "") if name else "")


class NameBody(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class PersonAdd(NameBody):
    email: str
    apps: List[str]


class AccessChange(BaseModel):
    email: str
    app: str
    enabled: bool


class NameChange(NameBody):
    email: str


class RequestBody(BaseModel):
    apps: List[str]
    note: Optional[str] = None


class Decision(BaseModel):
    email: str
    approve: List[str] = []  # the apps to let them into
    decline: List[str] = []  # the apps to turn down for good; any other app they are waiting on stays waiting


def _app(key: str) -> dict:
    found = next((a for a in people.member_apps() if a["key"] == key), None)
    if found is None:
        raise HTTPException(status_code=400, detail="That isn't an app with people in it")
    return found


def _names(first, last):
    try:
        return people.clean_name(first), people.clean_name(last)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/users.html", response_class=HTMLResponse)
def users_page(request: Request):
    """The central Users page. Anyone else goes home."""
    try:
        admin_email(request)
    except HTTPException:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "users.html", {"shared_assets_base": config.SHARED_ASSETS_BASE})


def _admin_page(request: Request, template: str):
    """A page for administrators only; anyone else goes home."""
    try:
        admin_email(request)
    except HTTPException:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, template, {"shared_assets_base": config.SHARED_ASSETS_BASE})


@app.get("/stats.html", response_class=HTMLResponse)
def stats_page(request: Request):
    """How the apps are being used, side by side."""
    return _admin_page(request, "stats.html")


@app.get("/person.html", response_class=HTMLResponse)
def person_page(request: Request):
    """One person across every app (the address is in the query, ?email=)."""
    return _admin_page(request, "person.html")


@app.get("/api/stats")
def get_stats(request: Request, admins: bool = False):
    """Every app's usage side by side. Administrators are left out of the sums unless `admins` is true."""
    admin_email(request)
    return stats.overview(session_cookie(request), include_admins=admins)


@app.get("/api/stats/person")
def get_person_stats(email: str, request: Request):
    admin_email(request)
    if "@" not in email:
        raise HTTPException(status_code=400, detail="That doesn't look like an email address")
    return stats.person(email, session_cookie(request))


@app.get("/profile.html", response_class=HTMLResponse)
def profile_page(request: Request):
    """Your own name. Signed-out visitors go to the front page, where they can sign in."""
    try:
        email = signed_in_email(request)
    except HTTPException:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "profile.html", {"shared_assets_base": config.SHARED_ASSETS_BASE, "email": email})


@app.get("/api/people")
def get_people(request: Request):
    admin_email(request)
    data = people.overview(session_cookie(request))
    data["requests"] = people.waiting_requests(data["people"])
    return data


@app.post("/api/requests/decide")
def decide_request(body: Decision, request: Request, background: BackgroundTasks):
    """Act on SOME of what a person asked for: approve those (the same as ticking their boxes: one email) and/or decline those
    (final: they can't ask again). Whatever isn't named stays waiting for a later decision. An app that could not be reached
    stays waiting too, so nothing is lost."""
    admin = admin_email(request)
    email = body.email.strip().lower()
    waiting = people.requests_for(email)
    if not waiting:
        raise HTTPException(status_code=404, detail="Nobody is waiting under that address")
    approve = [_app(key) for key in dict.fromkeys(body.approve)]
    decline = [_app(key) for key in dict.fromkeys(body.decline)]
    if not approve and not decline:
        raise HTTPException(status_code=400, detail="Choose at least one app")
    named = [t["key"] for t in approve + decline]
    if any(key not in waiting for key in named):
        raise HTTPException(status_code=400, detail="They didn't ask for that app")
    if len(set(named)) != len(named):
        raise HTTPException(status_code=400, detail="An app can't be both approved and declined")
    cookie = session_cookie(request)
    added, failed, let_in = [], [], []
    for target in approve:
        try:
            if people.grant(target, email, cookie) == "added":
                added.append(target["name"])
            let_in.append(target["key"])
        except people.AppProblem as exc:
            failed.append(str(exc))  # that one stays waiting
    people.settle_requests(email, let_in)
    people.settle_requests(email, [t["key"] for t in decline], decline=True)
    access.forget_apps(email)  # their menu's list of apps changes at once
    if added:
        background.add_task(people.tell_them, email, admin, added)
    return {"added": added, "failed": failed, "left_waiting": people.requests_for(email)}


@app.post("/api/requests")
def ask_for_access(body: RequestBody, request: Request):
    """The signed-in person asks for the apps they have no account in. The address is the one they signed in with."""
    email = signed_in_email(request)
    states = access.account_states(request, email)
    wanted = list(dict.fromkeys(body.apps))
    if not wanted:
        raise HTTPException(status_code=400, detail="Choose at least one app")
    for key in wanted:
        if states.get(key) != "no":  # only an app that says they have no account (not one they have, or one that did not answer)
            raise HTTPException(status_code=400, detail="You can't ask for that app")
    try:
        filed, already = people.file_requests(email, wanted, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OverflowError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    return {"filed": filed, "already": already}


@app.post("/api/people")
def add_person(body: PersonAdd, request: Request, background: BackgroundTasks):
    """Let someone into the ticked apps (one email for all of them) and save the name typed with them, if any."""
    admin = admin_email(request)
    email = body.email.strip().lower()
    if not people.EMAIL.match(email) or email.endswith(".invalid"):
        raise HTTPException(status_code=400, detail="That doesn't look like an email address")
    chosen = [_app(key) for key in dict.fromkeys(body.apps)]
    if not chosen:
        raise HTTPException(status_code=400, detail="Tick at least one app")
    first, last = _names(body.first_name, body.last_name)
    cookie = session_cookie(request)
    added, already, failed = [], [], []
    for target in chosen:
        try:
            (added if people.grant(target, email, cookie) == "added" else already).append(target["name"])
        except people.AppProblem as exc:
            failed.append(str(exc))
    if first or last:
        people.set_name(email, first, last)
    people.settle_requests(email, [t["key"] for t in chosen if t["name"] in added or t["name"] in already])
    access.forget_apps(email)
    if added:
        background.add_task(people.tell_them, email, admin, added)
    return {"email": email, "added": added, "already": already, "failed": failed}


@app.put("/api/people/access")
def change_access(body: AccessChange, request: Request, background: BackgroundTasks):
    """Tick or untick one app for one person. Ticking tells them by email; unticking keeps their data."""
    admin = admin_email(request)
    email = body.email.strip().lower()
    target = _app(body.app)
    cookie = session_cookie(request)
    try:
        if body.enabled:
            result = people.grant(target, email, cookie)
            people.settle_requests(email, [target["key"]])  # let in by hand, so any request for it is finished
            if result == "added":
                background.add_task(people.tell_them, email, admin, [target["name"]])
        else:
            result = people.revoke(target, email, cookie)
    except people.AppProblem as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        access.forget_apps(email)  # whatever happened, ask the apps afresh next time
    return {"result": result}


@app.put("/api/people/name")
def set_person_name(body: NameChange, request: Request):
    admin_email(request)
    first, last = _names(body.first_name, body.last_name)
    people.set_name(body.email, first, last)
    return {"first_name": first, "last_name": last}


@app.get("/api/profile")
def get_profile(request: Request):
    """The signed-in person's own name (anyone who can sign in may set it)."""
    first, last = people.get_name(signed_in_email(request))
    return {"first_name": first, "last_name": last}


@app.put("/api/profile")
def set_profile(body: NameBody, request: Request):
    first, last = _names(body.first_name, body.last_name)
    people.set_name(signed_in_email(request), first, last)
    return {"first_name": first, "last_name": last}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """The landing page: the person's apps when signed in, a sign-in button otherwise."""
    base = {"shared_assets_base": config.SHARED_ASSETS_BASE}
    try:
        email = signed_in_email(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise  # 503: sign-in isn't configured on the server
        return templates.TemplateResponse(request, "signed_out.html", base)
    apps, locked, broken = access.apps_and_locked(request, email)
    asked = people.request_states(email)
    for app_ in locked:
        app_["request"] = asked.get(app_["key"], "")  # "", "pending" or "declined"
    is_admin = email in config.ADMIN_EMAILS
    return templates.TemplateResponse(
        request, "index.html",
        {**base, "email": email, "apps": apps, "locked": locked, "broken": broken,
         "help_link": access.customer_service_link(email, broken) if broken else "",
         "is_admin": is_admin, "waiting": people.waiting_count() if is_admin else 0},
    )


@app.get("/signed-out", response_class=HTMLResponse)
def signed_out(request: Request):
    """Where logging out lands (OIDC_LOGOUT_RETURN_PATH). Someone who still has a real session cookie
    just goes home instead of being told they are signed out. (Only a real session counts: locally
    everyone is the dev user, which would otherwise hide this page.)"""
    cookie = auth.oidc_cookie_name()
    if cookie and request.cookies.get(cookie):
        try:
            signed_in_email(request)
            return RedirectResponse("/", status_code=302)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    return templates.TemplateResponse(request, "goodbye.html", {"shared_assets_base": config.SHARED_ASSETS_BASE})


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """Never let the browser (or an installed-app cache) serve a stale copy, and above all never a
    stale sign-in redirect. `no-store`, not `no-cache`: it is the only unambiguous 'cache this
    nowhere' signal. (Cloudflare's edge needs its own bypass rule for this hostname too.)"""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(oidc_login.router)  # /auth/login, /auth/callback, /auth/logout (404 until configured)

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=True)
