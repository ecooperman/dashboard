import subprocess
from pathlib import Path

from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from shared_python import auth, csrf, oidc_login

from . import access, config, people

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
templates = Jinja2Templates(directory="templates")


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
    return {
        "email": email,
        "is_admin": email in config.ADMIN_EMAILS,
        "logout_url": oidc_login.logout_url(request),
    }


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


@app.get("/api/people")
def get_people(request: Request):
    admin_email(request)
    return people.overview(session_cookie(request))


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
            if result == "added":
                background.add_task(people.tell_them, email, admin, [target["name"]])
        else:
            result = people.revoke(target, email, cookie)
    except people.AppProblem as exc:
        raise HTTPException(status_code=502, detail=str(exc))
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
    apps = access.apps_for(request, email)
    return templates.TemplateResponse(
        request, "index.html", {**base, "email": email, "apps": apps, "is_admin": email in config.ADMIN_EMAILS}
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
