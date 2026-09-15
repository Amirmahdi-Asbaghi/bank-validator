"""Django settings for the bank validation project.

All environment-specific values come from `.env` (see `.env.example`
for the template). Code-level defaults are provided so the project
boots with sane values even if a variable is missing — but production
deployments should set every value explicitly.

Grouping:

    Paths           BASE_DIR
    Environment     env parsing setup
    Security        SECRET_KEY, DEBUG, ALLOWED_HOSTS
    Apps            INSTALLED_APPS, MIDDLEWARE
    Templates       TEMPLATES
    WSGI            WSGI_APPLICATION
    Database        DATABASES (Postgres via DATABASE_URL)
    Auth            password validators
    i18n            language and timezone
    Static          STATIC_URL
    DRF             parsers, renderers, schema
    Storage         MinIO (S3), ClickHouse, Kafka
    App constants   SMALL_FILE_THRESHOLD, MEDIA_*

The config is loaded once per process. Changes to `.env` require a
container restart (`docker compose up -d --force-recreate web`) — a
plain `restart` reuses the old environment.
"""
from pathlib import Path
import environ

# ---------- Paths ----------

# BASE_DIR is the project root (`/app` in the container).
# It's used to locate .env, the media folder, and any static assets.
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------- Environment ----------

# `environ.Env` is set up with default types so that env values are
# coerced automatically. Without the type hints, everything would be
# a string and we'd need to convert manually at every use.
#
# The defaults here apply when the env var is missing. They're chosen
# so that `docker compose up` works out of the box with a minimal
# `.env` (or even none — the app still boots).
env = environ.Env(
    # bool: parse "true"/"false"/"1"/"0" into Python bool
    DJANGO_DEBUG=(bool, False),
    # list: parse comma-separated values into a Python list
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "web"]),
    # int: parse into a Python int
    SMALL_FILE_THRESHOLD=(int, 50 * 1024 * 1024),
)

# Load .env from the project root. This must happen before the first
# `env(...)` call, or the values won't be read.
#
# `environ.Env.read_env` is idempotent and skips missing files —
# if `.env` doesn't exist, the app falls back to the defaults above.
environ.Env.read_env(BASE_DIR / ".env")

# ---------- Security ----------

# SECRET_KEY is used for cryptographic signing (sessions, CSRF, etc.).
# The default is intentionally unsafe — it should never be used in
# production. `django-admin check --deploy` warns about this.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-dev-key")

# DEBUG=True enables the Django debug page and detailed error output.
# Never set True in production — it leaks stack traces to clients.
DEBUG = env("DJANGO_DEBUG")

# Hosts that Django will respond to. The container name `web` is
# included so Prometheus scraping inside the Docker network works.
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# ---------- Applications ----------

INSTALLED_APPS = [
    # Django built-ins. Order matters for admin —
    # django.contrib.admin must come before other apps that
    # register their own admin classes.
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "rest_framework",   # API views, serializers, pagination
    "drf_spectacular",  # OpenAPI schema generation
    "storages",         # django-storages: S3 backend for FileFields

    # Local apps. Ordering is alphabetical; the only constraint is
    # that `apps.common` provides shared utilities used by others.
    "apps.common",      # Prometheus metrics
    "apps.ingestion",   # Upload view, parser, Kafka producer
    "apps.validation",  # Models, rules, admin, seed command
    "apps.reports",     # Read endpoints
    "apps.storage",     # MinIO and ClickHouse clients
]

# Middleware processes every request/response in the order listed.
# The order matters:
#   - SecurityMiddleware first (headers)
#   - Session before Auth (Auth needs the session)
#   - CSRF before any view that mutates state
#   - CommonMiddleware handles APPEND_SLASH
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# The Python module that holds URL routing. `config.urls` is where
# `urlpatterns` lives — the entry point for every request.
ROOT_URLCONF = "config.urls"

# Django templates. We barely use them (only the admin and DRF's
# browsable API), but the configuration is required for admin to work.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],           # no project-level template overrides
        "APP_DIRS": True,     # look in each app's templates/ folder
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# WSGI entry point. `config.wsgi:application` is what gunicorn calls
# in production.
WSGI_APPLICATION = "config.wsgi.application"


# ---------- Database ----------

# Parsed from a single URL rather than separate HOST/USER/PASSWORD
# fields. This makes the connection string copy-pasteable between
# tools and environments.
#
# Format: postgresql://user:password@host:port/dbname
DATABASES = {
    "default": env.db("DATABASE_URL"),
}


# ---------- Password validation ----------

# Django's default validators. They apply to the admin's user creation
# and password change forms. The API has no user-facing password
# change, so these matter only for the admin.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# ---------- Internationalization ----------

# English UI, but Persian bank names appear in the data — that's
# stored as UTF-8 strings, not affected by LANGUAGE_CODE.
LANGUAGE_CODE = "en-us"

# UTC everywhere. Timestamps are stored in UTC and serialized with
# the `Z` suffix. Clients convert to local time. This is the standard
# approach — never store local time.
TIME_ZONE = "UTC"

USE_I18N = True
USE_TZ = True


# ---------- Static files ----------

