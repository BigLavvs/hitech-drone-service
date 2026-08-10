from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class HitechJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.access_control.authentication.HitechJWTAuthentication"
    name = "HitechJWTCookieAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": settings.HITECH_AUTH_ACCESS_COOKIE_NAME,
            "description": (
                "HttpOnly Hitech JWT access cookie. Unsafe same-origin requests also require "
                "the `csrftoken` cookie value in the `X-CSRFToken` header."
            ),
        }
