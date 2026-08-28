"""
Django settings for the Foolad Tabar Workflow platform.

This single project hosts several cooperating apps. Start here, then open the
app you need:
    core      - shared base templates, theming, the landing router and the
                authenticated /media/ server
    accounts  - users, profiles, units, roles, signatures, admin user creation
    cases     - the heart: cases (files), clients, expert codes, forms, workflow
    itemcoder - item coding / pricing engine (Tool Data + Build TO/PI)
    reports   - management dashboards and reporting (read-only over `cases`)
    people    - personnel records, work shifts and staff requests
    marketing - the Marketing unit's own section, outside the case workflow
    licensing - offline RSA licence activation and the request gate

The URL map that mounts them all is ftworkflow/urls.py; it is the other half of
this front door and documents why a few paths are aliased at site root.

Environment variables this file reads. All but one have a working default, and
the exception is the one that catches people out: with nothing set at all,
DJANGO_DEBUG is False, and a False DEBUG makes DJANGO_SECRET_KEY mandatory — so
a bare checkout does not start. Export DJANGO_DEBUG=1 for local work, or a real
DJANGO_SECRET_KEY for anything else. (Nothing here reads a .env file.)
    DJANGO_SECRET_KEY            - production secret key; required when DEBUG=0
    DJANGO_DEBUG                 - "1"/"0" (default "0")
    DJANGO_CSRF_TRUSTED_ORIGINS  - comma separated origins for CSRF
    DJANGO_DB_ENGINE             - "sqlite" (default) or "postgres"
    POSTGRES_*                   - connection details when DJANGO_DB_ENGINE=postgres
    DJANGO_TIME_ZONE             - default "Asia/Tehran"
    REQUIRE_FTCO_CODE_TO_SUPPLY  - workflow policy switch, see below
    REDIS_URL                    - opt-in cache backend; falls back to the DB cache
    DJANGO_SECURE_SSL            - turn on HTTPS-only cookies/redirects/HSTS
    DJANGO_HSTS_SECONDS          - HSTS max-age when DJANGO_SECURE_SSL is on

DJANGO_ALLOWED_HOSTS is deliberately NOT in that list - see ALLOWED_HOSTS below.
"""

import os
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

# Open to any host, permanently, by the owner's decision. This is not an
# oversight and it is not a TODO: these installs are reached by bare IP on an
# internal network, and an incomplete host list has broken a deployment before.
#
# Consequently DJANGO_ALLOWED_HOSTS is NOT read anywhere in this project. It may
# still appear in docker-compose.yml / .env for operators, where it is inert.
# Setting it changes nothing — please do not spend an afternoon wiring it up.
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
    # The Marketing unit's own section. Outside the TO/PI case workflow by
    # design (see the accounts.constants docstring): it holds no case and
    # touches no routing rule.
    "marketing.apps.MarketingConfig",
    # Item-coding / pricing engine (Build TO/PI + Tool Data)
    "itemcoder.apps.ItemcoderConfig",
    # Offline RSA license enforcement.
    "licensing.apps.LicensingConfig",
]

# This order is load-bearing; please read it before moving anything.
#
# The first eight entries are Django's own stack, in Django's own order, plus
# WhiteNoise (which serves static files efficiently in production and must sit
# high enough to answer before anything else looks at the request).
#
# The last three are this project's gates. Each one inspects the request and may
# redirect it somewhere else instead of letting it through, so their relative
# order decides who wins when more than one of them is unhappy at the same time.
# Two constraints fix that order:
#
#   * all three read ``request.user``, so all three must stay BELOW
#     AuthenticationMiddleware. That is the only hard requirement any of them
#     has; none of the three uses the messages framework, so sitting under
#     MessageMiddleware is simply where the end of Django's own stack puts them,
#     not a constraint of their own;
#   * the shift gate goes first, not because it is the widest rule but because
#     it is the only one that does more than redirect: it logs the session out,
#     and there is no sense asking a session that is about to end for a licence
#     or a new password. Widest it is not — ``work_shift.shift_exempt`` lets
#     superusers, administrators and general managers straight through, and
#     ``shift_window`` reads each ``Person``'s own work_start / work_end, so it
#     is a per-person schedule, not an install-wide switch. The remaining two do
#     go widest first: an unlicensed install is locked for everybody, and only
#     then is a single account asked to change its password. Put the password
#     gate first and a locked-out install would hide behind a password prompt.
#
# Each gate keeps its own allow-list of paths that stay reachable while it is
# closed — the activation page, the password-change screen, static files — so a
# closed gate can still be opened. See the three middleware modules for those.
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
    # Ends a session when the user's daily work shift is over.
    "people.middleware.WorkShiftMiddleware",
    # Sends every non-allowlisted request to the activation page while the
    # software is not validly licensed.
    "licensing.middleware.LicenseGateMiddleware",
    # Sends every non-allowlisted request to the forced password-change screen
    # for an account with must_change_password set — a freshly created account,
    # or one an admin just reset.
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
# Logging
# ---------------------------------------------------------------------------
# Without an explicit LOGGING dict Django installs its own default, in which the
# console handler is filtered by require_debug_true and the only other handler
# mails ADMINS. Production always runs DEBUG=False (docker-compose pins
# DJANGO_DEBUG=0) and ADMINS is empty and there is no mail server, so every
# unhandled 500 traceback was routed to two handlers that both discarded it —
# and because handlers *were* found, logging's last-resort fallback never fired
# either. All the operator saw was gunicorn's access line with a 500 on it.
#
# This sends records to the stream gunicorn already forwards, so tracebacks show
# up in `docker compose logs web` with no mail server and no extra package. No
# ADMINS/email backend is configured on purpose: there is no SMTP relay on these
# installs, and a half-configured one would fail silently in the same way.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        # Same level Django's own default uses, minus the DEBUG-only filter.
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # This is the logger that carries the traceback of an unhandled 500.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
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