# Where Django serves static files from in development. Not used in
# this project (no custom CSS/JS), but required by the admin.
STATIC_URL = "static/"

# Default primary key field type for models that don't declare one.
# BigAutoField uses 64-bit integers, avoiding overflow on large tables.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------- Django REST Framework ----------

REST_FRAMEWORK = {
    # The schema generator used by drf-spectacular. Every view that
    # uses @extend_schema builds on this.
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",

    # Parsers for request bodies, in order of preference. JSON first
    # (most common), then multipart (for file uploads), then form.
    # DRF tries each parser against the Content-Type header.
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],

    # Renderers for responses. JSON for clients, BrowsableAPIRenderer
    # for the HTML UI at /api/v1/... (useful during development).
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# drf-spectacular's OpenAPI metadata. These values appear in the
# generated schema and in Swagger UI's header.
SPECTACULAR_SETTINGS = {
    "TITLE": "Bank Validation API",
    "DESCRIPTION": "Validation platform for bank reporting files",
    "VERSION": "1.0.0",
    # Don't include the schema endpoint itself in the generated schema
    # (would be recursive noise).
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------- MinIO / S3 ----------

# MinIO is our S3-compatible object store in dev. In production, these
# would point at real S3 (or Ceph, or any S3-compatible endpoint).
MINIO_ENDPOINT = env("MINIO_ENDPOINT", default="http://minio:9000")
MINIO_ROOT_USER = env("MINIO_ROOT_USER", default="minioadmin")
MINIO_ROOT_PASSWORD = env("MINIO_ROOT_PASSWORD", default="minioadmin")

# Three buckets: raw (input files), curated (valid Delta), quarantine
# (invalid Delta). The names are configured here so different
# environments can use different bucket names if needed.
MINIO_BUCKET_RAW = env("MINIO_BUCKET_RAW", default="raw")
MINIO_BUCKET_CURATED = env("MINIO_BUCKET_CURATED", default="curated")
MINIO_BUCKET_QUARANTINE = env("MINIO_BUCKET_QUARANTINE", default="quarantine")

# django-storages uses the AWS_* namespace by convention, even when
# the backend is MinIO. These map our MINIO_* values into the
# framework's expected names.
#
# The alternative would be to name these MINIO_* and configure
# django-storages explicitly — but that fights the framework.
# Following the AWS naming means any S3-compatible backend works
# without a code change.
AWS_ACCESS_KEY_ID = MINIO_ROOT_USER
AWS_SECRET_ACCESS_KEY = MINIO_ROOT_PASSWORD
AWS_STORAGE_BUCKET_NAME = MINIO_BUCKET_RAW
AWS_S3_ENDPOINT_URL = MINIO_ENDPOINT
AWS_S3_REGION_NAME = "us-east-1"

# MinIO in dev runs over HTTP, not HTTPS.
# In production (real S3), these would be True.
AWS_S3_USE_SSL = False
AWS_S3_VERIFY = False

# Don't add auth query strings to generated URLs. We don't use
# pre-signed URLs; direct boto3 calls already carry auth headers.
AWS_QUERYSTRING_AUTH = False

# ---------- Kafka ----------

# Where the broker lives. For local dev, the compose service name
# `kafka` resolves to the broker container. For production, a
# comma-separated list of brokers (e.g. "b1:9092,b2:9092,b3:9092").
KAFKA_BOOTSTRAP_SERVERS = env("KAFKA_BOOTSTRAP_SERVERS", default="kafka:9092")

# The topic where upload events are published. Consumed by Spark
# Structured Streaming.
KAFKA_TOPIC_UPLOADS = env("KAFKA_TOPIC_UPLOADS", default="bank-uploads")

# ---------- ClickHouse ----------

# The analytical store. Written by Spark (not Django), read by the
# API's summary endpoint fallback.
CLICKHOUSE_HOST = env("CLICKHOUSE_HOST", default="clickhouse")
# 8123 is ClickHouse's HTTP port. The native protocol port is 9000,
# but we use the HTTP client (clickhouse-connect).
CLICKHOUSE_PORT = env.int("CLICKHOUSE_PORT", default=8123)
CLICKHOUSE_DB = env("CLICKHOUSE_DB", default="bankval")
CLICKHOUSE_USER = env("CLICKHOUSE_USER", default="default")
# Empty password for the default user in local dev. In production,
# this would come from a secret manager.
CLICKHOUSE_PASSWORD = env("CLICKHOUSE_PASSWORD", default="")


# ---------- App-level constants ----------

# The sync/async threshold. Files smaller than this are validated
# in-process with pandas; larger files go through Kafka → Spark.
#
# 50 MB is chosen so that Spark's ~10s startup is only paid for
# files big enough to benefit. Small files finish in milliseconds
# with pandas.
#
# Defined here so the view reads it from settings, and the .env can
# override it without a code change.
SMALL_FILE_THRESHOLD = env.int("SMALL_FILE_THRESHOLD", default=50 * 1024 * 1024)


# Media files — used by the pre-MinIO version of the storage layer.
# Kept because Django's admin may reference MEDIA_ROOT for uploads
# (e.g. if a future feature adds file fields to a model).
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"