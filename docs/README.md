# Docs

Project documentation lives in the repository root:

- [README.md](../README.md) — quick start, demo accounts, layout
- [DEPLOY.md](../DEPLOY.md) — Docker / production deployment

Tool Data (code tables, rules, offers) is managed in the web UI under
**Tool → Data** (`/tool/…`), not via the removed legacy `/coding/` app.

Large code tables:

```bash
python manage.py import_codes pipe /path/to/pipe_coding_data.csv
python manage.py import_codes fitting /path/to/fitting_coding_data.csv
```

SQLite databases for each group are stored under `itemcoder/resources/db/`
(or the Docker volume `code_db_data` at `/app/itemcoder/resources/db`).
