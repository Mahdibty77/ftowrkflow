# Foolad Tabar Workflow Platform (`ftworkflow`)

An online, multi-unit workflow platform for Foolad Tabar that runs the full
commercial flow across three units — **Commercial**, **Technical** and
**Supply** — plus a site **Administrator**, with an embedded item-coding and
pricing engine (`itemcoder`, formerly the standalone *codify* tool).

A case is opened by Commercial, routed to Technical for a Technical Offer (TO),
optionally to Supply for pricing (the Proforma, PI), then back through Technical
to Commercial to be closed or burned. Every action is recorded in an immutable,
timestamped audit trail and every form (Inquiry, TO, PI) is versioned.

> **New here? Read [`ARCHITECTURE.md`](ARCHITECTURE.md).** It is the map: what
> the system does, what each of the seven apps owns, the five concepts you
> cannot guess from the code, a "change X → look in Y" table, and the
> behavioural snapshot that protects you from breaking the business rules.

| Document | For |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Developers — how the system is built |
| [`DEPLOY.md`](DEPLOY.md) | Operators — Docker / production, Linux and Windows |
| [`UPGRADE_NOTES_2026-07.md`](UPGRADE_NOTES_2026-07.md) | Upgrading past the 2026-07 security pass |
| [`licensing/README_LICENSING_FA.md`](licensing/README_LICENSING_FA.md) | Licence activation (Persian) |
| `FooladTabar_Workflow_Manual.pdf` | The administrator guide (delivered alongside this code, not in the repo) |

## Quick start (local, SQLite)

Requires Python 3.11+ (developed on 3.12).

**Read this first:** nothing in this project loads `.env` — `settings.py` reads
the environment directly, and Docker Compose is what reads `.env` in production.
So copying `.env.example` is *not* enough locally: without `DJANGO_DEBUG=1` (or
a real `DJANGO_SECRET_KEY`) the server refuses to start with
`ImproperlyConfigured: DJANGO_SECRET_KEY is not set`. That is deliberate — see
[ARCHITECTURE.md §5](ARCHITECTURE.md#5-how-to-run-it-honestly).

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DJANGO_DEBUG=1                # PowerShell: $env:DJANGO_DEBUG = "1"
python manage.py migrate
python manage.py ensure_schema       # adds post-initial columns; idempotent
python manage.py seed_demo           # demo users, clients, expert codes, a case
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in.

## Demo accounts (after `seed_demo`)

| Username | Password | Role |
|---|---|---|
| `admin` | `admin12345` | Administrator |
| `gen_manager` | `pass12345` | General manager (sees everything, manages nobody) |
| `com_manager` / `com_super` / `com_expert` | `pass12345` | Commercial — manager / supervisor / expert |
| `tech_manager` / `tech_super` / `tech_expert` | `pass12345` | Technical — manager / supervisor / expert |
| `sup_manager` / `sup_super` | `pass12345` | Supply — manager / supervisor |
| `sup_expert_in` / `sup_expert_ex` | `pass12345` | Supply expert — Internal / External |

**Change every password before any real use.** `seed_demo` is safe to re-run and
never resets a password. Every account in the table above *except* `admin` is
left untouched on a second run — it prints `'<name>' already exists; leaving its
profile as it is` and moves on.

`admin` is the exception: `_ensure_admin`
(`core/management/commands/seed_demo.py`) sets `admin.profile.is_admin = True`
and saves it **every** run, and `Profile.save()` then also blanks that profile's
`unit`, `role` and `supply_kind` (that is the reserved-administrator rule in
`accounts/models.py`, not a seeding decision). Harmless on a demo database;
worth knowing before running it anywhere with real data. It is a development
command, not a production one.

## Code tables

The coding engine's per-group tables live in their own SQLite files under
`itemcoder/resources/db/`, outside the Django ORM (in Docker: the `code_db_data`
volume). Import them on the server — same operation as Tool Data → Import:

```bash
python manage.py import_codes pipe /path/to/pipe_coding_data.csv
python manage.py import_codes fitting /path/to/fitting_coding_data.csv
```

The demo pipe table `seed_demo` refers to is not present in this repository, so
that one step is skipped with a warning; everything else it does works.

## Project layout

Every source directory in the repository root — the seven apps plus the
plumbing around them:

```
<repo root>/
├── ftworkflow/  the Django project package: settings.py, urls.py, wsgi/asgi
├── core/        base layout, theming, landing router, authenticated /media/
├── accounts/    seats: users, profiles, units, roles, signatures, sign-in
├── cases/       the workflow heart — cases, forms, audit trail, exports
├── itemcoder/   item coding + pricing (Build TO/PI, Tool Data, SQLite code DBs)
├── people/      the humans — person records, seats, work shifts, requests
├── reports/     role-aware dashboards (read-only over cases)
├── licensing/   offline RSA licence activation and the request gate
├── templates/   project-level template overrides (currently admin/base_site.html)
├── static/      site-wide CSS/JS/images (itemcoder and people also ship their own)
├── scripts/     shell helpers run by the Docker backup service (backup/restore)
└── tests/       snapshot.py — the behavioural safety net (see ARCHITECTURE §6)
```

The seven apps are `core`, `accounts`, `cases`, `itemcoder`, `people`,
`reports` and `licensing`; the rest is project plumbing. Note that `ftworkflow`
names two different things — the repository, and inside it the settings/URL
package that ARCHITECTURE.md keeps pointing you at.

`media/` (uploads, served through `core`'s login-gated view) and `staticfiles/`
(the `collectstatic` output) appear at runtime and are git-ignored.

## Before you change anything

There are no unit tests. There is one behavioural snapshot, and it is the only
thing standing between a refactor and a wrong document reaching a customer:

```bash
python -m tests.snapshot --db /path/to/some.sqlite3 --out before.json
# ... make your change ...
python -m tests.snapshot --db /path/to/some.sqlite3 --out after.json
python -m tests.snapshot --compare before.json after.json
```

`IDENTICAL` is the only clean result for a refactor. See
[ARCHITECTURE.md §6](ARCHITECTURE.md#6-the-safety-net-testssnapshotpy) for what
it covers and how to read a diff.
