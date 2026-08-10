from .settings import *  # noqa: F403,F401


DATABASES = {
    "default": env.db("DATABASE_URL_TEST"),  # noqa: F405
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

DIRECT_URL = env("DIRECT_URL_TEST")  # noqa: F405
