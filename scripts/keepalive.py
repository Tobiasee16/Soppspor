"""Keep the Supabase project from pausing itself out of existence.

    python scripts/keepalive.py

Supabase pauses a free-tier project after **7 days with no database activity**.
Restoring it is a manual click in a dashboard, and until someone makes that
click nobody can sign in or sync. Nothing warns the users; the app just stops
being able to save anything.

For most projects that is a non-event, because most projects are used every
week. This one is seasonal. Mushroom season in Agder runs roughly August to
October; from November to July there may be no traffic at all for months. The
failure that follows is the bad kind: it happens while nobody is looking, and
it surfaces on the first warm wet weekend of the next season, to a user who has
just been told their spots are safely synced.

So this makes one trivial authenticated read, once, and reports what it found.
Run it weekly from anywhere that is reliably awake - a GitHub Actions schedule,
a Cloudflare Worker cron, or a machine that is on. See backend/README.md.

It uses the anon key and reads nothing: row-level security means an anonymous
caller selecting from `spots` gets an empty set back, which is a real query
against the real database and is exactly as much as is needed to count as
activity. No service-role key is involved and none should be.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "supabase.json"
TIMEOUT = 30


def main() -> int:
    # Environment first, so this runs on a scheduler that has no checkout of
    # config/supabase.json - which is per-developer and stays out of git.
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    anon = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not (url and anon):
        if not CONFIG.exists():
            print(f"no SUPABASE_URL/SUPABASE_ANON_KEY and no "
                  f"{CONFIG.relative_to(ROOT)} - nothing to keep alive")
            return 0
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        url, anon = (cfg.get("url") or "").strip(), (cfg.get("anon") or "").strip()
    if not url or not anon:
        raise SystemExit("config/supabase.json has no url/anon key")
    if anon.startswith("sb_secret_") or ".service_role." in anon:
        raise SystemExit(
            "REFUSING: that looks like a service_role key. This only ever needs "
            "the anon key - it is a liveness ping, not an administrative task.")

    # `limit=1` on a table RLS will return nothing from. The point is that the
    # query reaches Postgres, not that it comes back with anything.
    req = urllib.request.Request(
        url.rstrip("/") + "/rest/v1/spots?select=id&limit=1",
        headers={"apikey": anon,
                 "Authorization": f"Bearer {anon}",
                 "Accept": "application/json",
                 "User-Agent": "soppspor-keepalive"})
    started = dt.datetime.now(dt.timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(400).decode("utf-8", "replace")
            ms = (dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000
            print(f"{started.isoformat(timespec='seconds')}  HTTP {r.status}  "
                  f"{ms:.0f} ms  {body[:80]}")
            return 0
    except urllib.error.HTTPError as exc:
        # 401 still proves the project is awake and answering, which is the
        # thing being checked. Anything else is worth failing a scheduled run
        # over, because a paused project is what this exists to prevent.
        detail = exc.read(300).decode("utf-8", "replace")
        if exc.code in (401, 403):
            print(f"HTTP {exc.code} - project is awake (auth rejected, which is "
                  f"expected for an anonymous read): {detail[:120]}")
            return 0
        print(f"HTTP {exc.code} from {url}: {detail[:200]}", file=sys.stderr)
        return 1
    except Exception as exc:                       # noqa: BLE001 - report anything
        print(f"could not reach {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("If this persists the project may have paused. Restore it from "
              "the Supabase dashboard.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
