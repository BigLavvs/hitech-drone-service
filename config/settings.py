"""Environment-based Django, API, authentication, storage and worker settings."""

import os
from pathlib import Path

import environ


BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

WINDOWS_GDAL_DIR = Path("C:/Program Files/GDAL")
if os.name == "nt" and WINDOWS_GDAL_DIR.exists():
    os.add_dll_directory(str(WINDOWS_GDAL_DIR))
    GDAL_LIBRARY_PATH = str(WINDOWS_GDAL_DIR / "gdal.dll")
    GEOS_LIBRARY_PATH = str(WINDOWS_GDAL_DIR / "geos_c.dll")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[],
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.access_control.apps.AccessControlConfig",
    "apps.projects.apps.ProjectsConfig",
    "apps.surveys.apps.SurveysConfig",
    "apps.files.apps.FilesConfig",
    "apps.processing.apps.ProcessingConfig",
    "apps.approvals.apps.ApprovalsConfig",
    "apps.audit.apps.AuditConfig",
    "apps.maps.apps.MapsConfig",
    "apps.models3d.apps.Models3DConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "config.middleware.ContentSecurityPolicyMiddleware",
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
        "DIRS": [BASE_DIR / "templates"],
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
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

# Used by config.settings_migrations for direct database connections.
DIRECT_URL = env("DIRECT_URL")
ENABLE_DEMO_AUTH = env.bool("ENABLE_DEMO_AUTH", default=False)
DEMO_AUTH_PRIVATE_KEY = env("DEMO_AUTH_PRIVATE_KEY", default="")
DEMO_AUTH_PRIVATE_KEY_PATH = env(
    "DEMO_AUTH_PRIVATE_KEY_PATH",
    default=str(BASE_DIR / ".demo-auth" / "private_key.pem"),
)
DEMO_AUTH_PUBLIC_KEY_PATH = env(
    "DEMO_AUTH_PUBLIC_KEY_PATH",
    default=str(BASE_DIR / ".demo-auth" / "public_key.pem"),
)
DEMO_AUTH_TOKEN_TTL_SECONDS = max(
    60,
    min(env.int("DEMO_AUTH_TOKEN_TTL_SECONDS", default=1800), 1800),
)

_hitech_auth_jwt_public_key = env("HITECH_AUTH_JWT_PUBLIC_KEY", default="")
if not _hitech_auth_jwt_public_key and DEBUG and ENABLE_DEMO_AUTH:
    demo_public_key_path = Path(DEMO_AUTH_PUBLIC_KEY_PATH)
    if demo_public_key_path.exists():
        _hitech_auth_jwt_public_key = demo_public_key_path.read_text(encoding="utf-8")

HITECH_AUTH_JWT_PUBLIC_KEY = _hitech_auth_jwt_public_key
HITECH_AUTH_ACCESS_COOKIE_NAME = env(
    "HITECH_AUTH_ACCESS_COOKIE_NAME",
    default="hitech_access_token",
)
CSRF_COOKIE_HTTPONLY = False

AUTH_USER_MODEL = "access_control.User"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_BEAT_SCHEDULE = {
    "reconcile-stale-queued-processing-jobs": {
        "task": "apps.processing.reconcile_stale_queued_processing_jobs",
        "schedule": env.int("PROCESSING_DISPATCH_RECONCILE_INTERVAL_SECONDS", default=60),
    },
    "reconcile-expired-running-processing-jobs": {
        "task": "apps.processing.reconcile_expired_running_processing_jobs",
        "schedule": env.int("PROCESSING_RUNNING_RECONCILE_INTERVAL_SECONDS", default=60),
    },
}

PROCESSING_QUEUED_DISPATCH_STALE_AFTER_SECONDS = max(
    60,
    env.int("PROCESSING_QUEUED_DISPATCH_STALE_AFTER_SECONDS", default=300),
)
PROCESSING_RECONCILE_BATCH_SIZE = max(
    1,
    min(env.int("PROCESSING_RECONCILE_BATCH_SIZE", default=25), 100),
)
PROCESSING_RUNNING_LEASE_SECONDS = max(
    300,
    env.int("PROCESSING_RUNNING_LEASE_SECONDS", default=900),
)
PROCESSING_HEARTBEAT_INTERVAL_SECONDS = max(
    30,
    min(env.int("PROCESSING_HEARTBEAT_INTERVAL_SECONDS", default=60), PROCESSING_RUNNING_LEASE_SECONDS // 2),
)

R2_ENDPOINT_URL = env("R2_ENDPOINT_URL")
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = env("R2_BUCKET_NAME")
R2_PUBLIC_URL = env("R2_PUBLIC_URL", default="").rstrip("/")
POTREE_CONVERTER_PATH = env("POTREE_CONVERTER_PATH", default="")

MAX_FILE_SIZE_BYTES = env.int("MAX_FILE_SIZE_BYTES")
MAX_SURVEY_TOTAL_SIZE_BYTES = env.int("MAX_SURVEY_TOTAL_SIZE_BYTES")
UPLOAD_CHUNK_SIZE_BYTES = env.int("UPLOAD_CHUNK_SIZE_BYTES")
RATE_LIMIT_UPLOAD = env("RATE_LIMIT_UPLOAD")
RATE_LIMIT_RETRY = env("RATE_LIMIT_RETRY")
RATE_LIMIT_LOGIN = env("RATE_LIMIT_LOGIN", default="5/m")
RATE_LIMIT_GENERAL = env("RATE_LIMIT_GENERAL", default="100/m")

REDIS_CACHE_KEY_PREFIX = env(
    "REDIS_CACHE_KEY_PREFIX",
    default="hitech-drone-mapping:cache:",
)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "KEY_PREFIX": REDIS_CACHE_KEY_PREFIX,
    },
}

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
if DEBUG:
    from config.dev_static import enable_dev_static_no_cache

    enable_dev_static_no_cache()

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "config.throttling.AuthenticatedGeneralRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "general": RATE_LIMIT_GENERAL,
        "login": RATE_LIMIT_LOGIN,
        "upload": RATE_LIMIT_UPLOAD,
        "retry": RATE_LIMIT_RETRY,
    },
}

LOG_LEVEL = env("LOG_LEVEL", default="INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "config.logging.JsonLogFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Hitech Drone Mapping Service API",
    "DESCRIPTION": (
        "Versioned API documentation for the Hitech Drone Mapping Service. "
        "Protected operations require the `hitech_access_token` HttpOnly cookie. "
        "Unsafe same-origin requests also require the `csrftoken` cookie to be echoed in the "
        "`X-CSRFToken` header."
    ),
    "VERSION": "v1",
    "SERVE_PUBLIC": True,
}
