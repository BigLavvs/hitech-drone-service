from django.conf import settings
from rest_framework.throttling import UserRateThrottle


class AuthenticatedGeneralRateThrottle(UserRateThrottle):
    scope = "general"

    def get_rate(self):
        return settings.RATE_LIMIT_GENERAL

    def allow_request(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return True
        return super().allow_request(request, view)
