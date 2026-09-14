-- Schedule the tombstone purge, so "deleted" means deleted.
--
-- 2026-09-15. `purge_tombstones()` has existed since the first schema and has
-- never been scheduled. Nothing calls it. That is easy to miss because the
-- app behaves correctly either way: a deleted spot disappears from every
-- device immediately, because `deleted_at` is what the sync protocol reads.
-- What does not happen is the second half. The row stays in the table, with
-- its coordinates and its note, for as long as the database exists.
--
-- Until now that was untidy. It is now a factual claim in a published
-- document: web/personvern.html tells the reader that a deleted spot is
-- "fjernet permanent innen 30 dager", and tests/test_privacy_policy.py checks
-- that the number in the prose matches the interval in `purge_tombstones()`.
-- Neither of those can check that anything actually runs. This is the part
-- that makes the sentence true, and it has to be run before the policy is
-- published rather than after.
--
--   Run in the Supabase SQL editor, or:
--     psql "$DATABASE_URL" -f backend/migrations/004_schedule_tombstone_purge.sql
--
-- Safe to run twice: an existing job of the same name is replaced.

-- pg_cron is available on Supabase but is not enabled by default. Enabling it
-- from SQL is equivalent to ticking it in Database -> Extensions.
create extension if not exists pg_cron;

-- Unschedule first rather than relying on a particular pg_cron version's
-- upsert behaviour. `cron.unschedule` raises if the job is absent, so it is
-- guarded rather than called blind - a first run must not fail.
do $$
begin
    if exists (select 1 from cron.job where jobname = 'purge-spot-tombstones') then
        perform cron.unschedule('purge-spot-tombstones');
    end if;
end;
$$;

-- 03:17 daily. An odd minute on purpose: whole hours are when every scheduled
-- job in every database everywhere runs at once, and this one has no reason to
-- join them. Daily is far more often than needed for a 30-day window; the
-- point is that it runs at all, and a small delete costs nothing.
select cron.schedule('purge-spot-tombstones', '17 3 * * *',
                     $$select public.purge_tombstones()$$);

-- ---------------------------------------------------------------------------
-- Verify. Run these after, and read them rather than assuming.
--
--   The job exists and is active:
--
-- select jobid, schedule, command, active
--   from cron.job
--  where jobname = 'purge-spot-tombstones';
--
--     expect one row, active = true, schedule = '17 3 * * *'
--
--   It has run, and succeeded. Empty until after the first 03:17 - that is
--   the expected answer on the day you run this, not a failure:
--
-- select start_time, status, return_message
--   from cron.job_run_details
--   join cron.job using (jobid)
--  where jobname = 'purge-spot-tombstones'
--  order by start_time desc
--  limit 5;
--
--     expect status = 'succeeded'
--
--   Nothing is sitting in the table that should be gone. This is the question
--   the privacy policy actually answers, so it is the one worth checking:
--
-- select count(*) as overdue
--   from public.spots
--  where deleted_at is not null
--    and deleted_at < now() - interval '30 days';
--
--     expect 0 once the job has run once
--
--   Run it by hand now, for anything already overdue, rather than waiting for
--   03:17. The function is revoked from anon and authenticated, so this only
--   works as the owner in the SQL editor:
--
-- select public.purge_tombstones();
--
--     returns the number of rows removed
