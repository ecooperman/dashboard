"""Builds the Brassworks versions of Auth0's email templates from one shared layout.

    python3 email-templates/build.py        (from the dashboard folder)

Writes one HTML file per template into out/, plus out/README.md listing, for each, the Auth0
template it replaces, the subject line and where its link goes. Paste each file's contents into
Auth0 (Branding -> Email Templates -> the template -> Message) - they use Auth0's Liquid
placeholders ({{ url }}, {{ code }}, {{ user.email }}); check each against the template's own default
in the Auth0 editor (its Preview shows whether a placeholder filled in).

Colours are the logo's: the deep brass #8A5A14 for the button (white text on it is 5.9:1) on a warm
off-white. The logo is a PNG on the shared-assets host (email clients do not show SVG).
"""
from pathlib import Path

BRAND = "Brassworks"
TAGLINE = "Lists, trips, plans and the rest of the workshop"
HOME = "https://dashboard.evancooperman.com"
LOGO = "https://static.evancooperman.com/logo-email.png"
BRASS = "#8A5A14"
BRASS_DARK = "#4A3010"
PAGE_BG = "#F6F1E7"
TEXT = "#2B2A26"
MUTED = "#6B685F"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def button(label: str, href: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:26px 0 8px">'
        f'<tr><td align="center" bgcolor="{BRASS}" style="border-radius:8px">'
        f'<a href="{href}" style="display:inline-block;padding:14px 28px;font-family:{FONT};font-size:16px;'
        f'font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px">{label}</a>'
        f"</td></tr></table>"
    )


def code_box() -> str:
    return (
        f'<div style="margin:22px 0;padding:16px 20px;background:{PAGE_BG};border:1px solid #E3D9C3;border-radius:8px;'
        f'font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:30px;letter-spacing:6px;text-align:center;'
        f'color:{BRASS_DARK}">{{{{ code }}}}</div>'
    )


def link_fallback() -> str:
    return (
        f'<p style="margin:18px 0 0;font-size:13px;color:{MUTED};line-height:1.5">If the button doesn\'t work, '
        f'copy this address into your browser:<br><a href="{{{{ url }}}}" style="color:{BRASS};word-break:break-all">'
        f"{{{{ url }}}}</a></p>"
    )


def layout(heading: str, paragraphs: list, extra: str = "", why: str = "") -> str:
    body = "".join(f'<p style="margin:0 0 14px;font-size:16px;line-height:1.55;color:{TEXT}">{p}</p>' for p in paragraphs)
    why = why or "You received this because {{ user.email }} was used with Brassworks. If that wasn't you, you can safely ignore this email."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{heading}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE_BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE_BG}" style="background:{PAGE_BG}">
