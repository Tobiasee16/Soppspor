-- Soppspor: per-user storage for personal finds.
--
-- Target: Supabase (Postgres 15+ with PostGIS). Apply with
--   psql "$SUPABASE_DB_URL" -f backend/schema.sql
-- or paste into the SQL editor. It is idempotent; running it twice is safe.
--
-- ---------------------------------------------------------------------------
-- WHAT THIS STORES, AND WHY THAT MATTERS
--
-- A row here is a place a named person goes to pick mushrooms. That is not
-- ordinary app telemetry: it is precise location history tied to an identity,
-- it is the one thing in this product users would be upset to leak, and under
-- GDPR it is personal data with the user as the data subject. Three things
-- follow, and they are enforced below rather than left to the client:
--
--   1. Row-level security is on, and the only policy is "your own rows". The
--      service key bypasses RLS, so nothing server-side may use it to serve
--      user requests.
--   2. Deletes are real. `deleted_at` exists to let other devices learn a spot
--      is gone, not to keep it; `purge_tombstones()` removes the row for good
--      and is meant to run on a schedule.
--   3. No column here is needed for analytics and none is collected for it.
--      If that changes it needs a migration and a conversation, not a quiet
--      ALTER TABLE.
--
-- ---------------------------------------------------------------------------
-- CRS
--
-- CLAUDE.md requires EPSG:25833 everywhere in the *analysis* pipeline, and
-- that stands. User storage is deliberately different: coordinates land here
-- as WGS84 lon/lat, converted once at the client boundary, because UTM zone 33
-- stops being the right answer the moment anyone uses this west of Bergen or
-- outside Norway, and a stored coordinate outlives the zone it was chosen in.
-- Converting at the storage boundary is an egress point, not a mid-pipeline
-- reprojection.

-- No PostGIS. A spots table looks like it wants a geometry column and this one
-- had one; nothing ever read it. Every spatial question this app asks - what is
-- near me, how far, which way - is asked on the device, which already holds all
-- of that user's spots. See backend/migrations/001_drop_unused_postgis.sql.
create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- spots

create table if not exists public.spots (
    -- Generated on the client so a spot can be created with no signal and keep
    -- its identity when it eventually syncs. A server-assigned id would mean
    -- either blocking the save or renumbering it later.
    id            uuid primary key,
    user_id       uuid not null default auth.uid()
                      references auth.users (id) on delete cascade,

    lon           double precision not null check (lon between -180 and 180),
    lat           double precision not null check (lat between -90 and 90),
    -- Which species the map was showing when the spot was saved. Free text on
    -- purpose: the app must keep working when the species list changes, and a
    -- foreign key to a model artefact would make user data hostage to a refit.
    species_key   text,
    species_name  text,

    found_on      date not null default current_date,
    note          text not null default '',

    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    -- Tombstone. A deleted spot has to stay visible to the sync protocol long
    -- enough for every other device to hear about it; purge_tombstones() then
    -- removes it properly.
    deleted_at    timestamptz,

    constraint note_length check (char_length(note) <= 2000)
);

comment on table public.spots is
    'Personal foraging locations. Private to one user; never aggregated or shared.';

-- Delta sync reads "everything of mine that changed since X", so this is the
-- index that matters. Ordinary map reads are small enough not to need one.
create index if not exists spots_user_updated_idx
    on public.spots (user_id, updated_at desc);

-- updated_at is the merge key for last-write-wins, so the client must not be
-- able to set it. A device with a wrong clock could otherwise pin a stale row
-- permanently ahead of every correction.
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    new.user_id := coalesce(old.user_id, new.user_id);
    return new;
end;
$$;

drop trigger if exists spots_touch_updated_at on public.spots;
create trigger spots_touch_updated_at
    before insert or update on public.spots
    for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- row-level security

alter table public.spots enable row level security;
-- Belt and braces: without this a table owner still reads everything.
alter table public.spots force row level security;

drop policy if exists spots_select_own on public.spots;
create policy spots_select_own on public.spots
    for select using (user_id = auth.uid());

drop policy if exists spots_insert_own on public.spots;
create policy spots_insert_own on public.spots
    for insert with check (user_id = auth.uid());

drop policy if exists spots_update_own on public.spots;
create policy spots_update_own on public.spots
    for update using (user_id = auth.uid())
             with check (user_id = auth.uid());

drop policy if exists spots_delete_own on public.spots;
create policy spots_delete_own on public.spots
    for delete using (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- housekeeping

-- Tombstones are a sync mechanism, not an archive. Thirty days is far longer
-- than any plausible offline stretch and well short of keeping deleted
-- personal data around. Schedule it:
--   select cron.schedule('purge-spot-tombstones', '17 3 * * *',
--                        $$select public.purge_tombstones()$$);
create or replace function public.purge_tombstones(older_than interval default '30 days')
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    n integer;
begin
    delete from public.spots
     where deleted_at is not null
       and deleted_at < now() - older_than;
    get diagnostics n = row_count;
    return n;
end;
$$;

revoke all on function public.purge_tombstones(interval) from public, anon, authenticated;

-- Everything a user has, in one call, for the export/erasure rights they have
-- by law and for the "copy my spots" button that already exists in the app.
create or replace function public.export_my_spots()
returns jsonb
language sql
security invoker
stable
as $$
    select coalesce(jsonb_agg(to_jsonb(s) - 'user_id'), '[]'::jsonb)
      from public.spots s
     where s.user_id = auth.uid()
       and s.deleted_at is null;
$$;

grant execute on function public.export_my_spots() to authenticated;

-- Erasure, as a button rather than as an email to us.
--
-- Spots are stored as readable rows and the protection is row-level security
-- plus a policy of not looking at them. That is a deliberate choice, and it
-- puts the weight on the two things that make such a promise credible to
-- somebody who cannot audit it: saying plainly what is held, and letting them
-- take all of it back unilaterally.
--
-- Deleting the rows is already possible from the client. Deleting the *account*
-- is not - an `auth.users` row needs privileges an anon key does not have - so
-- this is the one privileged function an ordinary user may call. It takes no
-- arguments, which is what makes it safe: there is no value a caller could
-- supply that would name somebody else's account.
create or replace function public.delete_my_account()
returns void
language plpgsql
security definer
set search_path = public, auth
as $$
declare
    me uuid := auth.uid();
begin
    if me is null then
        raise exception 'not signed in';
    end if;
    -- Erasure, not the tombstone `deleted_at` provides for sync: there is no
    -- other device left to tell.
    delete from public.spots where user_id = me;
    delete from auth.users where id = me;
end;
$$;

revoke all on function public.delete_my_account() from public, anon;
grant execute on function public.delete_my_account() to authenticated;

-- ---------------------------------------------------------------------------
-- delta sync
--
-- The client sends every row it changed while offline and asks for everything
-- that changed elsewhere since its last successful pull. Conflicts resolve
-- last-write-wins on updated_at, which is the right trade here: a conflict
-- means the same person edited the same note on two devices, and the loss is
-- one edit rather than one spot. Merging note text would be worse than the
-- problem.

create or replace function public.pull_spots(since timestamptz default 'epoch')
returns setof public.spots
language sql
security invoker
stable
as $$
    select *
      from public.spots
     where user_id = auth.uid()
       and updated_at > since
     order by updated_at asc
     limit 5000;
$$;

grant execute on function public.pull_spots(timestamptz) to authenticated;
