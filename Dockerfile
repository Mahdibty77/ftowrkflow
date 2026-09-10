# ---------------------------------------------------------------------------
# Foolad Tabar Workflow - production image
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Keep Python lean and predictable inside the container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages. build-essential + libpq-dev let every dependency install
# cleanly on both amd64 and arm64 servers even if a pre-built wheel is missing,
# so the image builds the same way everywhere with no manual debugging.
#
# gettext supplies msgfmt/msguniq/xgettext - the toolchain
# `manage.py makemessages` / `compilemessages` shell out to when a later stage
# adds or updates a translated string (see locale/fa/LC_MESSAGES/django.po's
# own header). It is NOT needed to serve the app: the compiled
# locale/fa/LC_MESSAGES/django.mo this repo ships is read at runtime by
# Python's own stdlib `gettext` module, which has no external dependency at
# all. Installing the toolchain here only means a shell inside this same image
# (`docker compose exec web python manage.py compilemessages`) can rebuild
# that catalog after editing the .po, instead of needing a second machine that
# happens to have gettext on its PATH.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gettext \
        chromium \
        fonts-liberation \
        fonts-noto-core \
        fonts-noto-extra \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Headless Chromium used by TO/PI PDF export (cases/pdf_export.py).
ENV CHROME_PATH=/usr/bin/chromium \
    CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage"

WORKDIR /app

# Install Python dependencies first so this layer is cached across code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy the rest of the project.
COPY . .

# The entrypoint runs migrations, ensures the schema, collects static files,
# optionally creates the first admin, then launches gunicorn. We strip any
# carriage returns first so the script runs even if the project was unzipped on
# Windows (CRLF line endings would otherwise break the shebang in the container).
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
