# dashboard

The front door for every app, and (later) the landing page for all of them.
`dashboard.evancooperman.com`, port **8090**. The only data it keeps is people's first and last names and the requests for access (`people.db`, created on first use).

**Stage 1 (built):** it does the signing in, through the identity provider (Auth0): `/auth/login`,
`/auth/callback`, `/auth/logout` from `shared_python.oidc_login`. It sets one session cookie for the whole
parent domain (`.evancooperman.com`), so a single sign-in covers every app and one log out ends it
everywhere. The other apps never see the client secret: they only check the cookie and send signed-out
visitors here with the address they wanted (`OIDC_LOGIN_URL`, see `shared-python/README.md`).
The home page shows a Sign in button when signed out, and the person's apps when signed in. Administrators see every
app in `APPS` (`app/config.py`). Everyone else sees only the apps they have actually been let into: for the apps
with accounts (lists, trips) the dashboard asks the app itself, on this machine, whether the person has an account
(`app/access.py`: the app's own `/api/me`, with the person's own session cookie, redirects never followed); an app
that says anything but yes, or doesn't answer, is left off. Private apps with no accounts (time, plans, finances,
flights) are shown to administrators only. Someone let into nothing is told so.

**Stage 2 (partly built):** the landing page shows only what each person can actually reach (above). The **People** page
(`/users.html`, administrators only, `app/people.py`) is the one place to add someone and tick which apps they may use, with
their first and last name; ticking or unticking drives each app's own administrator Users routes AS the signed-in administrator (their own
cookie, fixed localhost addresses, no redirects followed, no extra secret), so each app stays the source of truth and checks for
itself. A person is emailed once for everything. Everyone can set their own name on the home page.

**Requests for access:** an app that answers 403 ("no account") to a signed-in person's `/api/me` shows on their home page as a
locked tile with a **Request access** button (an app that doesn't answer, or answers anything else, is left off, so nobody is
offered a request for something they may already have). `POST /api/requests` files it under the address in their own verified
session (never from the request body), after re-asking the app; one request per person per app, a decline is final, at most
100 wait at once, an optional 300-character note. The administrator is told through the usual `notify` channels (ntfy, Pushover,
`NOTIFY_EMAIL`) and sees a **Requests** list on the People page: tick the apps you mean, then **Approve** (same `grant()` as
ticking a box, one email) or **Decline** (final) those; anything left unticked stays waiting for a later decision, and so does an app
that can't be reached.

**When an app has a real problem** (an error answer, a timeout or no answer from its `/api/me`, for example while it restarts during a
deploy) the home page shows it as a red tile with "Couldn't check your access just now", plus a note to reload in a minute and a
**customer service** link: a `mailto:` to every address in `ADMIN_EMAILS` with what happened filled in. A 403 is a plain "no account"
(offered as a request), and a 401 or a redirect (the app doesn't recognise their sign-in) just leaves the app off.
Still to come: the apps' own Users pages becoming links here.

## Run it locally

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements-dev.txt
venv/bin/uvicorn app.main:app --port 8090 --reload
venv/bin/python -m pytest -q
```

Python 3.12 (the other apps use it; an older system `python3` can't install the pinned FastAPI).
Locally there is no sign-in unless you set the provider settings: you are `dev@localhost.test`
(an administrator), like every other app. The shared theme is read from `http://127.0.0.1:8070`
(run `shared-assets` for it to look right).

`OIDC_LOGOUT_RETURN_PATH=/signed-out` makes logging out land on the "See you, space cowboy..." page; its full
address (`https://dashboard.evancooperman.com/signed-out`) must be in the Auth0 application's Allowed Logout URLs.

To try the real flow locally, put the Auth0 settings in your shell (the file `oidc-auth0.env` in the
projects folder has them) and add `http://localhost:8090/auth/callback` and `http://localhost:8090/`
to the Auth0 application's allowed callback and logout URLs.

## Settings

Its own env file on the droplet, `/etc/personal-apps/dashboard.env` (root-only, **not** `shared.env`,
which every app reads):

```
OIDC_CLIENT_ID=<the Auth0 application's Client ID>
OIDC_CLIENT_SECRET=<its Client Secret>
OIDC_COOKIE_DOMAIN=.evancooperman.com
OIDC_RETURN_HOSTS=.evancooperman.com
OIDC_LOGOUT_RETURN_PATH=/signed-out
ADMIN_EMAILS=you@example.com
```

plus `OIDC_ISSUER` and `OIDC_AUDIENCE` (the client id), which `shared.env` carries for every app.

## Droplet and Cloudflare (once)

See DEPLOYMENT.md ("Adding a new app" and "Signing in with Auth0"). Specific to this app: its
hostname must **bypass Cloudflare Access** (a Bypass policy for just `dashboard.evancooperman.com`),
or people hit Cloudflare's login before they can reach the sign-in; and it needs the same cache-bypass rule
as the other hostnames.
