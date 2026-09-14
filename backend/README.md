# Backend — personal spot storage

> **Why Supabase and not something else:** see [DECISION.md](DECISION.md).
> It also records three things that needed fixing (a seasonal pause hazard, the
> region being unchangeable after creation, and an unused PostGIS column) and
> one recommended upgrade that has not been made.


One table, one job: hold each user's own foraging spots and sync them between
their devices. Nothing else in this project needs a server, and nothing else
should move here without a reason.

## Decisions, and why

**Supabase.** Managed Postgres with PostGIS, so spots are real geodata rather
than two float columns we later regret; auth and per-user isolation come with
it rather than being written by hand; and it is open source and self-hostable,
so choosing it now does not become a wall later.

**Anonymous identity first.** A spot saves on first launch with no account, no
email and no login wall — which is the whole point for someone who downloads
this in September and wants to mark a place ten minutes later. Supabase
anonymous sign-in issues a real `auth.users` row, so the rows are keyed
properly from the start; signing in with Apple or Google later *links* to that
same user and the spots follow. There is no import step and nothing to lose.

**Sync is on once there is an identity.** This was a deliberate choice against
the earlier "local by default" wording in CLAUDE.md, and that line has been
updated to match rather than left to contradict the build. The practical
consequence, which the app must state plainly to users: **spots leave the
device from first run.** Turning sync off has to remain possible in settings.

## What is stored, and what is not

| stored | not stored |
|---|---|
| longitude / latitude of the spot | anything about where the user went and did *not* save |
| which species the map was showing | any identification or edibility judgement |
| the date, and a free-text note | photographs (a later decision, not an omission) |
| created / updated timestamps | analytics, device fingerprints, ad identifiers |

A row here is precise location history tied to an identity. It is the one thing
in this product a user would be genuinely upset to leak. Three protections are
in the schema rather than in client code, where they could be forgotten:

1. **Row-level security is on and forced.** The only policy is `user_id =
   auth.uid()`. There is no "read all spots" path, not even for an admin
   screen. The service-role key bypasses RLS entirely, so it must never be
   used to serve a user request — keep it out of anything client-reachable.
2. **Deletes are real.** `deleted_at` exists only so other devices learn a spot
   is gone. `purge_tombstones()` removes the row permanently and is meant to be
   scheduled daily.
3. **Export and erasure are one call each** — `export_my_spots()`, and a
   `delete` the user can trigger. Both are rights under GDPR, and both are
   cheaper to build now than to retrofit.

## Setup

1. Create a project at supabase.com. Note the **Project URL** and the
   **anon/publishable key** — the anon key is safe in client code, which is
   the point of RLS. The **service key is not**; it never goes near the app.
2. Enable anonymous sign-ins: *Authentication → Providers → Anonymous*.
3. Apply the schema:
   ```
   psql "$SUPABASE_DB_URL" -f backend/schema.sql
   ```
   or paste it into the SQL editor. It is idempotent.
4. **Schedule the tombstone purge.** Paste
   `migrations/004_schedule_tombstone_purge.sql` into the SQL editor. It
   enables `pg_cron`, schedules the job, and carries the queries to verify it
   afterwards.

   This is not housekeeping any more. `web/personvern.html` tells the reader
   that a deleted spot is removed permanently within 30 days, and nothing
   removes it unless this job runs. **Do this before publishing the policy**,
   not after.
5. Give me the Project URL and anon key and I will wire the client sync layer
   to them. Until then the app keeps working exactly as it does now, entirely
   on the device.

## Sync protocol

Deliberately small, because the hard part of sync is the cases nobody tests.

- **Ids are generated on the client** (uuid v4), so a spot saved with no signal
  keeps its identity forever. No renumbering, no "pending" state.
- **Push**: upsert every locally-changed row.
- **Pull**: `pull_spots(since)` returns everything of yours changed after that
  timestamp, oldest first, capped at 5000.
- **Conflicts**: last-write-wins on `updated_at`, which the server sets via
  trigger so a device with a wrong clock cannot pin a stale row ahead of every
  correction. A conflict means one person edited the same note on two devices;
  losing one edit is the right trade against merging prose badly.
- **Deletes** propagate as tombstones and are purged after 30 days — far longer
  than any plausible offline stretch, far shorter than keeping deleted personal
  data around.

## Not here yet

- **Photos.** A storage bucket per user, with the same RLS shape. Worth doing,
  but it is the point where storage cost becomes real and where the app starts
  holding images of things people want identified — which runs straight into
  the constraint that this app never identifies a species. Decide deliberately.
- **Tile serving.** The map currently calls Kartverket's WMS directly from the
  client. That is fine for a handful of people and wrong for a product: no SLA,
  and their open terms are not an invitation to be someone's production tile
  backend. At scale this becomes self-hosted tiles behind a CDN, and it is the
  main infrastructure cost of scaling — not this table.
