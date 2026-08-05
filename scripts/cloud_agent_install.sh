#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Cloud Agent / local development bootstrap for the Foolad Tabar Workflow
# platform. Idempotent: safe to re-run. Prepares a self-contained SQLite dev
# instance (the README "Quick start" flow) plus a local dev license so the
# licensing gate lets the app run without a vendor-issued key.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root
REPO_ROOT="$(pwd)"

# --- Python virtual environment -------------------------------------------
# Use a venv (matches README). On Ubuntu the stdlib venv needs python3-venv.
if [ ! -x ".venv/bin/python" ]; then
  if ! python3 -m venv .venv 2>/dev/null; then
    echo "==> Installing python3-venv (needed to create the virtualenv)…"
    sudo apt-get update -y
    sudo apt-get install -y "python3-venv" || sudo apt-get install -y "python$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')-venv"
    python3 -m venv .venv
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# --- Python dependencies ---------------------------------------------------
echo "==> Installing Python dependencies…"
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# Local development runs with SQLite + DEBUG (see README quick start).
export DJANGO_DEBUG=1

# --- Database migrations ---------------------------------------------------
# On a brand-new SQLite DB the first `migrate` fails at cases/0009 because
# cases_caseform.two_stage is created by the runtime schema-sync net
# (cases/apps.py), which only fires on the *next* process once the tables
# exist. Retrying once lets that net add the column and the remaining
# migrations then apply cleanly. On an already-migrated DB the first pass
# succeeds and no retry happens (so we never reverse-migrate).
echo "==> Applying database migrations…"
if ! python manage.py migrate --run-syncdb --noinput; then
  echo "==> First migrate pass failed (expected on a fresh DB); retrying so the schema-sync net can add the missing column…"
  python manage.py migrate --run-syncdb --noinput
fi

echo "==> Ensuring schema columns / cache table…"
python manage.py ensure_schema
python manage.py createcachetable

# --- Demo data -------------------------------------------------------------
# Idempotent: creates the admin, unit/role users and a couple of demo cases
# only if they do not already exist.
echo "==> Seeding demo data…"
python manage.py seed_demo

# --- Local development license --------------------------------------------
# The platform gates every request behind a signed, machine-bound license.
# For local dev we mint one against a throwaway dev keypair (see the script).
echo "==> Provisioning local development license…"
python scripts/dev_bootstrap_license.py

echo "==> Install complete. Start the app with: python manage.py runserver"
