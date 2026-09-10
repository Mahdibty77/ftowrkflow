# Upgrading the live server from the old version

This is the procedure for replacing the version currently running on the company
server with the version in this folder. Follow it in order. Nothing here is
optional except where it says so.

Everything below was tested by taking a copy of the old project's database and
running this codebase's migrations against it, then walking the "add a person and
give them their role" flow on the result. What the test found is written down
honestly, including the one part you will not like.

---

## The short answer to your question

**Will any data be deleted?** No. Not one row. Cases, case forms, case history,
clients, line items, expert codes, currency rates, user accounts, profiles,
uploaded files and the item-coding tables all survive the upgrade untouched. This
was measured before and after, table by table — see "Row counts, measured" below.

**Will you still be able to define a person and give them their role?** Yes. It
was tested end to end against the migrated copy of your database: an administrator
creates a Person, assigns them an existing seat (for example the Commercial
Expert seat), and that seat keeps its unit, its role, its internal code, and every
case and history entry it has ever been attached to.

**But there is one thing you must know before you start.** After the upgrade,
**only `admin` can sign in.** All twelve other logins — the eleven unit/role
accounts plus the leftover `zzperm` — are switched off (`is_active = false`)
until you attach a person to them. Nothing is deleted and no password is lost:
the accounts are simply asleep until you assign them.
This is how the new version works: a login is a *seat*, and a seat is only awake
while a person holds it. Section 6 explains exactly what to do about it.

Plan the upgrade for a time when you can spend an hour afterwards creating people
and handing out seats. Do not do it at 8 a.m. on a working day.

---

## 1. Before you start — take a backup, and check that it exists

Do not skip this and do not trust that the nightly backup ran.

```bash
cd /path/to/ftworkflow          # the folder with docker-compose.yml
docker compose exec db pg_dump -U ftworkflow ftworkflow > ~/ftworkflow-before-upgrade.sql
ls -lh ~/ftworkflow-before-upgrade.sql
```

The second command must show a file of a real size (megabytes, not zero bytes).
If it does not, stop and fix that before going any further.

Copy that file **off the server** — a USB stick or another machine. A backup that
lives only on the machine you are about to change is not a backup.

Also make sure you still have your **licence string** written down somewhere off
the server (see section 7). You almost certainly will not need it, but the day you
do need it is the day you cannot get it.

---

## 2. Replace the files

**Unzip the new version over the existing folder. Do not delete the folder first.**

The reason matters. `docker-compose.yml` bind-mounts two things straight from the
project folder on the host:

* `./itemcoder/resources/json` — the live item-coding configuration that your
  admins edit through the Tool Data screens;
* `./scripts` — the backup/restore scripts.

Those are real, live configuration. Deleting the folder and copying a fresh one in
would replace whatever your admins have edited there. Unzipping *over* the folder
overwrites only the files the new version ships and leaves the rest alone.

One harmless detail you may notice afterwards: three files in
`itemcoder/resources/json` are no longer part of this version
(`confind_size.json`, `offer_nut-bolt.json`, `offer_nut&bolt.json`). Unzipping
over the folder leaves your old copies behind. That is fine — nothing in this
version reads them, so they just sit there. You may delete them or leave them.

Keep your existing `.env` file. The new version reads exactly the same
environment variables as the old one, and `requirements.txt` is unchanged, so
there are no new packages and no new settings to fill in.

---

## 3. Run the upgrade

```bash
docker compose up -d --build
docker compose logs -f web
```

Watch the log. You are waiting for the migration block. It lists roughly thirty
migrations, and what matters is that **every one ends in `OK`** and the log then
reaches `Starting gunicorn on 0.0.0.0:8000`. Press `Ctrl-C` to stop following the
log — that does not stop the server.

Your database, uploaded files, item-code tables and licence all live in Docker
named volumes, not in the image, so rebuilding does not touch them.

**If a migration fails**, the log will stop on the failing one and the web
container will restart in a loop. PostgreSQL applies each migration inside its own
transaction, so a failure leaves the database exactly as it was before that
migration started — nothing half-applied. Write down the migration name and the
error, then restore from section 1 if you need to get running again immediately.

---

## 4. Check it worked

Run these three checks before you tell anyone the upgrade is done.

**a) Every migration applied.**

```bash
docker compose exec web python manage.py showmigrations
```

Every line must show `[X]` — no line anywhere may show an empty `[ ]`. Pay
particular attention to `cases` and `people`, which are where all the new work is:
both must be applied all the way to their last listed migration.

**b) The start-up log has no hand-patching warnings.**

```bash
docker compose logs web | grep -i "schema sync"
```

