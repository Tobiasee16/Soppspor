-- The right to erasure, as a button the user can actually press.
--
-- 2026-09-12. The team decided that spots are stored as readable rows and that
-- the protection is row-level security plus a policy of not looking, rather
-- than encryption. That is a legitimate choice, and it moves weight onto the
-- things that make such a promise credible: saying plainly what is held, and
-- letting somebody take it all back without asking us. A "we don't look"
-- promise with no delete button is just a promise.
--
-- Deleting rows is already possible from the client - `spots_delete_own` allows
-- it - but that leaves the account itself, and an `auth.users` row cannot be
-- removed with an anon key. Hence a definer function: the only privileged thing
-- in this schema that an ordinary user may call, restricted to exactly one row.
--
--   Run in the Supabase SQL editor, or:
--     psql "$DATABASE_URL" -f backend/migrations/002_delete_my_account.sql

begin;

create or replace function public.delete_my_account()
returns void
language plpgsql
security definer
-- Pinned, because a definer function runs as the owner and an attacker-supplied
-- search_path would otherwise decide which `users` table "users" means.
set search_path = public, auth
as $$
declare
    me uuid := auth.uid();
begin
    -- An anonymous caller has no uid. Without this the delete below would have
    -- no WHERE value and the function would be a very bad day.
    if me is null then
        raise exception 'not signed in';
    end if;

    -- Spots go first and go properly. This is erasure, not the tombstone that
    -- `deleted_at` provides for sync: there is no other device left to tell.
    delete from public.spots where user_id = me;

    -- The account itself. `spots.user_id references auth.users on delete
    -- cascade` would have taken the rows anyway; doing it explicitly above
    -- means the outcome does not depend on a foreign key staying as it is.
    delete from auth.users where id = me;
end;
$$;

-- `authenticated` only. An anonymous session on this project *is* authenticated
-- in Postgres terms - anonymous sign-in issues a real JWT with a real uid - so
-- this correctly covers someone who never added an email, which is the default
-- and the person most likely to want out.
revoke all on function public.delete_my_account() from public, anon;
grant execute on function public.delete_my_account() to authenticated;

commit;

-- Verify, as a signed-in user:
--   select public.delete_my_account();
--   -- then: select count(*) from public.spots;   -- expect 0 for that user
--
-- And verify it cannot reach anyone else: the function has no arguments, so
-- there is no value a caller can supply to name a different account.
