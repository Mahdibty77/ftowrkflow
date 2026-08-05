# Foolad Tabar Workflow Platform (`ftworkflow`)

An online, multi-unit workflow platform for Foolad Tabar that runs the full
commercial flow across three units — **Commercial**, **Technical** and
**Supply** — plus a site **Administrator**, with an embedded item-coding and
pricing engine (`itemcoder`, formerly the standalone *codify* tool).

A case is opened by Commercial, routed to Technical for a Technical Offer (TO),
optionally to Supply for pricing (the Proforma, PI), then back through Technical
to Commercial to be closed or burned. Every action is recorded in an immutable,
timestamped audit trail and every form (Inquiry, TO, PI) is versioned.

> See **`FooladTabar_Workflow_Manual.pdf`** (delivered alongside this code) for
> the full administrator guide, architecture reference and deployment guide.

## Quick start (local, SQLite)

Requires Python 3.11+ (developed on 3.12).

```bash
cd ftworkflow
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # set DJANGO_SECRET_KEY; keep DJANGO_DEBUG=1 locally
python manage.py migrate
python manage.py seed_demo          # demo users + sample pipe code table (SQLite)
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in.

## Demo accounts (after `seed_demo`)

| Username | Password | Role |
|---|---|---|
| `admin` | `admin12345` | Administrator |
| `com_manager` / `com_super` / `com_expert` | `pass12345` | Commercial — manager / supervisor / expert |
| `tech_manager` / `tech_super` / `tech_expert` | `pass12345` | Technical — manager / supervisor / expert |
| `sup_manager` / `sup_super` / `sup_expert` | `pass12345` | Supply — manager / supervisor / expert |

**Change every password before any real use.**

## Importing large code tables

The bundled sample pipe table is loaded by `seed_demo` into the group's SQLite
DB. Import full/large tables on the server (same path as Tool Data → Import):

```bash
python manage.py import_codes pipe /path/to/pipe_coding_data.csv
python manage.py import_codes fitting /path/to/fitting_coding_data.csv
```

## Project layout

```
ftworkflow/
├── core/        theming, shared base layout, home router, seed_demo
├── accounts/    users, profiles, authentication
├── cases/       the workflow heart (cases, forms, audit, exports, master data)
├── itemcoder/   item coding + pricing (Build TO/PI, Tool Data, SQLite code DBs)
└── reports/     role-aware dashboards
```

## Status

A runnable, well-structured **v1 foundation** covering the whole flow end to
end. Known items to finish on the real server (see the manual, section 5):

- import the large fitting code table with `import_codes`;
- the exported TO/PI print template is built from the written spec (the original
  `form-gmi-4.html` was not available) and can be refined to the house style;
- coding-grid behaviour should be verified once the real reference tables are
  imported, since the engine depends on that data.