This should print **nothing**. Any line here names a table or column that the
migration history failed to create and that the old start-up safety net had to
patch by hand. On the tested upgrade there were none: the migrations alone
produced a complete, correct schema — the safety net is not hiding a gap.

(This is also the check `entrypoint.sh` asks for before the safety net can be
retired. If both this and check (a) are clean, you may later drop `--run-syncdb`
and set `FT_SKIP_SCHEMA_SYNC=1`. That is a separate decision — leave it for now.)

**c) Your data is all there.**

```bash
docker compose exec db psql -U ftworkflow ftworkflow -c "
SELECT 'cases',       count(*) FROM cases_case
UNION ALL SELECT 'case forms',  count(*) FROM cases_caseform
UNION ALL SELECT 'case events', count(*) FROM cases_caseevent
UNION ALL SELECT 'clients',     count(*) FROM cases_client
UNION ALL SELECT 'line items',  count(*) FROM cases_lineitem
UNION ALL SELECT 'users',       count(*) FROM auth_user
UNION ALL SELECT 'profiles',    count(*) FROM accounts_profile;"
```

Compare with the same numbers taken before the upgrade. They must be identical.
(Take them beforehand — run exactly the same command before step 3.)

Then sign in as `admin` and open a few existing cases. The names beside old
history entries and the expert names on old cases are now *frozen* — they show
who did the work at the time, and they will not change later when people move
between seats. That is intentional and it is a good thing.

---

## 5. Row counts, measured

Taken from the real upgrade test: a copy of the old project's database, brought
up to the old version's final migration, then migrated with this codebase.

| Table                      | Before | After  |
|----------------------------|--------|--------|
| auth_user                  |     13 |     13 |
| accounts_profile           |     13 |     13 |
| cases_case                 |      4 |      4 |
| cases_caseform             |     20 |     20 |
| cases_caseevent            |     41 |     41 |
| cases_client               |      3 |      3 |
| cases_lineitem             |    764 |    764 |
| cases_expertcode           |      3 |      3 |
| cases_currencyrate         |      2 |      2 |
| cases_caseexportlog        |      5 |      5 |
| cases_casecurrencylog      |      3 |      3 |
| itemcoder_featurevalue     |    219 |    219 |
| itemcoder_groupfeature     |     15 |     15 |
| coding_coderecord          | 37,154 | 37,154 |
| django_session             |    159 |    159 |
| **active logins**          | **13** |  **1** |

Nothing dropped. The export-log and currency-log rows were deliberately put there
for the test, because on your server those two tables were created by hand years
ago and this version brings them under migration control — the concern was that
adopting them might recreate them empty. It does not: the migrations create those
tables **only when they are missing**, so on your server they are left exactly as
they are, rows and all.

The last row is the one that matters to you, and it is section 6.

---

## 6. Waking the staff logins back up

After the upgrade the only account that can sign in is `admin`. The other logins
still exist, still hold their unit, role and internal code, still own all their
cases and history, and **still have their old passwords**. They are just inactive.

This is the new version's model: a login is a **seat** in the organisation, and a
seat is awake only while a **person** holds it. That is what lets you move an
employee between roles, or hand a seat to a replacement, without inventing new
accounts and without rewriting the history of what the previous holder did.

### The supported way: create a person, give them the seat

Sign in as `admin`, then for each employee:

1. Go to **People** → add a new person. Fill in their name in Persian **and their
   Latin name** — the Latin name is what their login name is built from, so it is
   not optional.
2. Save. The person gets a permanent detail code automatically.
3. Open the person and use **Available seats**. All your old logins are listed
   there by unit and role — Commercial Manager, Commercial Supervisor, Commercial
   Expert, Technical Manager, and so on.
4. Assign the seat that matches their job. The seat wakes up immediately.

This was tested against the migrated copy of your database. Assigning a person to
the `com_expert` seat gave:

* the seat active again, holding unit `COMMERCIAL`, role `EXPERT`, internal code
  `103` — unchanged;
* a `PersonRole` recording that this person holds Commercial / Expert;
* the case that seat created and both history entries it wrote **still attached
  to it**, and the frozen names on them unchanged;
* the seat's **old password still working**.

Assigning a *second* seat to the same person also works — that is how you record
one employee who covers two roles. It adds a second role to the person rather than
a second login.

### Two things about this that will catch you out

**The login name changes.** When you assign a person to a seat, the seat's
username is rebuilt from their Latin name, for example
`com_expert` → `Mahdi_Bayati574278`. The password is unchanged, but the username
is not. Write the new name down and give it to the employee. There is no way to
choose it by hand — the trailing digits are generated.

