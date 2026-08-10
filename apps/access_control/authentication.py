import jwt
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.validators import validate_email
from django.forms import ValidationError as FormsValidationError
from rest_framework import authentication
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from apps.access_control.models import UserRole
from apps.access_control.services import resolve_active_user_from_external_identity

REQUIRED_JWT_CLAIMS = ("sub", "email", "role", "exp")


class HitechJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        token = request.COOKIES.get(settings.HITECH_AUTH_ACCESS_COOKIE_NAME)
        if not token:
            raise AuthenticationFailed("Authentication token was not provided.")

        claims = _decode_hitech_access_token(token)
        user = resolve_active_user_from_external_identity(
            external_id=claims["sub"],
            email=claims["email"],
            role=claims["role"],
        )
        if user is None:
            raise AuthenticationFailed("Authenticated user is not permitted.")

        if request.method not in ("GET", "HEAD", "OPTIONS", "TRACE"):
            self._enforce_csrf(request)

        return (user, None)

    def authenticate_header(self, request):
        return f'Cookie realm="{settings.HITECH_AUTH_ACCESS_COOKIE_NAME}"'

    def _enforce_csrf(self, request) -> None:
        check = CSRFCheck(lambda request: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise PermissionDenied(f"CSRF Failed: {reason}")


def _decode_hitech_access_token(token: str) -> dict:
    public_key = settings.HITECH_AUTH_JWT_PUBLIC_KEY
    if not public_key:
        raise ImproperlyConfigured("HITECH_AUTH_JWT_PUBLIC_KEY is required for JWT validation.")

    try:
        claims = jwt.decode(
            token,
            key=_normalize_public_key(public_key),
            algorithms=["RS256"],
            options={"require": list(REQUIRED_JWT_CLAIMS)},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed("Invalid authentication token.") from exc

    _validate_required_claim_values(claims)
    return claims


def _normalize_public_key(public_key: str) -> str:
    return public_key.replace("\\n", "\n")


def _validate_required_claim_values(claims: dict) -> None:
    sub = claims.get("sub")
    email = claims.get("email")
    role = claims.get("role")

    if not isinstance(sub, str) or not sub.strip():
        raise AuthenticationFailed("Invalid authentication token.")

    if not isinstance(email, str):
        raise AuthenticationFailed("Invalid authentication token.")

    try:
        validate_email(email)
    except FormsValidationError as exc:
        raise AuthenticationFailed("Invalid authentication token.") from exc

    if role not in UserRole.values:
        raise AuthenticationFailed("Invalid authentication token.")
