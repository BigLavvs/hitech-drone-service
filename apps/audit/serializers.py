import re
from collections.abc import Mapping

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.audit.models import AuditAction, AuditLog
from apps.projects.serializers import _reject_unknown_fields

SENSITIVE_DETAIL_KEYS = {
    "authorization",
    "checksum",
    "convertedpath",
    "downloadurl",
    "jwt",
    "objectkey",
    "objectpath",
    "previewpath",
    "presignedurl",
    "r2key",
    "r2path",
    "sha256",
    "sha256checksum",
    "signedurl",
    "sourceurl",
    "storagekey",
    "storagepath",
    "token",
}
SENSITIVE_KEY_SUFFIXES = ("authorization", "checksum", "jwt", "key", "path", "token", "url")
PRIVATE_STORAGE_VALUE_RE = re.compile(r"^surveys/\d+/files/\d+/.+")
HEX_64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class AuditLogReadSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField(allow_null=True)
    survey_id = serializers.IntegerField(allow_null=True)
    user_id = serializers.IntegerField(allow_null=True)
    details = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "entity_type",
            "entity_id",
            "project_id",
            "survey_id",
            "user_id",
            "details",
            "timestamp",
        )

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_details(self, obj: AuditLog):
        return _sanitize_audit_details(obj.details)


class AuditLogListQuerySerializer(serializers.Serializer):
    project_id = serializers.IntegerField(min_value=1, required=False)
    survey_id = serializers.IntegerField(min_value=1, required=False)
    action = serializers.ChoiceField(choices=AuditAction.values, required=False)
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)
    offset = serializers.IntegerField(min_value=0, required=False)

    def validate(self, attrs):
        from_date = attrs.get("from_date")
        to_date = attrs.get("to_date")
        if from_date and to_date and from_date > to_date:
            raise serializers.ValidationError(
                {"to_date": ["to_date must be on or after from_date."]}
            )
        return attrs

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            _reject_unknown_fields(
                serializer=self,
                data=data,
                allowed_fields={
                    "project_id",
                    "survey_id",
                    "action",
                    "from_date",
                    "to_date",
                    "limit",
                    "offset",
                },
            )

        return super().to_internal_value(data)


def _sanitize_audit_details(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, nested_value in value.items():
            if _is_sensitive_detail_key(key):
                continue

            cleaned = _sanitize_audit_details(nested_value)
            if cleaned is not None:
                sanitized[key] = cleaned
        return sanitized

    if isinstance(value, list):
        sanitized = []
        for item in value:
            cleaned = _sanitize_audit_details(item)
            if cleaned is not None:
                sanitized.append(cleaned)
        return sanitized

    if isinstance(value, str):
        if _is_private_storage_value(value):
            return None
        if _is_signed_or_token_value(value):
            return None
        if HEX_64_RE.fullmatch(value):
            return None

    return value


def _normalize_detail_key(key):
    return re.sub(r"[^a-z0-9]+", "", str(key).lower())


def _is_sensitive_detail_key(key):
    normalized_key = _normalize_detail_key(key)
    if normalized_key in SENSITIVE_DETAIL_KEYS:
        return True
    if normalized_key.endswith(SENSITIVE_KEY_SUFFIXES):
        return any(
            marker in normalized_key
            for marker in ("object", "r2", "sha256", "signed", "storage", "download", "source")
        )
    return False


def _is_private_storage_value(value):
    return bool(PRIVATE_STORAGE_VALUE_RE.fullmatch(value))


def _is_signed_or_token_value(value):
    lowered = value.lower()
    return (
        "x-amz-signature=" in lowered
        or "x-amz-credential=" in lowered
        or lowered.startswith("bearer ")
        or JWT_RE.fullmatch(value) is not None
    )
