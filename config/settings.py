"""
Django settings for config project.
"""
from pathlib import Path
import environ

# ---------- Paths ----------
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------- Environment ----------
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "web"]),
    SMALL_FILE_THRESHOLD=(int, 50 * 1024 * 1024),
)

# Load .env from project root
environ.Env.read_env(BASE_DIR / ".env")

# ---------- Security ----------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-dev-key")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# ---------- Applications ----------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "rest_framework",
    "drf_spectacular",
    "storages",

    # Local apps
    "apps.common",
    "apps.ingestion",
    "apps.validation",
    "apps.reports",
    "apps.storage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
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

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    "default": env.db("DATABASE_URL"),
}


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

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


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------- Celery ----------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------- Django REST Framework ----------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Bank Validation API",
    "DESCRIPTION": "Validation platform for bank reporting files",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------- MinIO / S3 (django-storages) ----------
MINIO_ENDPOINT = env("MINIO_ENDPOINT", default="http://minio:9000")
MINIO_ROOT_USER = env("MINIO_ROOT_USER", default="minioadmin")
MINIO_ROOT_PASSWORD = env("MINIO_ROOT_PASSWORD", default="minioadmin")
MINIO_BUCKET_RAW = env("MINIO_BUCKET_RAW", default="raw")
MINIO_BUCKET_CURATED = env("MINIO_BUCKET_CURATED", default="curated")
MINIO_BUCKET_QUARANTINE = env("MINIO_BUCKET_QUARANTINE", default="quarantine")

AWS_ACCESS_KEY_ID = MINIO_ROOT_USER
AWS_SECRET_ACCESS_KEY = MINIO_ROOT_PASSWORD
AWS_STORAGE_BUCKET_NAME = MINIO_BUCKET_RAW
AWS_S3_ENDPOINT_URL = MINIO_ENDPOINT
AWS_S3_REGION_NAME = "us-east-1"
AWS_S3_USE_SSL = False
AWS_S3_VERIFY = False
AWS_QUERYSTRING_AUTH = False

# ---------- Kafka ----------
KAFKA_BOOTSTRAP_SERVERS = env("KAFKA_BOOTSTRAP_SERVERS", default="kafka:9092")
KAFKA_TOPIC_UPLOADS = env("KAFKA_TOPIC_UPLOADS", default="bank-uploads")

# ---------- ClickHouse ----------
CLICKHOUSE_HOST = env("CLICKHOUSE_HOST", default="clickhouse")
CLICKHOUSE_PORT = env.int("CLICKHOUSE_PORT", default=8123)
CLICKHOUSE_DB = env("CLICKHOUSE_DB", default="bankval")
CLICKHOUSE_USER = env("CLICKHOUSE_USER", default="default")
CLICKHOUSE_PASSWORD = env("CLICKHOUSE_PASSWORD", default="")


# ---------- App-level constants ----------
SMALL_FILE_THRESHOLD = env.int("SMALL_FILE_THRESHOLD", default=50 * 1024 * 1024)


MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"