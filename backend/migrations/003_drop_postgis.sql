-- Drop PostGIS. Nothing uses it, and it is what puts an unprotected table in
-- the public schema.
--
-- Supabase's linter reports `RLS Disabled in Public` for
-- `public.spatial_ref_sys`. That table is PostGIS's own: about 8,500 rows of
-- EPSG coordinate-system definitions, the same ones printed in any projection
-- reference. It holds nothing about anybody, so the finding is not a leak of
-- user data - `public.spots` is the table that matters there, and it has RLS
-- enabled with own-row policies, which is why the linter does not flag it.
--
-- It is still worth fixing at the root rather than silencing, because the
-- table cannot be secured in place:
--
--   * `alter table public.spatial_ref_sys enable row level security` fails
--     with `must be owner of table spatial_ref_sys`. The table belongs to the
--     extension, and the project's `postgres` role is not a superuser on
--     Supabase. There is no permission to grant yourself here.
--   * Moving the extension to a non-exposed schema means dropping and
--     recreating it, which is this migration plus an extra step.
--
-- So the honest fix for a dependency nobody uses is to remove the dependency.
--
-- migrations/001 dropped `spots.geom`, the generated geography column that was
-- the only thing in this schema ever tied to PostGIS, and said:
--
--     If this is the only thing that ever used it, `drop extension postgis;`
--     is safe to run separately once you have checked.
--
-- Checked, 2026-09-14. `backend/schema.sql` and every migration contain no
-- geometry or geography column, no `st_*` call, and no `postgis.*` reference;
-- the five functions (`touch_updated_at`, `purge_tombstones`,
-- `export_my_spots`, `delete_my_account`, `pull_spots`) are plain SQL over
-- `lon`/`lat` doubles. Every spatial question this app asks - what is near me,
-- how far, which way - is answered on the device, which already holds all of
-- that user's spots.
--
--   Run in the Supabase SQL editor, or:
--     psql "$DATABASE_URL" -f backend/migrations/003_drop_postgis.sql
--
-- Safe to run twice: `if exists` makes a second run a no-op.
--
-- **Do not add `cascade`.** Plain `drop extension` refuses if anything still
-- depends on PostGIS, which is the point - a loud failure means the check
-- above missed something and is worth reading. `cascade` would drop whatever
-- that something is instead of telling you about it.

begin;

drop extension if exists postgis;

commit;

-- Verify:
--   select extname from pg_extension where extname = 'postgis';
--   -- expect: no rows
--
--   select to_regclass('public.spatial_ref_sys');
--   -- expect: null
--
--   select tablename from pg_tables
--    where schemaname = 'public' and rowsecurity = false;
--   -- expect: no rows
--
-- The saved spots are untouched either way; `spots` has no PostGIS column to
-- lose. Confirm with:
--   select count(*) from public.spots;
--
-- If it is ever needed again, `create extension postgis;` brings it back - and
-- brings the linter finding back with it, so put it in a dedicated schema
-- then: `create extension postgis schema extensions;`
