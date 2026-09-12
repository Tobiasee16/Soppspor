-- Both pending migrations, in one paste.
--
-- Open the SQL editor, paste the whole of this file, press Run:
--   https://supabase.com/dashboard/project/_/sql/new
--
-- Safe to run twice. Everything here is `if exists` or `create or replace`, so
-- a second run changes nothing and reports the same result.
--
-- What it does:
--   001  drops an unused PostGIS column that costs a write on every save
--   002  adds delete_my_account(), so the app's delete button actually works
--
-- Neither touches anybody's spots except to give their owner a way to remove
-- them. Your two test accounts keep whatever they have.

begin;

-- ---------------------------------------------------------------------------
-- 001: drop the unused spatial column
--
-- `spots.geom` was a generated geography column with a GiST index, on the
-- assumption a spots table would eventually be queried spatially. It never was:
-- the app holds every one of its own spots in memory, so "what is near me" is
-- answered on the phone. export_my_spots() even stripped the column on the way
-- out. It cost a recomputation and a reindex on every single save, for a read
-- path that does not exist.
-- ---------------------------------------------------------------------------

drop index if exists public.spots_geom_idx;
alter table public.spots drop column if exists geom;

-- export_my_spots() no longer needs to strip a column that is gone.
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

-- ---------------------------------------------------------------------------
-- 002: the right to erasure, as a button
--
-- Deleting rows is already possible from the app. Deleting the *account* is
-- not - an auth.users row needs privileges an anon key does not have - so this
-- is the one privileged function an ordinary user may call.
--
-- It takes no arguments. That is what makes it safe: there is no value a caller
-- could send that would name somebody else's account.
-- ---------------------------------------------------------------------------

create or replace function public.delete_my_account()
returns void
language plpgsql
security definer
-- Pinned. A definer function runs as the owner, and an attacker-supplied
-- search_path would otherwise get to decide which table "users" means.
set search_path = public, auth
as $$
declare
    me uuid := auth.uid();
begin
    -- An anonymous caller has no uid. Without this the deletes below would run
    -- with a null comparison and quietly match nothing - or, if the predicate
    -- were ever written differently, match everything.
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

commit;


-- ---------------------------------------------------------------------------
-- Verify. Run this separately afterwards; it changes nothing.
-- ---------------------------------------------------------------------------
--
-- select column_name
--   from information_schema.columns
--  where table_schema = 'public' and table_name = 'spots'
--  order by ordinal_position;
--
--   expect exactly: id, user_id, lon, lat, species_key, species_name,
--                   found_on, note, created_at, updated_at, deleted_at
--   and NO geom
--
-- select p.proname, p.prosecdef as security_definer,
--        array_agg(a.rolname) filter (where has_function_privilege(
--            a.rolname, p.oid, 'execute')) as may_execute
--   from pg_proc p
--   join pg_namespace n on n.oid = p.pronamespace
--   cross join (select rolname from pg_roles
--                where rolname in ('anon','authenticated','service_role')) a
--  where n.nspname = 'public' and p.proname = 'delete_my_account'
--  group by p.proname, p.prosecdef;
--
--   expect: security_definer = true, and may_execute containing
--           'authenticated' and 'service_role' but NOT 'anon'
