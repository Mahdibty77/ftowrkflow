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
- `DJANGO_ALLOWED_HOSTS` — **this one does nothing.** It is passed through by
  `docker-compose.yml` and listed in `.env.example`, but `ftworkflow/settings.py`
  never reads it: `ALLOWED_HOSTS` is deliberately hard-coded to `["*"]` so that
  an incomplete host list can never lock an existing deployment out. Setting it
  is harmless and changes nothing — do not spend time on it, and do not record
  host validation as enforced because you filled it in. See the comment above
  `ALLOWED_HOSTS` in `ftworkflow/settings.py`.
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
`POSTGRES_PASSWORD`, and optionally the first-admin username/password.
(`DJANGO_ALLOWED_HOSTS` is inert — see the note in step A3.) Save in Notepad
(`Ctrl+S`) and close it.

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
DJANGO_CSRF_TRUSTED_ORIGINS=https://workflow.example.com
DJANGO_SECURE_SSL=1
```

`DJANGO_CSRF_TRUSTED_ORIGINS` and `DJANGO_SECURE_SSL` are both read and both
matter here. `DJANGO_ALLOWED_HOSTS` is not read at all (see step A3), so host
validation is not part of what this step turns on.

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

# 12. Installing the app on other computers (PWA), with no public domain

The site already carries everything needed for a browser's "Install app" /
"Add to Home Screen" prompt — a manifest (`static/manifest.json`) and a
registered service worker (`core/templates/base.html`). The one thing that
prompt refuses to appear without is **HTTPS** (or `localhost`) — that is a
browser security rule, not a setting in this app, so `http://192.168.1.50:8000`
never offers it on any computer other than the server itself, no matter what.

Section 10 covers HTTPS behind a real domain (Let's Encrypt). This section is
for the common case where that is not an option — an internal server with no
public domain. Nothing extra to install and nothing extra to run on the
server itself: the `caddy` service already defined in `docker-compose.yml`
(inactive unless you ask for it — see that file's own comment on the `caddy`
service) generates and manages its own certificate internally. The only
unavoidable manual step is telling OTHER computers to trust the certificate
Caddy generates — there is no way around that and still have a certificate
that means anything.

## 12.1. Turn the HTTPS front door on

In `.env` (same file as `POSTGRES_PASSWORD` etc. — see section A3), set the
address people actually type into their browser — the server's own LAN IP is
fine, e.g.:

```
FT_HTTPS_HOST=192.168.101.30
```

Then build/start with **one extra word** on the usual command:

```bash
sudo docker compose --profile https up -d --build
```

That is the entire server-side change from your normal deploy command.
Everything else — generating the certificate, keeping it across restarts,
redirecting plain HTTP to HTTPS — Caddy does on its own the first time it
starts. Watch `docker compose logs caddy` for `certificate obtained`. The app
is now reachable at `https://192.168.101.30` (plain `http://192.168.101.30`
redirects there automatically); `http://SERVER_IP:8000` keeps working exactly
as before for anyone who does not need the install prompt.

## 12.2. Trust that certificate on every other computer

Caddy's own certificate is not signed by a public authority (there is no
domain for one to vouch for), so a browser that has never seen it will show a
warning instead of a padlock — and will not offer the install prompt. Export
the certificate Caddy generated, once, from the server:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./ftworkflow-ca.crt
```

Copy `ftworkflow-ca.crt` onto each computer that should install the app and
add it to the OS's trusted root store:

- **Windows:** double-click it → **Install Certificate** → **Local Machine**
  → place it in **"Trusted Root Certification Authorities"**. For many
  computers at once, this is a standard Group Policy "Certificates" push.
- **macOS:** double-click it, then in Keychain Access set it to **Always
  Trust**.
- **Android:** Settings → Security → Encryption & credentials → Install a
  certificate → CA certificate.

Skipping this step does not block anything — the site still opens — but that
computer's browser will show a certificate warning and will not offer the
install prompt.

## 12.3. Install it

On a computer with the certificate trusted (12.2), open
`https://192.168.101.30`:

- **Chrome / Edge (desktop):** an install icon appears at the right end of the
  address bar; click it → **Install**.
- **Android Chrome:** menu (⋮) → **Install app**, or the banner Chrome shows
  on its own after a couple of visits.
- **iOS Safari:** Share button → **Add to Home Screen** (Safari ignores the
  manifest for this and always shows the option; it still needs HTTPS for the
  service worker behind it to register).

## 12.4. If you add or change the server's address later

Update `FT_HTTPS_HOST` in `.env` to the new address and re-run
`docker compose --profile https up -d --build`. Caddy issues itself a new
certificate for the new address automatically; repeat 12.2 for it (a
certificate for a different address is a different certificate, even from the
same CA).

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