<tr><td align="center" style="padding:32px 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px">
    <tr><td bgcolor="#ffffff" style="background:#ffffff;border:1px solid #E3D9C3;border-radius:12px;padding:28px 32px 28px;font-family:{FONT}">
      <div style="text-align:center;padding:0 0 18px">
        <img src="{LOGO}" width="72" height="72" alt="{BRAND}" style="display:inline-block;border:0;width:72px;height:72px">
        <div style="margin-top:6px;font-family:{FONT};font-size:20px;font-weight:700;letter-spacing:.5px;color:{BRASS_DARK}">{BRAND}</div>
      </div>
      <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:{TEXT};font-family:{FONT}">{heading}</h1>
      {body}
      {extra}
    </td></tr>
    <tr><td align="center" style="padding:20px 8px 0;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED}">
      {why}<br>
      <span style="color:{BRASS_DARK}">{BRAND}</span> &middot; {TAGLINE}
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>
"""


TEMPLATES = [
    # (file, Auth0 template name, subject, where its link goes / note, heading, paragraphs, extra, why)
    ("verification-link", "Verification Email (using link)", "Confirm your email for Brassworks",
     "Redirect To: " + HOME + " (so people land somewhere useful after confirming)",
     "Confirm your email",
     ["Thanks for signing up. Confirm that <strong>{{ user.email }}</strong> is yours to finish setting up your account."],
     button("Confirm my email", "{{ url }}") + link_fallback(), ""),
    ("verification-code", "Verification Email (using code)", "Your Brassworks verification code",
     "Shows a code to type into the page that asked for it; no link.",
     "Your verification code",
     ["Enter this code on the page you just came from to confirm <strong>{{ user.email }}</strong>:"],
     code_box() + f'<p style="margin:0;font-size:13px;color:{MUTED}">The code expires after a short while. Never share it with anyone.</p>', ""),
    ("welcome", "Welcome Email", "Welcome to Brassworks",
     "The button goes to the dashboard.",
     "Welcome to Brassworks",
     ["Your account is ready. Everything you can reach lives on one page: your lists, your trips and the rest of the workshop.",
      "A passkey is the quickest way to sign in next time: no password to remember."],
     button("Open Brassworks", HOME), ""),
    ("change-password-link", "Change Password (using link)", "Reset your Brassworks password",
     "Link goes to Auth0's reset page.",
     "Choose a new password",
     ["Someone asked to reset the password for <strong>{{ user.email }}</strong>. Use the button to choose a new one."],
     button("Choose a new password", "{{ url }}") + link_fallback(),
     "If you didn't ask for this, you can ignore this email; your password hasn't changed."),
    ("change-password-code", "Change Password (using code)", "Your Brassworks password reset code",
     "Shows a code and a link to the reset page.",
     "Your password reset code",
     ["Someone asked to reset the password for <strong>{{ user.email }}</strong>. Use this code on the reset page:"],
     code_box() + button("Open the reset page", "{{ url }}"),
     "If you didn't ask for this, you can ignore this email; your password hasn't changed."),
    ("blocked-account", "Blocked Account Email", "Sign-in to Brassworks was blocked",
     "Link re-enables sign-in from the blocked location.",
     "We blocked a suspicious sign-in",
     ["There were too many failed attempts to sign in to <strong>{{ user.email }}</strong>, so we paused sign-in from that location to keep your account safe.",
      "If that was you, use the button to unblock it. If it wasn't, you don't need to do anything."],
     button("Unblock sign-in", "{{ url }}") + link_fallback(), ""),
    ("password-breach", "Password Breach Alert", "Change your Brassworks password",
     "No link placeholder assumed: it points people to the sign-in page's reset option.",
     "Please change your password",
     ["A password used with <strong>{{ user.email }}</strong> appeared in a data breach at another service, so we've paused sign-in to protect you.",
      "Use <strong>Forgot password</strong> on the sign-in page to choose a new one, and avoid using it anywhere else. A passkey avoids passwords altogether."],
     button("Go to sign-in", HOME), ""),
    ("mfa-enrollment", "Enroll in Multi-factor Authentication", "Set up two-step sign-in for Brassworks",
     "Only sent if you invite someone to two-step sign-in (a paid Auth0 feature).",
     "Set up two-step sign-in",
     ["Add a second step to signing in to <strong>{{ user.email }}</strong> for extra protection."],
     button("Set it up", "{{ url }}") + link_fallback(), ""),
    ("mfa-code", "MFA Verification Code", "Your Brassworks sign-in code",
     "A code to type into the sign-in page (paid Auth0 feature).",
     "Your sign-in code",
     ["Enter this code to finish signing in as <strong>{{ user.email }}</strong>:"],
     code_box(), ""),
    ("user-invitation", "User Invitation", "You've been invited to Brassworks",
     "Used for Auth0 Organizations invitations, which this setup does not use; included so nothing says Auth0.",
     "You've been invited",
     ["You've been invited to join Brassworks. Use the button to accept."],
     button("Accept the invitation", "{{ url }}") + link_fallback(), ""),
    ("passwordless-otp", "Passwordless OTP (set under Authentication > Passwordless)", "Your Brassworks sign-in code",
     "Not used unless you turn on passwordless email sign-in; configured on its own page.",
     "Your sign-in code",
     ["Enter this code to sign in:"],
     code_box(), "You received this because someone asked to sign in to Brassworks with this email address."),
]


def main():
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    readme = ["# Brassworks email templates for Auth0", "",
              "Generated by `build.py` - edit that and re-run, don't hand-edit these. Paste each file's contents into",
              "Auth0 (Branding -> Email Templates -> the template -> Message) and set its Subject and From as below.",
              "", f"**From:** `{BRAND} <your sending address>` (set the same on every template).", "",
              "| # | Auth0 template | File | Subject | Notes |", "|---|---|---|---|---|"]
    for i, (name, auth0, subject, note, heading, paragraphs, extra, why) in enumerate(TEMPLATES, 1):
        fname = f"{i:02d}-{name}.html"
        (out / fname).write_text(layout(heading, paragraphs, extra, why))
        readme.append(f"| {i} | {auth0} | `{fname}` | {subject} | {note} |")
    readme += ["", "Placeholders used: `{{ url }}`, `{{ code }}`, `{{ user.email }}`. After pasting, use the editor's",
               "Preview; if a placeholder shows up blank, replace it with what that template's own default uses."]
    (out / "README.md").write_text("\n".join(readme) + "\n")
    print(f"wrote {len(TEMPLATES)} templates to {out}")


if __name__ == "__main__":
    main()
