from .settings import *  # noqa: F403,F401


DATABASES = {
    "default": env.db("DATABASE_URL"),  # noqa: F405
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

DIRECT_URL = env("DIRECT_URL")  # noqa: F405

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hitech-drone-mapping-test-cache",
        "KEY_PREFIX": "hitech-drone-mapping-test:",
    },
}

STORAGES = {  # noqa: F405
    **STORAGES,  # noqa: F405
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
