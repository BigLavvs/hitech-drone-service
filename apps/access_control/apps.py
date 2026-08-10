from django.apps import AppConfig


class AccessControlConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.access_control"

    def ready(self):
        from . import schema  # noqa: F401
