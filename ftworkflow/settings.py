"""
Django settings for the Foolad Tabar Workflow platform.

This single project hosts several cooperating apps:
    core      - shared base templates, theming and dashboards entry point
    accounts  - users, profiles, units, roles, signatures, admin user creation
    cases     - the heart: cases (files), clients, expert codes, forms, workflow
    itemcoder - item coding / pricing engine (Tool Data + Build TO/PI)
    reports   - management dashboards and reporting

Environment variables (all optional in development):
    DJANGO_SECRET_KEY   - production secret key
    DJANGO_DEBUG        - "1"/"0"
    DJANGO_ALLOWED_HOSTS- comma separated host list
    DJANGO_DB_ENGINE    - "sqlite" (default) or "postgres"
    POSTGRES_*          - connection details when DJANGO_DB_ENGINE=postgres
"""

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Core security settings
# ---------------------------------------------------------------------------
# Default False so a misconfigured production deploy never ships with DEBUG on.
# Local/dev: set DJANGO_DEBUG=1 in the environment (or .env).
DEBUG = _env_bool("DJANGO_DEBUG", False)

# The secret key must be supplied via the environment. In development we fall
# back to an ephemeral generated key (fine because DEBUG restarts often); in
# production a missing/placeholder key is a hard error so a real deployment can
# never silently run on a shared, guessable key.
_INSECURE_KEY = "django-insecure-change-this-key-before-deploying-to-production-server"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY or SECRET_KEY == _INSECURE_KEY:
    if DEBUG:
        import secrets as _secrets
        SECRET_KEY = _secrets.token_urlsafe(64)
    else:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is not set (or is the insecure placeholder). "
            "Set a strong, unique DJANGO_SECRET_KEY in the environment before "
            "running with DJANGO_DEBUG=0."
        )

# Intentionally open to any host. DJANGO_ALLOWED_HOSTS is documented for
# operators and used by Compose, but the app keeps ["*"] so existing
# deployments are not broken by an incomplete host list.

ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "cases.apps.CasesConfig",
    "reports.apps.ReportsConfig",
    "people.apps.PeopleConfig",
    # Item-coding / pricing engine (Build TO/PI + Tool Data)
    "itemcoder.apps.ItemcoderConfig",
    # Offline RSA license enforcement.
    "licensing.apps.LicensingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files efficiently in production.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Daily work-shift gate (after auth so request.user is set).
    "people.middleware.WorkShiftMiddleware",
    # License gate runs last: session, auth and messages are available, and it
    # redirects every non-allowlisted request to the activation page when the
    # software is not validly licensed.
    "licensing.middleware.LicenseGateMiddleware",
    # Password-change gate runs after the license gate (an invalid license
    # still takes priority for everyone), and redirects every non-allowlisted
    # request to the forced password-change screen for an account that has
    # must_change_password set — a freshly created account or one an admin
    # just reset. See accounts/middleware.py.
    "accounts.middleware.MustChangePasswordMiddleware",
]

ROOT_URLCONF = "ftworkflow.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Adds the active unit theme (colors) to every template.
                "core.context_processors.theme",
                "core.context_processors.tool_data_access",
                # Exposes is_impersonating / impersonator_username for the
                # "return to admin" banner.
                "core.context_processors.impersonation_status",
                # Work-shift countdown banner (last 30 minutes of the day).
                "people.context_processors.work_shift_banner",
                # Adds license_status (+ kartabl warning flag) to every template.
                "licensing.context_processors.license_status",
            ],
        },
    },
]

WSGI_APPLICATION = "ftworkflow.wsgi.application"
ASGI_APPLICATION = "ftworkflow.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
if os.environ.get("DJANGO_DB_ENGINE", "sqlite").lower() == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "ftworkflow"),
            "USER": os.environ.get("POSTGRES_USER", "ftworkflow"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Require a fresh sign-in for every new browser session: the session cookie is
# dropped when the browser closes, so credentials are never silently reused.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# ---------------------------------------------------------------------------
# Internationalization - the UI is English; timezone is local Iran time.
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Allow large Excel/CSV uploads for the admin code-table importer.
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB in-memory threshold
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Workflow policy switches
# ---------------------------------------------------------------------------
# When True (default): Technical cannot Submit / send a TO to Supply while any
# active row still lacks an FTCO code. When False: that FTCO-code gate is off —
# Technical may still send/return to Supply even if some TO rows have no code.
# Other gates (Technical Problem flags, PI remark blocks, etc.) are unchanged.
# Override via env: REQUIRE_FTCO_CODE_TO_SUPPLY=0
REQUIRE_FTCO_CODE_TO_SUPPLY = _env_bool("REQUIRE_FTCO_CODE_TO_SUPPLY", False)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# A database-backed cache is shared by every gunicorn worker (unlike the default
# per-process LocMemCache). The login throttle relies on this so a lockout is
# counted across all workers. The table is created idempotently on start
# (`manage.py createcachetable`, run from entrypoint.sh).
#
# Redis-ready, entirely opt-in: set REDIS_URL (and add a redis service — see
# docker-compose.yml) to switch to it. Until then, or if django-redis isn't
# installed yet, this falls back to exactly today's DatabaseCache behaviour,
# so deploying this code by itself changes nothing for a server that hasn't
# also added Redis.
_redis_url = os.environ.get("REDIS_URL", "").strip()
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "ft_cache",
    }
}
if _redis_url:
    try:
        import django_redis  # noqa: F401 - presence check only
    except ImportError:
        import warnings
        warnings.warn(
            "REDIS_URL is set but the django-redis package is not installed "
            "(pip install -r requirements.txt and rebuild). Using the "
            "database cache for now.",
            RuntimeWarning,
        )
    else:
        CACHES["default"] = {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _redis_url,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }

# ---------------------------------------------------------------------------
# Transport / cookie hardening
# ---------------------------------------------------------------------------
# These headers are always safe to send.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "SAMEORIGIN"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # the CSRF cookie is read by JS for AJAX posts
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# HTTPS-only protections are opt-in: many installs run over plain HTTP on an
# internal network (http://SERVER_IP:8000), where forcing HTTPS/secure cookies
# would lock everyone out. Turn them on with DJANGO_SECURE_SSL=1 once the app is
# served behind TLS (e.g. a reverse proxy / domain with HTTPS).
SECURE_SSL_ENABLED = _env_bool("DJANGO_SECURE_SSL", False)
if SECURE_SSL_ENABLED:
    # Trust the X-Forwarded-Proto header set by the TLS-terminating proxy.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
