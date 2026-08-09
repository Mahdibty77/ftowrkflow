#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# Container start-up: prepare the database and static files, then serve.
# Runs every time the web container starts; all steps are idempotent.
# ---------------------------------------------------------------------------
set -e

echo "==> Applying database migrations..."
python manage.py migrate --run-syncdb --noinput

# ---------------------------------------------------------------------------
# Schema-drift safety net (item 7 of the review document) — being retired.
#
# Historically the migration history did not cover every model, so this step
# issued hand-written CREATE TABLE / ALTER TABLE statements for the gaps, per
# database vendor, swallowing every error. That is what let the development
# machine, this server and each fresh install drift apart without anyone
# noticing.
#
# Both remaining gaps are now closed by real migrations: the currency-conversion
# audit table (cases/0007) and the export audit table (cases/0008). Until those
# were written, this step was the ONLY thing in the whole codebase that created
# either table — so turning it off before them would have left every new
# install unable to export a document at all.
#
# Retiring it is a two-step change, deliberately not done in one go on a live
# system:
#   1. Deploy this version and let it migrate. Then confirm BOTH of these:
#        - `python manage.py showmigrations cases` reports every migration
#          applied, through 0008.
#        - the start-up log contains no "cases schema sync ..." warnings.
#          Any such line names a table or column still being patched by hand
#          and means step 2 is not safe yet.
#   2. Once a deploy is clean on both counts, drop `--run-syncdb` from the
#      migrate line above and set FT_SKIP_SCHEMA_SYNC=1 (already supported —
#      see cases/apps.py) to turn this step off for good. From then on the
#      migration history is the single source of truth for the schema.
# ---------------------------------------------------------------------------
echo "==> Ensuring schema columns (drift safety net; see comment above)..."
python manage.py ensure_schema

# Create the database-backed cache table (shared across gunicorn workers, used
# by the login throttle). Idempotent: does nothing if the table already exists.
echo "==> Ensuring cache table..."
python manage.py createcachetable

echo "==> Collecting static files..."
python manage.py collectstatic --noinput

# Optionally create the very first administrator from environment variables.
# Only runs when both a username and password are provided, and never overwrites
# an existing user.
#
# The name is not free-form. Platform-administrator rights come from the
# reserved login "admin" and from nothing else (accounts/signals.py explains
# why: Profile.is_admin also decides who is EXCLUDED from being a document
# signatory, so it cannot simply be handed to every Django superuser). An
# account created here under any other name is a Django superuser who can reach
# /admin/ but none of the product's own screens — a half-working administrator,
# which is worse than a clear failure. So say so and skip, rather than produce
# one.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ "$DJANGO_SUPERUSER_USERNAME" != "admin" ]; then
  echo "==> !! DJANGO_SUPERUSER_USERNAME is '$DJANGO_SUPERUSER_USERNAME', but the platform"
  echo "       administrator must be named exactly 'admin'. No account was created."
  echo "       Set DJANGO_SUPERUSER_USERNAME=admin in .env and start again."
  DJANGO_SUPERUSER_USERNAME=""
fi
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "==> Ensuring admin user '$DJANGO_SUPERUSER_USERNAME'..."
  # The credentials are read from the environment INSIDE Python, never pasted
  # into the Python source. Pasting them meant the shell expanded them first, so
  # a password containing $ was silently truncated (the admin was created with a
  # different password than the operator configured, and the log still said
  # "admin created"), a ' ended the string literal, and $( ) / backticks would
  # have run as shell commands before Python ever saw them.
  python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
U = get_user_model()
u = os.environ['DJANGO_SUPERUSER_USERNAME']
p = os.environ['DJANGO_SUPERUSER_PASSWORD']
e = os.environ.get('DJANGO_SUPERUSER_EMAIL', '')
if U.objects.filter(username=u).exists():
    print('    admin already exists, leaving it unchanged')
else:
    U.objects.create_superuser(username=u, email=e, password=p)
    print('    admin created')
" || echo "    !! ADMIN NOT CREATED - the step above failed. Create one with: docker compose exec web python manage.py createsuperuser"
fi

echo "==> Starting gunicorn on 0.0.0.0:8000..."
exec gunicorn ftworkflow.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-600}" \
  --access-logfile - \
  --error-logfile -
