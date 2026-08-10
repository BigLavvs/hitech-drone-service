from django.conf import settings
from django.core.management.base import CommandError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.authentication import BaseAuthentication, CSRFCheck
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.views import APIView

from apps.access_control.authentication import HitechJWTAuthentication
from apps.access_control.demo_access import get_demo_user_spec, issue_demo_token_for_spec
from apps.access_control.models import User, UserRole
from apps.access_control.serializers import (
    AuthValidateResponseSerializer,
    DemoSessionCreateResponseSerializer,
    DemoSessionCreateSerializer,
    UserCreateSerializer,
    UserReadSerializer,
    UserUpdateSerializer,
)
from apps.access_control.services import (
    _UNSET,
    create_local_user,
    get_local_user_for_admin,
    get_local_users_for_admin,
    get_seeded_demo_user,
    update_local_user,
)


class AuthValidateView(APIView):
    authentication_classes: list[type[BaseAuthentication]] = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Validate the current Hitech JWT session",
        responses={200: AuthValidateResponseSerializer},
    )
    def get(self, request):
        user = request.user
        return Response(
            {
                "authenticated": True,
                "user": {
                    "id": user.id,
                    "external_id": user.external_id,
                    "email": user.email,
                    "role": user.role,
                },
            },
            status=status.HTTP_200_OK,
        )


class DemoSessionCreateView(APIView):
    authentication_classes: list[type[BaseAuthentication]] = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Create an assessment-only demo session",
        request=DemoSessionCreateSerializer,
        responses={
            200: DemoSessionCreateResponseSerializer,
            403: OpenApiResponse(description="CSRF validation failed."),
            404: OpenApiResponse(description="Assessment demo access is disabled."),
        },
    )
    def post(self, request):
        if not getattr(request, "_dont_enforce_csrf_checks", False):
            _enforce_request_csrf(request)

        if not settings.ENABLE_DEMO_AUTH:
            raise Http404

        serializer = DemoSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        spec = get_demo_user_spec(serializer.validated_data["role"])
        demo_user = get_seeded_demo_user(spec=spec) if spec is not None else None
        if demo_user is None or spec is None:
            raise DRFValidationError({"role": ["The selected demo role is unavailable in this environment."]})

        try:
            token = issue_demo_token_for_spec(spec=spec)
        except CommandError as exc:
            raise DRFValidationError(
                {"role": ["Assessment demo access is unavailable in this environment."]}
            ) from exc
        response = Response(
            {"redirect_to": reverse("projects")},
            status=status.HTTP_200_OK,
        )
        response.set_cookie(
            settings.HITECH_AUTH_ACCESS_COOKIE_NAME,
            token,
            max_age=settings.DEMO_AUTH_TOKEN_TTL_SECONDS,
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Lax",
            path="/",
        )
        return response


class UserLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class UserListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = UserLimitOffsetPagination
    serializer_class = UserReadSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return UserCreateSerializer
        return UserReadSerializer

    @extend_schema(
        summary="List local users",
        responses={200: UserReadSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        queryset = get_local_users_for_admin(actor=request.user)
        page = self.paginate_queryset(queryset)
        serializer = UserReadSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        summary="Create a local user",
        request=UserCreateSerializer,
        responses={201: UserReadSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = create_local_user(
                actor=request.user,
                email=serializer.validated_data["email"],
                external_id=serializer.validated_data["external_id"],
                role=serializer.validated_data["role"],
                is_active=serializer.validated_data["is_active"],
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc
        return Response(UserReadSerializer(user).data, status=status.HTTP_201_CREATED)


class UserDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserReadSerializer

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return UserUpdateSerializer
        return UserReadSerializer

    @extend_schema(
        summary="Update a local user",
        request=UserUpdateSerializer,
        responses={200: UserReadSerializer},
    )
    def patch(self, request, user_id, *args, **kwargs):
        serializer = UserUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            target_user = get_local_user_for_admin(actor=request.user, user_id=user_id)
        except User.DoesNotExist as exc:
            raise Http404 from exc

        try:
            user = update_local_user(
                actor=request.user,
                target_user=target_user,
                email=serializer.validated_data.get("email", _UNSET),
                role=serializer.validated_data.get("role", _UNSET),
                is_active=serializer.validated_data.get("is_active", _UNSET),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc
        return Response(UserReadSerializer(user).data)


@ensure_csrf_cookie
def admin_panel_view(request):
    user = _authenticate_template_request(request)
    if user is None:
        return redirect("/login")

    if user.role != UserRole.ADMINISTRATOR:
        return redirect("/projects")

    return render(
        request,
        "admin.html",
        {
            "active_nav": "admin",
            "page_title": "Administration | Hitech Drone Mapping",
            "ui_user_name": user.email,
            "ui_user_role": "Administrator",
            "ui_user_initials": user.email[:2].upper(),
            "ui_user_is_admin": True,
        },
    )


def _authenticate_template_request(request):
    authenticator = HitechJWTAuthentication()
    try:
        auth_result = authenticator.authenticate(request)
    except AuthenticationFailed:
        return None
    except PermissionDenied:
        return None

    if auth_result is None:
        return None

    user, _auth = auth_result
    request.user = user
    return user


def _enforce_request_csrf(request) -> None:
    check = CSRFCheck(lambda request: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied(f"CSRF Failed: {reason}")


def _to_drf_validation_error(exc: DjangoValidationError) -> DRFValidationError:
    if hasattr(exc, "message_dict"):
        return DRFValidationError(exc.message_dict)
    if hasattr(exc, "messages"):
        return DRFValidationError(exc.messages)
    return DRFValidationError(str(exc))
