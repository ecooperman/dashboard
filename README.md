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

**Stage 2 (partly built):** the landing page shows only what each person can actually reach (above). The **Users** page
(`/users.html`, administrators only, `app/people.py`) is the one place to add someone and tick which apps they may use, with
their first and last name; ticking or unticking drives each app's own administrator Users routes AS the signed-in administrator (their own
cookie, fixed localhost addresses, no redirects followed, no extra secret), so each app stays the source of truth and checks for
itself. A person is emailed once for everything. Everyone can set their own name on the home page.

**Menu:** every signed-in page has the shared sidebar (bottom bar on a phone), built in `static/nav.js`. The apps stay as tiles on the home page; the menu holds
everything else: **Apps**, **Your profile** (`/profile.html`, your own name), and for an administrator an **Admin** group with **Users** (a badge counts the
requests waiting, from `/api/me`) and **Usage**. The server still refuses the admin pages to anyone else; hiding the links is only tidiness. A new page goes in `nav.js`.

**Requests for access:** an app that answers 403 ("no account") to a signed-in person's `/api/me` shows on their home page as a
locked tile with a **Request access** button (an app that doesn't answer, or answers anything else, is left off, so nobody is
offered a request for something they may already have). `POST /api/requests` files it under the address in their own verified
session (never from the request body), after re-asking the app; one request per person per app, a decline is final, at most
100 wait at once, an optional 300-character note. The administrator is told through the usual `notify` channels (ntfy, Pushover,
`NOTIFY_EMAIL`) in ONE digest every `REQUEST_DIGEST_MINUTES` (default 5) covering everything asked since the last one, and sees a **Requests** list on the Users page: tick the apps you mean, then **Approve** (same `grant()` as
ticking a box, one email) or **Decline** (final) those; anything left unticked stays waiting for a later decision, and so does an app
that can't be reached.

**Usage (`/stats.html`) and a person's page (`/person.html?email=`)**, administrators only (`app/stats.py`): a rough picture of whether and how the apps are
used. Each member app answers its own administrator-only `GET /api/admin/stats` (counts and dates only, from `shared_python.usage`); the dashboard asks all of
them in parallel as the signed-in administrator (their own cookie, the same fixed localhost addresses, no redirects followed) and shows every app side by side
(people, active in 7 and 30 days, never used it, last activity, and the totals of what they keep) plus the people across apps (with an account, active, in more
than one app, waiting requests). A person's page shows the same for one person in each app, with their access requests. Active means last seen, or the newest
thing they made, inside the window; administrators are left out unless a box is ticked; an app that is down or not updated yet is named and left out of the sums.

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

## The daily usage chart

The Usage page draws how many people used the apps on each of the last 30 days. Each app tells the dashboard the first time it sees a
person on a new UTC day (`POST /internal/active-day`, from `shared_python.usage`; refused for anything that came through the internet);
the dashboard keeps only, per day and app, how many other people and how many administrators were there (`daily_active` in `people.db`,
`app/trend.py`), never an address. History starts when each app is next deployed. A report lost while the dashboard is down is a missing
person-day, not recovered. The administrators checkbox applies to the chart too.

## Feedback (one inbox for every app)

People send a problem, an idea or a question from the **Send feedback** entry in any app's menu; the app passes it on
(`shared_python.feedback_relay` -> `POST /internal/feedback`, refused for anything that came through the internet) and the dashboard keeps it
in `people.db` (`app/feedback.py`) with the app it came from and the page they were on. The administrator is told (ntfy, Pushover or
`NOTIFY_EMAIL`, the same channels as access requests) and reads, ticks off and deletes messages by app on the **Feedback** page (menu,
Admin group, with a count of those waiting). Each person sees what they sent, and whether it was dealt with, on their **Profile** page.
Ten a day per person, 4000 characters, no attachments. The Jobs app's old messages are copied in once with the button on the Feedback page.

## Your API keys (one per person, for every app)

A person saves a key for a paid service (Anthropic, Google Maps, Jina) once on their **Profile** page; it is checked with the service, kept
encrypted in `people.db` (`app/keys.py`, `KEY_ENCRYPTION_SECRET` from `shared.env`), never sent back to a browser, and never in an app's database.
Trips and Jobs ask for it at the moment they use it (`shared_python.user_keys` -> `GET /internal/keys/<provider>`), forwarding the person's OWN
sign-in cookie, so an app can only get the key of someone signed in and using it right now. The route refuses anything that came through the internet.
A request with no sign-in (the Chrome extension's token door) gets no key. Only people let into an app (or an administrator) may save one.

