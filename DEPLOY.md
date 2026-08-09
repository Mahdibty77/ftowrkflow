# Deploying Foolad Tabar Workflow with Docker

This guide runs the application on a server using Docker and PostgreSQL. The
main database starts **empty**. Reference code tables (pipe/valve/...) are
copied from the image into the `code_db_data` volume the first time that volume
is created — but only the tables that were present in the folder the image was
built from. The `.sqlite3` files are excluded from version control, so an image
built from a fresh `git clone` contains **none** of them and every table has to
be imported once from the admin **Tool Data** page.

> **Before sending this project to anyone:** delete `.env` from the copy you
> send. It is the developer's own environment file — a real secret key, database
> password and admin password — and it is not part of the delivery. `.env` is
> excluded from git and from the Docker image, but nothing strips it out of a
> zip or an `scp -r` of the folder. The customer creates their own in step 3.

There are two install paths below — pick the one that matches your server:

- **Part A — Linux** (Ubuntu/Debian server). Recommended for production.
- **Part B — Windows** (Docker Desktop). For a Windows server or local testing.

The build/run commands are the same on both; only Docker installation, the
shell, and file paths differ. Sections 6+ (operations, backups, etc.) apply to
both.

---

## Generate a secret key (works on any OS)

You'll need a `DJANGO_SECRET_KEY`. Once Docker is installed you can generate one
with Docker itself (no local Python required):

```bash
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Copy the printed string into `.env` in step 3.

---

# Part A — Linux (Ubuntu/Debian)

## A1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # run docker without sudo
```

Log out and back in (or run `newgrp docker`), then verify:

```bash
docker --version
docker compose version
```

## A2. Copy the project to the server

From your own machine (where the zip is), send it over SSH:

```bash
scp ftworkflow_r24.zip user@SERVER_IP:~/
```

Then on the server, unzip and enter the folder:

```bash
sudo apt-get update && sudo apt-get install -y unzip   # if unzip is missing
unzip ftworkflow_r24.zip
cd ftworkflow          # the folder that contains docker-compose.yml
```

## A3. Configure the environment

```bash
cp .env.example .env
nano .env
```

Fill in at least:

- `DJANGO_SECRET_KEY` — the value from the generate step above.
- `POSTGRES_PASSWORD` — a strong database password.
- `DJANGO_ALLOWED_HOSTS` — your server IP and/or domain,
  e.g. `203.0.113.10` or `workflow.example.com,203.0.113.10`.
- (Optional) `DJANGO_SUPERUSER_USERNAME` + `DJANGO_SUPERUSER_PASSWORD` to create
  the first admin automatically (otherwise create it in step A5).
  **`DJANGO_SUPERUSER_USERNAME` must be exactly `admin`.** Platform-administrator
  rights belong to that one reserved login; an account created under any other
  name can reach Django's `/admin/` but none of the platform's own screens
  (Users, Seats, People, Backups). Start-up refuses any other name and tells you
  so in the log rather than creating a half-working administrator.

Save in nano: `Ctrl+O`, `Enter`, then `Ctrl+X`.

## A4. Build and start

```bash
docker compose up -d --build
```

The first run takes a few minutes. Watch the logs until you see
`Starting gunicorn`:

```bash
docker compose logs -f web
```

`Ctrl+C` stops following the logs (the app keeps running).

## A5. Create the first administrator (if you skipped it in `.env`)

```bash
docker compose exec web python manage.py createsuperuser
```

## A6. Open the firewall (if one is active) and visit the app

```bash
sudo ufw allow 8000/tcp     # only if ufw is enabled
```

Open `http://SERVER_IP:8000` in a browser.

---

# Part B — Windows (Docker Desktop)

Use this to run the stack on a Windows machine — a Windows server, or your own
PC for testing.

## B1. Install Docker Desktop (with WSL2)

1. Open **PowerShell as Administrator** and enable WSL2:
   ```powershell
   wsl --install
   ```
   Restart Windows when prompted.
2. Download **Docker Desktop** from https://www.docker.com/products/docker-desktop/
   and run the installer. Keep **"Use WSL 2 instead of Hyper-V"** checked.
3. Restart, then **launch Docker Desktop** and wait until the bottom-left status
   says **"Engine running"** (the whale icon in the system tray is steady).
4. Verify in a normal PowerShell window:
   ```powershell
   docker --version
   docker compose version
   ```

> Docker Desktop must be running before any `docker` command. If a command says
> "cannot connect to the Docker daemon", open Docker Desktop first.

## B2. Get the project

1. Right-click `ftworkflow_r24.zip` -> **Extract All...** and choose a folder,
   e.g. `C:\Workflow`. You'll get `C:\Workflow\ftworkflow`.
