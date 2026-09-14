# Fixing Supabase — a checklist

Four things. **The first two are done** — the region was checked and is fine,
and the migrations are applied and verified against the live project. They are
kept here with their results rather than deleted, because "did we ever run
that?" is a question that comes back.

The two that remain are the email sender and the URL configuration, and neither
has a deadline.

Every dashboard link below uses Supabase's `project/_` form, which resolves
to whichever project you are signed in to. That keeps the links clickable
without writing the project reference into a public repository — it is not a
secret, since it is part of the app's own URL, but there is no reason to hand
a stranger the exact endpoint either.

The reference itself lives in `config/supabase.json`, which is gitignored.

---

## 1. Check the region — DONE, and it is fine

**Checked 2026-09-12: `eu-west-1`** (Ireland). Inside the EU, so nothing to do
and nothing to migrate. Round trip from Norway is around 30 ms, which is well
under anything a person notices.

This was the only item with a deadline on it, because Supabase fixes a
project's region at creation and changing it later means manually migrating
every row and every account. It does not need checking again unless the project
is ever recreated — in which case pick `eu-west-1`, `eu-central-1` (Frankfurt)
or `eu-west-3` (Paris), and note that the picker's **Europe** grouping also
contains London and Zurich, which have adequacy decisions but are not in the EU.

Region is a data-location control and not by itself GDPR compliance: a signed
DPA, a privacy policy and a working deletion path are separate. The deletion
path exists as of step 2; the other two do not.

---

## 2b. Drop PostGIS — PENDING

Supabase's linter reports **RLS Disabled in Public** for
`public.spatial_ref_sys`. That is PostGIS's own EPSG table — about 8,500 rows
of coordinate-system definitions, nothing about anybody — so it is not a leak
of user data. `public.spots` is the table that would be, and the linter does
not flag it, because it has RLS enabled with own-row policies.

It cannot be secured in place: `alter table public.spatial_ref_sys enable row
level security` fails with *must be owner of table spatial_ref_sys*, since the
table belongs to the extension and this project's `postgres` role is not a
superuser. So the fix is to remove a dependency nothing uses — which migration
001 already anticipated when it dropped `spots.geom`:

> If this is the only thing that ever used it, `drop extension postgis;` is
> safe to run separately once you have checked.

Checked on 2026-09-14: no geometry or geography column, no `st_*` call and no
`postgis.*` reference survives anywhere in `schema.sql` or the migrations.

Run `backend/migrations/003_drop_postgis.sql`. It is one statement, it is safe
to run twice, and it deliberately does **not** use `cascade` — a plain `drop
extension` refuses if something still depends on PostGIS, which is a failure
worth reading rather than working around.

Saved spots are untouched: `spots` has had no PostGIS column since 001.

---

## 2. Run the migrations — DONE and verified

Verified live on 2026-09-12 by `python scripts/check_backend.py`, against the
real project:

- `the unused geom column is gone` — migration 001 applied
- `B can erase itself` (HTTP 204) — migration 002 applied, and the app's delete
  button has something to call
- `the anon key CANNOT call delete_my_account` (HTTP 401) — the grant is right
- `an erased account CANNOT write again on its old token` (HTTP 409,
  *Key is not present in table "users"*) — a magic link's token stays valid
  until it expires, but the foreign key stops a deleted account resurrecting
  its own rows

All 24 checks pass, including the 16 isolation checks that are the reason any
of this exists.

`check_backend.py` now erases its own two test users at the end rather than
leaving them behind, so the old "delete the two anonymous test users" chore is
gone for good — and it exercises the erasure path on every run, which is the
right place for it.

---

## 3. Site URL and Redirect URLs — one minute

**Why:** a magic link has to come back to wherever the app is running. The app
now tells Supabase which origin that is on every request, so the **Redirect
URLs** list is what decides whether it is allowed. Anything not on the list is
rejected outright.

**Do:** open
[Authentication → URL Configuration](https://supabase.com/dashboard/project/_/auth/url-configuration).

Set **Site URL** to `http://localhost:8765/` for now — it is the fallback for
anything that does not name an origin, and the deployed site replaces it later.

Add to **Redirect URLs**:

```
http://localhost:8765/          <- testing on this machine. Start here.
https://<your-lan-ip>:8765/     <- only if you want the link to open on the phone
https://<your-deployed-site>/**  <- after the first deploy
```

**Start with `localhost`, not the LAN address.** `python scripts/serve_interface.py --dist`
serves on `http://localhost:8765`, and browsers treat localhost as a secure
context — so service workers, geolocation and the magic link all work there with
no certificate at all. It is also stable.

The LAN address is for one specific job: opening the email *on your phone* and
having the link land in the app there. It carries two annoyances that localhost
does not. The certificate is self-signed, so the phone warns you and you have
to tap through. And the address is handed out by your router, so it changes —
and when it does, the entry silently stops matching and links start being
rejected with no clue why. If you want it stable, reserve the address in your
router's DHCP settings.

## 4. Email — the one with a prerequisite

**The situation is worse than "rate limited".** Supabase's built-in mailer
sends **2 messages per hour**, and only to email addresses belonging to your
own team. It is a development convenience, not a mail service. Account recovery
was never going to work on it for a real user, whatever we did in the app.

Configuring your own SMTP raises that to 30/hour by default, adjustable on the
[Rate Limits page](https://supabase.com/dashboard/project/_/auth/rate-limits).

**The prerequisite nobody mentions:** Resend requires a **verified domain**
before it will send on your behalf. So does every other reputable provider, for
the same reason — otherwise anyone could send mail claiming to be you.

That leaves two paths.

### If you want to test the flow today, with no domain

Sign up at Resend and use their test sender `onboarding@resend.dev`. It will
only deliver to the email address on your own Resend account, which is enough
to prove the magic link works end to end and not enough for anybody else.

| field | value |
|---|---|
| Host | `smtp.resend.com` |
| Port | `465` |
| Username | `resend` |
| Password | your Resend API key |
| Sender email | `onboarding@resend.dev` |
| Sender name | `Soppkart` |

Fill those in at
[Authentication → SMTP Settings](https://supabase.com/dashboard/project/_/auth/smtp),
then from the project root:

```
python scripts/check_auth_email.py <your-resend-account-email>
```

It sends one real magic link and tells you SENT, RATE LIMITED, REJECTED or
REFUSED.

### For real users

You need a domain — which you need for hosting anyway, so this is one purchase
solving two problems. Roughly £10/year. Point it at the site, verify it in
Resend (three DNS records), then change the sender to something like
`ikke-svar@dittdomene.no`.

Alternative if you would rather not buy a domain yet: **Brevo** verifies a
single *email address* rather than a whole domain, and its free tier is 300
mails a day. Same SMTP fields, different host. It is a slightly lower barrier
and a slightly worse long-term answer.

---

## And one thing that is not a fix, but will bite

Enable the **keepalive** workflow. A free project pauses after 7 days with no
database activity, and restoring it is a manual click nobody will be watching
for. This app is seasonal: busy August to October, then potentially silent
until next summer. The pause would land in February and surface on the first
good weekend of the season, to a user who had been told their spots were safely
synced.

`.github/workflows/keepalive.yml` already exists. It needs two repository
secrets, neither sensitive — the anon key already ships inside the app:

```
SUPABASE_URL        https://<project-ref>.supabase.co
SUPABASE_ANON_KEY   the anon / publishable key
```

Or run `python scripts/keepalive.py` from anything that stays awake. It made a
successful read in 258 ms when I tested it, so the project is alive right now.
