# Why the backend stays on Supabase

Decided 2026-09-12, after being asked whether something better exists. Written
down because "should we move off X" is a question that comes back every few
months and is expensive to answer twice.

**Conclusion: stay.** The thing that is currently broken is not a Supabase
problem, and the thing Supabase does best is the thing this app can least
afford to get wrong.

## What was actually broken

Account recovery does not work, because Supabase's built-in mailer returns 429.
That reads like a platform limitation and it is not. The built-in sender does
**2 messages an hour, and only to addresses belonging to your own team** - it
is a development convenience rather than a mail service, and no setting raises
it. Every provider expects you to bring your own SMTP for real traffic. It is a
configuration line, not an architecture.

No alternative avoids it. Any backend that sends a magic link needs an email
provider. Moving platforms to fix an email problem would be moving for the one
reason that does not depend on the platform.

## What Supabase is actually providing

One thing above all: **row-level security**. The privacy guarantee in this
project is not a policy, it is a database rule:

```sql
create policy spots_select_own on public.spots
    for select using (user_id = auth.uid());
```

Nothing in the client can bypass it, no forgotten `where` clause can leak past
it, and it has been verified against the live project with 22 isolation checks
covering read, update, delete and ownership forgery between two real users.

Every alternative below replaces that with authorization code we would write
ourselves. Hand-rolled authorization is the single worst thing to get wrong in
this codebase - the failure mode is publishing people's secret mushroom spots
to each other, which the project's own values documents describe as destroying
its reason to exist. Trading a verified database rule for our own middleware,
to solve an email problem, is a bad trade at any price.

Also in use and worth keeping: anonymous auth (the app works before anyone
signs up, which CLAUDE.md requires), PostgREST (no server code at all), and a
free tier of 50,000 monthly active users - far beyond where this is.

## What was considered

| option | why not |
|---|---|
| **Cloudflare D1 + Workers** | Same company as the hosting, generous free tier, and genuinely appealing. But it has no auth and no row-level security, so the isolation guarantee becomes our own Worker code. That is the exact swap described above. |
| **PocketBase, self-hosted** | Single binary, SQLite, full control, EU hosting trivial, about €4/month. The cost is that two people now operate a server: patching, backups, uptime, and being the ones who get paged. |
| **Appwrite Cloud** | Comparable model with an EU region. A lateral move - different vendor, same shape of dependency, and we would re-verify the isolation guarantees from scratch. |
| **Firebase** | US-centric, and a values mismatch for an app whose selling point is that your spots are nobody's business. |
| **No backend at all** | Would mean no sync. The team explicitly chose "sync on once signed in" over local-only, so this is settled. |

None of them is better *for this problem*. Several would be defensible if the
problem were different.

## The three real weaknesses, and what was done

### 1. The free tier pauses after 7 days of inactivity — fixed

A free project with no database activity for a week is paused, and restoring it
is a manual click in a dashboard. For most projects that never happens. **This
one is seasonal**: busy August to October, potentially silent from November to
July. The pause would land in February and surface on the first warm wet
weekend of the next season, to a user who had been told their spots were safely
synced. Nothing warns anybody; saving just stops working.

`scripts/keepalive.py` makes one authenticated read, and
`.github/workflows/keepalive.yml` runs it twice a week — every three to four
days against a seven-day limit, so one failed run cannot cause a pause. It uses
the anon key and reads nothing: under row-level security an anonymous select
returns an empty set, which is a real query and is all that "activity" requires.

### 2. The project region cannot be changed later — checked, and it is fine

**`eu-west-1` (Ireland), checked 2026-09-12.** Inside the EU, so there is
nothing to migrate and nothing to decide. Round trip from Norway is around
30 ms.

Supabase fixes a region at creation and changing it means moving every row and
every account by hand, so this was worth checking while the cost of being wrong
was two test accounts rather than a user base. It was not wrong. If the project
is ever recreated, keep it in `eu-west-1`, `eu-central-1` or `eu-west-3`, and
note that the picker's **Europe** grouping also contains London and Zurich,
which have adequacy decisions but are not in the EU.

Region is a data-location control, not GDPR compliance by itself: a signed DPA
and a privacy policy are separate work. The deletion path is now done.

### 3. An unused PostGIS dependency — removed

`spots.geom` was a generated `geography(Point, 4326)` column with a GiST index,
on the assumption that a spots table would be queried spatially. It never was.
Every spatial question the app asks - what is near me, how far, which way - is
answered on the device, which already holds all of that user's spots.
`export_my_spots()` even stripped the column explicitly on the way out.

It cost a recomputation and a reindex on every write for a read path that does
not exist, and it was the only part of the schema tied to PostGIS specifically.
Removed in `migrations/001_drop_unused_postgis.sql`. The one thing worth
preserving about a backend choice is the ability to leave it.

## End-to-end encryption: proposed, and declined

Recorded because it is the kind of question that comes back, and the answer is
settled.

The privacy promise in CLAUDE.md is absolute: saved spots are never our data,
"not aggregated, not de-identified, not opt-in". Row-level security enforces
that **no other user can read your spots** — a real guarantee, verified against
the live project. It does not mean *we* cannot: the rows are readable, so
anyone with database access could read every saved spot in the country.

End-to-end encryption is the stronger form — "we cannot look" rather than "we
will not look" — and it needs no platform change. It also has a cost that
cannot be negotiated away: encryption and "recover on a new phone with just
your email" are mutually exclusive, because the whole point is that the server
cannot help you. Recovery would have to come from a code the user saves once,
and losing it would destroy a season of finds with nobody able to undo it.

Put to the team on 2026-09-12, with that trade stated plainly. The decision:

> "I think its fine if we have access to the users data as we follow our 'we
> dont look' thing if that makes it possible to save data in DBs."

**So spots stay readable and email recovery stays possible.** Do not re-propose
encryption unless the team raises it.

What that choice obliges instead, and what was built because of it:

- **The app says so.** The privacy section in Innstillinger states in plain
  Norwegian that the spots are *not* encrypted against us, that we can
  technically read them, and that we do not. A promise the user cannot verify
  should at least be one they can read.
- **The app can undo it.** `delete_my_account()` removes every row and the
  account itself, behind a typed confirmation, and wipes the device even when
  the server cannot be reached. A "we don't look" promise with no delete button
  is just a promise.

Both are live and both are covered by tests — `tests/test_erasure.js` for the
client, `scripts/check_backend.py` for the server.

## Still outstanding, unchanged by any of the above

- **Custom SMTP.** The built-in mailer does 2 messages an hour and only to your
  own team, so it was never a rate limit to raise. Every real provider needs a
  verified sending domain first. See `FIXING_SUPABASE.md` step 4.
- **Site URL and Redirect URLs.** The app now sends its own origin with each
  auth request, so the allow-list does the deciding — add each origin you
  actually open the app on.
- **Schedule `purge_tombstones()`** so deleted spots do not accumulate.
- ~~Privacy policy and a delete button.~~ Both built; see above. A published
  policy page for the deployed site is still separate work from the in-app text.
- ~~Delete the two anonymous test users.~~ Done, and it cannot recur:
  `check_backend.py` now erases the users it creates, on every run.
