-- Drop the PostGIS column and index. Nothing reads them.
--
-- 2026-09-12. `spots.geom` was added as a generated `geography(Point, 4326)`
-- with a GiST index, on the assumption that a spots table would eventually be
-- queried spatially. It never was and it never will be: the client holds every
-- one of its own spots in memory, so proximity, sorting and "what is near me"
-- are all answered on the device. `export_my_spots()` even strips the column
-- explicitly (`to_jsonb(s) - 'geom'`), which is the clearest possible sign it
-- was never wanted on the way out either.
--
-- Two reasons to remove it rather than leave it sitting there.
--
-- It costs on every write. A generated stored column plus a GiST index is
-- recomputed and reindexed on every insert and every edit of a note, for a
-- read path that does not exist.
--
-- And it is the only part of this schema that ties it to PostGIS specifically.
-- Everything else here - row-level security, `auth.uid()`, the trigger, the
-- RPCs - is Supabase or plain Postgres. Keeping an unused spatial extension in
-- the dependency list makes the schema look harder to move than it is, and the
-- one thing worth preserving about a backend choice is the ability to leave it.
--
-- Safe to run on a live table: a generated column holds no independent data,
-- so dropping it loses nothing that is not recomputed from lon and lat.
--
--   Run in the Supabase SQL editor, or:
--     psql "$DATABASE_URL" -f backend/migrations/001_drop_unused_postgis.sql

begin;

drop index if exists public.spots_geom_idx;
alter table public.spots drop column if exists geom;

-- Left installed rather than dropped. `postgis` may be in use by something
-- else in the project now or later, and dropping an extension is a much
-- bigger claim than dropping a column nobody reads. If this is the only thing
-- that ever used it, `drop extension postgis;` is safe to run separately once
-- you have checked.

commit;

-- Verify:
--   select column_name from information_schema.columns
--    where table_schema = 'public' and table_name = 'spots';
--   -- expect: id, user_id, lon, lat, species_key, species_name,
--   --         found_on, note, created_at, updated_at, deleted_at
