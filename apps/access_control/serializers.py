from collections.abc import Mapping

from rest_framework import serializers

from apps.access_control.demo_access import DEMO_USER_SPECS
from apps.access_control.models import User, UserRole


class UserReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "external_id",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        )


class AuthValidatedUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    external_id = serializers.CharField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=UserRole.values)


class AuthValidateResponseSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField()
    user = AuthValidatedUserSerializer()


class DemoSessionCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=sorted(DEMO_USER_SPECS.keys()))

    def to_internal_value(self, data):
        _reject_unknown_fields(
            data=data,
            allowed_fields={"role"},
        )
        return super().to_internal_value(data)


class DemoSessionCreateResponseSerializer(serializers.Serializer):
    redirect_to = serializers.CharField()


class UserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    external_id = serializers.CharField(max_length=255)
    role = serializers.ChoiceField(choices=UserRole.values)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_external_id(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def to_internal_value(self, data):
        _reject_unknown_fields(
            data=data,
            allowed_fields={"email", "external_id", "role", "is_active"},
        )
        return super().to_internal_value(data)


class UserUpdateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    role = serializers.ChoiceField(choices=UserRole.values, required=False)
    is_active = serializers.BooleanField(required=False)

    def to_internal_value(self, data):
        _reject_unknown_fields(
            data=data,
            allowed_fields={"email", "role", "is_active"},
        )
        return super().to_internal_value(data)


def _reject_unknown_fields(*, data, allowed_fields: set[str]) -> None:
    if not isinstance(data, Mapping):
        return

    unexpected_fields = sorted(set(data.keys()) - allowed_fields)
    if unexpected_fields:
        raise serializers.ValidationError(
            {field: [f"Unexpected field: {field}."] for field in unexpected_fields}
        )
