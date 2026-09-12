"""Does account recovery actually work? One command, one honest answer.

    python scripts/check_auth_email.py you@example.no

Account recovery is built and tested, but it is only as good as the mail that
carries it, and mail fails quietly. Supabase's built-in sender does **2
messages an hour, only to addresses belonging to your own team** - it is a
development convenience rather than a mail service, and no setting raises it.
The failure arrives as an HTTP 429 that the app can only report, not fix.

This sends one real magic link to an address you control and says exactly what
happened: sent, rate limited, rejected, or refused by your SMTP provider. Run
it once before configuring custom SMTP and once after, and the difference is
the answer.

Configure SMTP first at:
    Supabase -> Project Settings -> Authentication -> SMTP Settings

Any transactional provider works. Resend's free tier is 3,000 messages a month:
    host smtp.resend.com   port 465   user resend   pass <your API key>

**Every reputable provider needs a verified sending domain first**, for the
obvious reason - otherwise anyone could send mail claiming to be you. To try
the flow before owning a domain, use Resend's `onboarding@resend.dev` sender,
which delivers only to the address on your own Resend account. That is enough
to prove the link works end to end and deliberately not enough for anyone else.
Brevo is the alternative that verifies a single address rather than a domain.

Also check, because a link that sends but lands nowhere looks identical to
success from here:
    Authentication -> URL Configuration -> Site URL and Redirect URLs
must contain the address you actually open the app on.
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "config" / "supabase.json"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def call(url, key, path, method="GET", body=None, token=None):
    h = {"apikey": key, "Content-Type": "application/json",
         "Authorization": "Bearer " + (token or key)}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:300]


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python scripts/check_auth_email.py you@example.no\n"
                         "Use a real inbox you can open; example.com is rejected.")
    addr = sys.argv[1].strip()
    if "@" not in addr or addr.lower().endswith(("@example.com", "@example.no")):
        raise SystemExit(f"{addr!r} will be rejected. Use a real address you can read.")

    if not CFG_PATH.exists():
        raise SystemExit("config/supabase.json is missing - see backend/README.md")
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    url, key = cfg["url"].rstrip("/"), cfg["anon"]

    print(f"project: {url}")
    s, settings = call(url, key, "/auth/v1/settings")
    if s != 200 or not isinstance(settings, dict):
        raise SystemExit(f"cannot reach the auth service: HTTP {s} {settings}")
    ext = {k for k, v in (settings.get("external") or {}).items() if v}
    print(f"providers enabled: {', '.join(sorted(ext)) or 'none'}")
    if "email" not in ext:
        print("  WARNING: the email provider is off; recovery cannot work at all")
    if "anonymous_users" not in ext:
        print("  WARNING: anonymous sign-in is off; first-run saving will fail")
    print(f"email confirmation required: {not settings.get('mailer_autoconfirm')}")

    print(f"\nsending a sign-in link to {addr} ...", flush=True)
    s, j = call(url, key, "/auth/v1/otp", "POST",
                {"email": addr, "create_user": True})
    code = (j or {}).get("error_code") if isinstance(j, dict) else None

    print()
    if s == 200:
        print("SENT.  Open the link on the device you want signed in.")
        print("If nothing arrives within a few minutes, the cause is almost always")
        print("one of: the message went to spam, SMTP credentials are wrong, or the")
        print("sending domain is unverified with your provider.")
        return 0
    if code == "over_email_send_rate_limit":
        print("RATE LIMITED.  This is Supabase's built-in sender, not a bug in the app.")
        print("It allows only a few messages an hour, so recovery will fail for real")
        print("users. Configure custom SMTP - see the top of this file - and re-run.")
        return 1
    if code == "email_address_invalid":
        print(f"REJECTED.  Supabase considers {addr!r} invalid. Reserved domains such")
        print("as example.com are refused; use a real inbox.")
        return 1
    if code == "otp_disabled":
        print("REFUSED.  Email sign-in is disabled for this project.")
        print("Authentication -> Providers -> Email, and enable it.")
        return 1
    msg = (j or {}).get("msg") if isinstance(j, dict) else j
    print(f"FAILED.  HTTP {s}: {msg}")
    print("If this mentions SMTP, the provider rejected the message - check the")
    print("credentials and that the sending domain is verified.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
