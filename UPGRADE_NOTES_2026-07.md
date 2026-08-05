# Deploying this update — 2026-07 security & integrity pass

This note covers only what changed in this update and how to roll it onto
the live server safely. For the general deployment process see `DEPLOY.md`.

## 2026-07-24 addendum — if your first deploy attempt crashed on migrations

If you already tried deploying an earlier copy of this update and saw the
web container crash-loop with:

    psycopg2.errors.InvalidForeignKey: there is no unique constraint
    matching given keys for referenced table "cases_caseform"

that has been fixed in this package — no action needed beyond deploying
this version. What happened: the new SignatureSnapshot table's link back to
CaseForm asked PostgreSQL to enforce it as a foreign key, and something
about how `cases_caseform` exists on your production database (it predates
every migration in this project) didn't satisfy what Postgres requires to
accept a *new* foreign key pointing at it. Nothing else in this codebase had
ever linked to that table this way before, so this was the first time it was
exercised. The fix does not touch `cases_caseform` at all: the new table's
link to it is now enforced by Django itself instead of by the database
(`db_constraint=False`), which sidesteps the issue entirely.

Because PostgreSQL applies each migration's changes as one transaction, that
crash left your database exactly as it was before the attempt — nothing was
partially created, no existing data was touched. This version's migrations
apply cleanly from that same starting point.

## 2026-07-25 addendum — second issue, one migration further along

After the fix above, `cases.0004` applied successfully but the very next
migration, `cases.0005` (the one-time backfill of names onto existing
timeline entries), then crash-looped with:

    AttributeError: 'User' object has no attribute 'get_full_name'

Also fixed in this package, also does not touch any existing data. The cause
was different from the first issue and worth understanding on its own:
inside a Django data migration, `apps.get_model(...)` returns a *historical*
reconstruction of a model — one built only from the fields recorded in
migration history. It does not carry over custom Python methods defined on
the real model class, even for Django's own built-in User model. The
migration called `user.get_full_name()` on a user object obtained this way;
that method doesn't exist on the historical version, only on a real one used
anywhere else in the app. The fix builds the same full name by hand from the
underlying first_name/last_name fields instead of calling that method — data
migrations only touch plain fields from here on, verified by actually
executing both data migrations' logic against stand-in objects that mimic
this exact restriction before packaging this version.

## Before you deploy

1. **Take a fresh backup.** Use the existing Backups tab (or `docker compose
   exec backup ...` per `DEPLOY.md`) and confirm the archive actually landed
   in `../backups` before continuing.
2. **Rotate three credentials** if you haven't already: `DJANGO_SECRET_KEY`,
   `POSTGRES_PASSWORD`, and the platform superuser's password. These were
   present in a `.env` file that was shared outside the server at least once.
   Generate a new secret key with:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
   Changing `DJANGO_SECRET_KEY` signs every currently-open session out —
   do this at the end of a work day.
3. **Check `DJANGO_ALLOWED_HOSTS` in your real `.env`.** This update makes
   that setting actually take effect for the first time (previously it was
   silently ignored and the app accepted any hostname). If it's unset,
   nothing changes — the app stays open to any host, exactly as today. If
   it *is* set, make sure it lists every hostname and IP address this server
   is genuinely reached by, including any you rarely use, before relying on
   it — an incomplete list here means real, working requests start getting
   rejected. When in doubt, leave it blank for this deploy and tighten it
   separately, verified on its own.

## Deploying

Normal upgrade procedure — nothing new required beyond the usual steps,
because the new database changes are ordinary Django migrations that already
run automatically as part of container start-up (`entrypoint.sh`):

```
git pull            # or unpack the new code over the old, however you normally update
docker compose up -d --build
docker compose logs -f web    # watch it come up
```

What happens automatically on this start-up:
- The four new migrations apply (two in `accounts`, two in `cases`).
- One of them backfills the name/title shown on every existing case-timeline
  entry, using each person's *current* name and role. Deploy this promptly —
  the longer it waits, the more likely a role change happens first, at which
  point that one-time backfill would use the *new* title for old events
  instead of the exact original.
- A new `redis` container starts. It sits idle and unused unless you later
  set `REDIS_URL` — nothing about today's behaviour changes because it
  exists.

## After you deploy — verify

- [ ] Sign in as an existing user with their existing password. Confirm nothing
      about their account changed.
- [ ] Open an existing case and confirm the timeline still displays correctly.
- [ ] Open the Users page: the password column is gone, replaced by
      "Reset password" and "Log in as" actions. Existing accounts should show
      as **Active**, with no "Pending change" tag (that tag only appears on
      accounts created or reset *after* this update).
- [ ] Create one test user and confirm you get a one-time password shown
      exactly once, and that account is forced to set its own password at
      first sign-in.
- [ ] Try "Log in as" on that test user, confirm the return-to-admin banner
      appears and works, then check the Users page still looks right
      afterwards.
- [ ] Sign in as a General Manager account (if you have one) and confirm they
      can reach Users and use "Log in as", but see no Create/Edit/Reset
      password/Cut off actions.
- [ ] Sign in as a departmental manager (Technical Manager, Commercial
      Manager, etc.) and confirm the Users page is not reachable at all —
      no impersonation option anywhere for that account, under any path.
- [ ] Open any case's PDF/print export and confirm the signature block still
      renders correctly. Export a form that has never been exported before
      and confirm it renders (this is what exercises the new
      SignatureSnapshot table for the first time).
- [ ] Confirm a signature or stamp image still displays correctly on the
      profile page (this exercises the new authenticated media route).

## What does NOT require any action

- Existing user accounts, passwords, and sessions are unaffected — nobody is
  logged out, nobody's password changes, by this deploy itself.
- Existing cases, forms, and their current workflow state are untouched —
  no case's status, holder, or assignment changes.
- Existing exported PDFs/Excel files already on disk or already sent to
  clients are obviously unaffected; only what a *new* export of an
  *already-exported* form shows is now frozen going forward.

## Rolling back

Every migration in this update is a schema addition (new columns, new
tables) or a data backfill — nothing drops or renames an existing column.
If you need to revert the code, the database does not need a matching
rollback first; the old code simply won't reference the new columns/tables,
which is safe. If you do want to unwind the migrations themselves:

```
docker compose exec web python manage.py migrate cases 0003_currencyrate
docker compose exec web python manage.py migrate accounts 0005_profile_stamp
```