**Working hours are now enforced.** Anyone who is not the platform admin or the
general manager is signed out outside their shift, which defaults to 08:00–17:00
until you set that person's own hours. If you upgrade in the evening, do not be
surprised that a test login is refused.

### If you need someone back in *right now*

If somebody must work before you have entered them as a person, you can switch
their old login back on directly. This is a stopgap, not the intended way to run
the system — do the People flow properly afterwards.

```bash
docker compose exec web python manage.py shell -c "
from django.contrib.auth.models import User
u = User.objects.get(username='com_expert')
u.is_active = True
u.save(update_fields=['is_active'])
print('reactivated', u.username)
"
```

Change `com_expert` to the login you need. Their old username and old password
both work again. This was tested: the account authenticates and passes the shift
check inside working hours. It will show the old name rather than a person's
record, and it will not appear under any person, which is why it is a stopgap.

---

## 7. Your licence

**You stay licensed. You do not need a new licence string and you do not need to
re-activate.** This was tested directly against your installation's actual
fingerprint.

Two things had to hold, and both do:

* The machine binding was rewritten in this version, but it still reads the same
  cached fingerprint file and produces the **same machine ID**, so the sealed
  licence file still opens.
* The new version also checks the hardware signal by signal. A licence activated
  before that check existed has no signals recorded — and the code treats "nothing
  recorded" as **allow, and record**. Your install passes, and quietly gains the
  stronger binding on its first run without you doing anything.

**The one way to lose it:** the licence lives in the `license_data` Docker volume,
which is *not* covered by the backups. `docker compose up -d --build` does not
touch it. `docker compose down -v`, or wiping Docker, destroys it — and then the
machine fingerprint is recomputed from scratch and no longer matches, so the app
goes to the activation screen.

**Never run `docker compose down -v` on this server.** Use `docker compose down`
(no `-v`) if you need to stop everything.

---

## 8. What each new migration does

For reference, and so nothing here is a black box. "Your data" below means the
database as it stands on the server today.

### accounts — six new migrations

| Migration | What it does to existing rows |
|---|---|
| `0010_profile_seat_fields` | Adds three columns with defaults. Safe. |
| `0011_profile_seat_code` | Adds a nullable `seat_code` column (safe — every existing row gets NULL, so the unique index cannot be violated). Then runs code that gives `admin` a seat code **and switches off every non-admin login that no person holds.** First half of the sign-in change. |
| `0012_profile_avatar` | Adds one nullable column. Safe. |
| `0013_seat_index_per_role` | Replaces the global unique on `seat_code` with a per-Unit+Role one, then renumbers seat codes to `001`, `002`, … within each unit/role pool. Rewrites `seat_code` values; removes no rows. |
| `0014_platformconfig_default_daily_hours` | Adds four columns with defaults (the 08:00–17:00 shift). Safe. |
| `0015_alter_profile_stamp` | Help-text only. Touches nothing. |

### cases — six new migrations

| Migration | What it does to existing rows |
|---|---|
| `0006_signaturesnapshot_signer_title` | Adds one blank column. Old documents render exactly as before. |
| `0007_casecurrencylog_into_migrations` | Brings the currency-conversion audit table under migration control. **Creates the table only if it is missing.** Yours exists, so nothing happens and no row is touched. |
| `0008_caseexportlog_create_if_missing` | The same for the export audit table. Same result. |
| `0009_case_expert_display_freeze` | Adds two blank columns, then **writes to your existing case rows**: it fills in the commercial and technical expert names as they stand today, so a later staff change can never rewrite what an old case showed. This changes data. It is the point of the migration and it removes nothing. |
| `0010_case_delegated_fields` | Adds three columns (default false / nullable). Safe. |
| `0011_alter_caseevent_action` | Choice labels only. Never reaches the database. |

`cases/0003` was corrected in this version, but it is already applied on your
server and Django never re-runs an applied migration, so it will not run again.
The correction only affects databases built from scratch.

### people — the whole app is new (0001 to 0021)

Every table it creates is created **empty**. There is deliberately no backfill:
no person is invented for your existing accounts. You enter them yourself.

Six of these migrations reach out and change `accounts` / `auth` rows, so they are
worth naming:

