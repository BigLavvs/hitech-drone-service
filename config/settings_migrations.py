from .settings import *  # noqa: F403,F401


DATABASES = {
    "default": env.db("DIRECT_URL"),  # noqa: F405
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"