2. Open PowerShell in that folder. Easiest: open the `ftworkflow` folder in File
   Explorer, then type `powershell` in the address bar and press Enter. Or:
   ```powershell
   cd C:\Workflow\ftworkflow
   ```
   Make sure you're in the folder that contains `docker-compose.yml`:
   ```powershell
   dir docker-compose.yml
   ```

## B3. Configure the environment

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in the same values as Linux: `DJANGO_SECRET_KEY` (from the generate step),
`POSTGRES_PASSWORD`, `DJANGO_ALLOWED_HOSTS` (your PC/server IP, or `localhost`
for local testing), and optionally the first-admin username/password. Save in
Notepad (`Ctrl+S`) and close it.

## B4. Build and start

```powershell
docker compose up -d --build
```

Watch the logs until `Starting gunicorn`:

```powershell
docker compose logs -f web
```

Press `Ctrl+C` to stop following (the app keeps running).

## B5. Create the first administrator (if you skipped it in `.env`)

```powershell
docker compose exec web python manage.py createsuperuser
```

## B6. Visit the app

Open `http://localhost:8000` (same PC) or `http://WINDOWS_PC_IP:8000` from
another machine on the network. On Windows, Docker Desktop publishes the port
automatically; if other machines can't reach it, allow the port in Windows
Defender Firewall for Docker.

---

# 6. Everyday operations (Linux & Windows)

The commands below are identical in bash and PowerShell.

```bash
docker compose ps                 # status
docker compose logs -f web        # live logs
docker compose restart web        # restart just the app
docker compose down               # stop everything (data is kept in volumes)
docker compose up -d              # start again
```

# 7. Update to a new version

When you receive an updated project zip:

1. Back up the database first (section 8).
2. Replace the project files with the new version (unzip over the folder).
3. Rebuild:
   ```bash
   docker compose up -d --build
   ```

Your data is **not** lost on rebuild — the database, uploads and code tables
live in Docker **named volumes**, not in the image.

That cuts both ways for the reference code tables: a named volume is filled from
the image only when Docker first creates it, and from then on it hides whatever
the image carries. So if a new version ships a **corrected** code table, the
rebuild does **not** replace the one already in `code_db_data`, and nothing in
the logs or the UI says so. Import the corrected table from the admin **Tool
Data** page after the upgrade.

# 8. Back up and restore the database

Back up:

```bash
docker compose exec db pg_dump -U ftworkflow ftworkflow > backup.sql
```

Restore:

```bash
docker compose exec -T db psql -U ftworkflow ftworkflow < backup.sql
```

# 9. Change the starting case number

The four-digit case serial is seeded in code. Open `cases/codes.py` and find:

```python
key="case_serial", defaults={"value": 1802},
```

Set the value to **(your desired first number minus 1)** — e.g. `1802` to make
the first case `1803`. This only matters on a fresh database, before the first
case is created. After editing, rebuild with `docker compose up -d --build`.

Current seed in code: `defaults={"value": 1802}` → first case serial **1803**.

# 10. (Optional) Domain name + HTTPS

To serve on a domain with automatic HTTPS, put a reverse proxy (e.g. Caddy) in
front of the app on port 8000, then set in `.env`:

```
DJANGO_ALLOWED_HOSTS=workflow.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://workflow.example.com
DJANGO_SECURE_SSL=1
```

and run `docker compose up -d` to apply.

`DJANGO_SECURE_SSL=1` is what actually makes the deployment an HTTPS one: it
marks the session and CSRF cookies **Secure** (so they are never sent over plain
HTTP), redirects HTTP to HTTPS, trusts the proxy's `X-Forwarded-Proto`, and
sends HSTS. Without it the site is reachable over HTTPS but the cookies are
still transmitted in the clear to anyone who opens `http://SERVER_IP:8000`
directly — the container port stays published on the LAN either way. Leave it
unset (or `0`) for a plain-HTTP internal install: turning it on without TLS in
front redirects every request to a URL nobody is serving.

# 11. The license volume

Activation state lives in the `license_data` volume and is **not** covered by
the automatic backups (they cover the database, uploads and code tables). That
is deliberate — it is not application data. If the volume is lost
(`docker compose down -v`, a wiped Docker install), the app redirects to the
activation page and you simply paste the same license string again, so **keep
that string somewhere outside the server**. A new license is only needed when
the machine itself changes.

---

## Notes

- The main database uses PostgreSQL and starts empty.
- Reference code tables (millions of rows) remain fast read-only SQLite files.
  Any that are present when the image is built are seeded into the
  `code_db_data` volume the first time that volume is created; they are not in
  version control, so a build from a fresh clone ships none. Tables you upload
  through the admin are persisted in that volume, and once it exists the volume
  takes precedence over the image (see section 7).
- Static files are served by WhiteNoise; `collectstatic` runs automatically on
  every start.
