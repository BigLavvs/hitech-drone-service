import boto3
from botocore.config import Config as BotoConfig
from celery import current_app
from django.conf import settings
from django.db import connections
from django.http import JsonResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from redis import Redis
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from apps.files.storage import PrivateR2StorageAdapter

HEALTH_RESPONSE_SERIALIZER = inline_serializer(
    name="HealthResponse",
    fields={
        "status": serializers.CharField(),
    },
)

READY_RESPONSE_SERIALIZER = inline_serializer(
    name="ReadyResponse",
    fields={
        "status": serializers.CharField(),
        "components": serializers.DictField(child=serializers.CharField()),
    },
)


@extend_schema(
    summary="Liveness probe",
    responses={200: HEALTH_RESPONSE_SERIALIZER},
    auth=[],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health_view(request):
    return JsonResponse({"status": "ok"}, status=200)


@extend_schema(
    summary="Readiness probe",
    responses={
        200: READY_RESPONSE_SERIALIZER,
        503: OpenApiResponse(response=READY_RESPONSE_SERIALIZER),
    },
    auth=[],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def ready_view(request):
    component_checks = {
        "database": _check_database,
        "redis": _check_redis,
        "r2": _check_r2,
        "celery_worker": _check_celery_worker,
    }
    components = {
        name: ("ok" if check() else "unavailable")
        for name, check in component_checks.items()
    }
    is_ready = all(status == "ok" for status in components.values())
    return JsonResponse(
        {
            "status": "ready" if is_ready else "unavailable",
            "components": components,
        },
        status=200 if is_ready else 503,
    )


def _check_database() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
    except Exception:
        return False
    return bool(row and row[0] == 1)


def _check_redis() -> bool:
    try:
        client = Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        return bool(client.ping())
    except Exception:
        return False


def _check_r2() -> bool:
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=BotoConfig(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}),
        )
        storage = PrivateR2StorageAdapter(client=client, bucket_name=settings.R2_BUCKET_NAME)
        storage.client.head_bucket(Bucket=storage.bucket_name)
    except Exception:
        return False
    return True


def _check_celery_worker() -> bool:
    try:
        inspect = current_app.control.inspect(timeout=2)
        ping_response = inspect.ping()
    except Exception:
        return False
    return bool(ping_response)
