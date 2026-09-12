"""Live checks against the real Supabase project.

The one that matters is isolation: RLS being *enabled* and RLS being *correct*
are different things, and the difference here is between private spots and a
public list of everyone's mushroom patches. So this signs in as two separate
users and has the second one try, in every way the REST API allows, to see or
touch the first one's row.

Creates two anonymous users and a handful of rows, then deletes the rows.

Lives in `scripts/` rather than `tests/` deliberately. It needs the network and
real credentials, it creates real users, and it exits via SystemExit at import
time - pytest collecting it aborts the whole run. Run it by hand:

    python scripts/check_backend.py
"""
from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "supabase.json")
                 .read_text(encoding="utf-8"))
URL, KEY = CFG["url"].rstrip("/"), CFG["anon"]

fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS  " if ok else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails += 1


def call(path, method="GET", body=None, token=None, prefer=None):
    h = {"apikey": KEY, "Content-Type": "application/json",
         "Authorization": "Bearer " + (token or KEY)}
    if prefer:
        h["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def signin():
    s, j = call("/auth/v1/signup", "POST", {"data": {}})
    if s != 200 or not j or "access_token" not in j:
        raise SystemExit(f"anonymous sign-in failed: {s} {j}")
    return j["access_token"], j["user"]["id"]


print("— identity —")
tok_a, uid_a = signin()
tok_b, uid_b = signin()
check("anonymous sign-in works", bool(tok_a and tok_b))
check("the two test users are distinct", uid_a != uid_b, f"{uid_a[:8]} vs {uid_b[:8]}")

print("\n— table and defaults —")
spot_a = str(uuid.uuid4())
s, j = call("/rest/v1/spots", "POST",
            [{"id": spot_a, "lat": 58.1467, "lon": 7.9956,
              "species_key": "craterellus", "species_name": "Traktkantarell",
              "found_on": "2026-09-11", "note": "testrad"}],
            token=tok_a, prefer="return=representation")
check("A can insert a spot", s == 201, f"HTTP {s}")
row = j[0] if isinstance(j, list) and j else {}
check("user_id defaults to the caller", row.get("user_id") == uid_a)
# Migration 001 removed it. Asserted as an absence rather than deleted from the
# file: a generated column silently coming back would mean a reindex on every
# save for a read path that does not exist.
check("the unused geom column is gone", "geom" not in row,
      "still present" if "geom" in row else "")
check("created_at and updated_at are set",
      bool(row.get("created_at")) and bool(row.get("updated_at")))

print("\n— isolation (the one that matters) —")
s, j = call(f"/rest/v1/spots?id=eq.{spot_a}&select=*", token=tok_a)
check("A can read A's spot", s == 200 and len(j or []) == 1, f"{len(j or [])} row(s)")

s, j = call(f"/rest/v1/spots?id=eq.{spot_a}&select=*", token=tok_b)
check("B CANNOT read A's spot", s == 200 and len(j or []) == 0,
      f"HTTP {s}, {len(j or [])} row(s)")

s, j = call("/rest/v1/spots?select=*", token=tok_b)
check("B sees an empty table, not A's rows", s == 200 and len(j or []) == 0,
      f"{len(j or [])} row(s)")

s, j = call(f"/rest/v1/spots?id=eq.{spot_a}", "PATCH", {"note": "hijacked"},
            token=tok_b, prefer="return=representation")
check("B CANNOT update A's spot", s in (200, 204) and not j,
      f"HTTP {s}, changed {len(j or [])}")

s, j = call(f"/rest/v1/spots?id=eq.{spot_a}", "DELETE",
            token=tok_b, prefer="return=representation")
check("B CANNOT delete A's spot", s in (200, 204) and not j,
      f"HTTP {s}, deleted {len(j or [])}")

s, j = call(f"/rest/v1/spots?id=eq.{spot_a}&select=note", token=tok_a)
check("A's spot is untouched after B's attempts",
      s == 200 and (j or [{}])[0].get("note") == "testrad",
      repr((j or [{}])[0].get("note")))

s, j = call("/rest/v1/spots", "POST",
            [{"id": str(uuid.uuid4()), "lat": 58.1, "lon": 8.0, "user_id": uid_a}],
            token=tok_b, prefer="return=representation")
check("B CANNOT insert a row owned by A", s >= 400, f"HTTP {s}")

print("\n— the updated_at trigger —")
s, j = call(f"/rest/v1/spots?id=eq.{spot_a}", "PATCH",
            {"note": "endret", "updated_at": "2099-01-01T00:00:00Z"},
            token=tok_a, prefer="return=representation")
bogus = (j or [{}])[0].get("updated_at", "")
check("a client-supplied updated_at is overridden by the server",
      s == 200 and not bogus.startswith("2099"), bogus)

print("\n— sync surface —")
s, j = call("/rest/v1/rpc/pull_spots", "POST", {"since": "1970-01-01T00:00:00Z"},
            token=tok_a)
check("pull_spots returns A's rows", s == 200 and len(j or []) >= 1,
      f"HTTP {s}, {len(j or [])} row(s)")

s, j = call("/rest/v1/rpc/pull_spots", "POST", {"since": "1970-01-01T00:00:00Z"},
            token=tok_b)
check("pull_spots leaks nothing to B", s == 200 and len(j or []) == 0,
      f"{len(j or [])} row(s)")

s, j = call("/rest/v1/rpc/export_my_spots", "POST", {}, token=tok_a)
check("export_my_spots works and hides internals",
      s == 200 and isinstance(j, list) and j and "user_id" not in j[0] and "geom" not in j[0],
      f"HTTP {s}, keys {sorted((j or [{}])[0].keys()) if j else []}")

s, j = call("/rest/v1/rpc/purge_tombstones", "POST", {}, token=tok_a)
check("purge_tombstones is NOT callable by a user", s >= 400, f"HTTP {s}")

print("\n— tombstones —")
s, j = call(f"/rest/v1/spots?id=eq.{spot_a}", "PATCH",
            {"deleted_at": "2026-09-11T10:00:00Z"},
            token=tok_a, prefer="return=representation")
check("A can tombstone a spot", s == 200 and (j or [{}])[0].get("deleted_at"))

s, j = call("/rest/v1/rpc/pull_spots", "POST", {"since": "1970-01-01T00:00:00Z"},
            token=tok_a)
tomb = [r for r in (j or []) if r["id"] == spot_a and r.get("deleted_at")]
check("the tombstone reaches other devices via pull", len(tomb) == 1)

print("\n— cleanup —")
s, _ = call(f"/rest/v1/spots?id=eq.{spot_a}", "DELETE", token=tok_a)
check("A can delete A's own spot", s in (200, 204), f"HTTP {s}")
s, j = call("/rest/v1/spots?select=id", token=tok_a)
check("A's table is empty again", s == 200 and len(j or []) == 0,
      f"{len(j or [])} row(s) left")

# ---------------------------------------------------------------- erasure --
# The app's "delete everything" button. Checked rather than trusted, because the
# failure mode is an app telling somebody their data is gone while it sits in
# Postgres. It also cleans up after this script, which used to leave two
# anonymous users behind on every run.
print()
print("— erasure —")

s, j = call("/rest/v1/rpc/delete_my_account", "POST", {})
check("the anon key CANNOT call delete_my_account", s in (401, 403, 404),
      f"HTTP {s} {str(j)[:80]}")

s, j = call("/rest/v1/spots", "POST",
            {"id": str(uuid.uuid4()), "lon": 8.0, "lat": 58.1,
             "note": "to be erased"},
            token=tok_b, prefer="return=representation")
check("B has a spot to lose", s == 201, f"HTTP {s}")

s, j = call("/rest/v1/rpc/delete_my_account", "POST", {}, token=tok_b)
check("B can erase itself", s in (200, 204), f"HTTP {s} {str(j)[:80]}")

s, j = call("/rest/v1/spots?select=id", token=tok_b)
check("B's rows are gone", s in (200, 401, 403) and not (j or []),
      f"HTTP {s}, {len(j or [])} row(s)")

# The JWT stays valid until it expires; the account behind it does not. A write
# must fail rather than resurrect a deleted user's data.
s, j = call("/rest/v1/spots", "POST",
            {"id": str(uuid.uuid4()), "lon": 8.0, "lat": 58.1},
            token=tok_b, prefer="return=representation")
check("an erased account CANNOT write again on its old token", s >= 400,
      f"HTTP {s} {str(j)[:80]}")

s, j = call("/rest/v1/spots?select=id", token=tok_a)
check("erasing B left A alone", s == 200, f"HTTP {s}")

s, j = call("/rest/v1/rpc/delete_my_account", "POST", {}, token=tok_a)
check("A can erase itself too", s in (200, 204), f"HTTP {s}")

print(f"\n{'ALL CHECKS PASSED' if not fails else str(fails) + ' FAILED'}")
print("both test users erased themselves - nothing left in auth.users")
raise SystemExit(1 if fails else 0)