| Migration | What it does to existing rows |
|---|---|
| `0004_legacy_one_login_roles` | Keeps `admin` as the one administrator and demotes any other. Marks every unheld login as an available seat. Switches off only `userN`-style placeholder logins — your named accounts are left alone here. |
| `0005_restore_legacy_logins` | Switches real (non-placeholder) logins back on and re-attaches the admin flag to `admin`. |
| `0006_repair_seats_and_tech_manager` | Demotes non-`admin` administrators again, then **switches off every login that no person holds, except the platform admin.** Second half of the sign-in change. |
| `0007_guarantees_internal_seatlog` | Drops four columns — but from `people_person`, which is new and empty. No data exists to lose. |
| `0010_remove_auto_tech_manager` | Deletes a login called `tech_manager` **only if it is completely blank**: no name, no email, no person, and unit/role exactly Technical/Manager. Yours is named "Technical Manager", so it is kept. Verified: 13 users before, 13 after. |
| `0017_remove_leave_purchase` | Deletes Leave and Purchase staff requests. That table is brand new and empty on your server, so nothing is deleted. |

The remaining people migrations add columns to the app's own new tables.

### itemcoder

No new migrations. Nothing changes.

### The old `coding` tables

Your database still holds `coding_coderecord` and friends (37,154 rows) from an
app that neither the old nor the new version installs. The migration history still
lists a `coding` app that no longer exists in the code. Django ignores both, the
upgrade runs fine, and the rows are left alone. Nothing to do.

---

## 9. What I changed to make this safe

One real defect was found and fixed:
`people/migrations/0006_repair_seats_and_tech_manager.py`.

That migration identifies "the real administrator" as the login named `admin`, or
failing that the first Django superuser. If it found **neither**, it demoted *every*
administrator — and its own last step then switches off every login that is not an
administrator and not held by a person. The result was a database with **zero
active users and zero administrators**: all the data intact, nobody able to sign
in, and no administrator left to assign anybody. Unrecoverable through the
interface.

This is not a theoretical worry. Being an administrator on this platform is
`Profile.is_admin`, which has nothing to do with the username or with Django's
superuser flag, so an install whose administrator is called something else is a
perfectly ordinary shape. I reproduced the lockout by renaming `admin` to
`ftadmin` and clearing its superuser flag on a copy of your database, then
migrating: zero active users, zero admin profiles.

The fix falls back to the earliest `is_admin` profile when no `admin` login and no
superuser can be found — which is exactly what the sibling migration `0004`
already does in the same situation. After the fix, the same test keeps `ftadmin`
as the administrator, active, with all 13 users, 13 profiles, 4 cases, 20 forms
and 41 events intact.

**On your server this changes nothing at all.** Your administrator *is* called
`admin` and *is* a superuser, so the fallback never fires. Migrating your database
gives byte-identical results before and after the fix. It is there so the upgrade
cannot go wrong in a way you could not undo.

---

## 10. Honest list of risks and decisions

1. **The sign-in change is a decision, not a bug.** Two separate migrations state
   it deliberately, and the running code agrees with them: a vacant seat is an
   inactive login. It is not reversible by rolling back one migration. Plan for it
   (section 6) rather than being surprised by it.

2. **Old usernames stop working once a person is assigned.** The login name is
   rebuilt from the person's Latin name. Collect the new names as you go and hand
   them out; you cannot look them up later without opening each person.

3. **If you have promoted a second administrator, they lose it.** The new version
   allows exactly one platform administrator, `admin`. Any other profile with the
   admin flag is demoted and, being unheld, switched off. Verified on a copy with
   two admins: two before, one after. Decide beforehand who that person becomes —
   general manager is usually the right answer.

4. **Working hours are enforced from now on.** Default 08:00–17:00 for everyone
   who is not admin or general manager, until you set each person's own hours.
   Set them before the first working morning, or people will be locked out of
   their own shifts.

5. **The reference code tables are not replaced by the upgrade.** They live in the
   `code_db_data` volume, and a named volume hides whatever the new image ships.
   If this version corrects a code table, you must re-import it from the admin
   **Tool Data** page. Nothing in the log will tell you. (This is DEPLOY.md
   section 7 — mentioned here because it is easy to miss during an upgrade.)

6. **Everything above was tested on SQLite, not PostgreSQL.** The server runs
   PostgreSQL; the copy of the database that ships with the old project is
   SQLite, and that is what was available to test against. The migration history
   is identical on both and none of these migrations issues vendor-specific SQL,
   so the outcome should be the same — but "should" is why step 1 is a backup you
   have checked and copied off the machine, and why step 4 is three checks and not
   none.

7. **The bundled `db.sqlite3` in the old project folder is behind the old code.**
   It sits at `accounts.0005` / `cases.0003`, while the old project's own code goes
   to `accounts.0009` / `cases.0005`. Your live PostgreSQL is at the later point.
   Both starting points were tested and both upgrade cleanly to the same result,
   so it makes no difference — but if you ever restore that SQLite file for
   development, expect a few extra migrations to run.
