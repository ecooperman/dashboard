import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from shared_python import auth, oidc_login

from . import config

# The front door for every app: /auth/login, /auth/callback and /auth/logout (shared_python.oidc_login)
# sign people in through the identity provider and set the session cookie for the whole parent
# domain; every other app only checks that cookie. The landing page is the home page. No database.
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
    admin = email in config.ADMIN_EMAILS
    apps = [dict(a, url=config.app_url(a)) for a in config.APPS if admin or a["shared"]]
    return templates.TemplateResponse(request, "index.html", {**base, "email": email, "apps": apps})


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
