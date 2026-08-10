import math
from collections.abc import Mapping

from drf_spectacular.utils import extend_schema_field
from django.contrib.gis.geos import Point
from rest_framework import serializers

from apps.access_control.models import User
from apps.projects.models import Project, ProjectMembership, Site


class ProjectReadSerializer(serializers.ModelSerializer):
    project_manager_id = serializers.IntegerField(allow_null=True)
    created_by = serializers.IntegerField(source="created_by_id", allow_null=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "description",
            "location",
            "status",
            "project_manager_id",
            "created_by",
            "created_at",
            "updated_at",
        )


class ProjectWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=True)
    description = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    location = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )
    project_manager_id = serializers.PrimaryKeyRelatedField(
        source="project_manager",
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def to_internal_value(self, data):
        _reject_unknown_fields(
            serializer=self,
            data=data,
            allowed_fields={"name", "description", "location", "project_manager_id"},
        )
        return super().to_internal_value(data)


class ProjectMemberReadSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="user_id")
    email = serializers.EmailField(source="user.email")
    role = serializers.CharField(source="user.role")

    class Meta:
        model = ProjectMembership
        fields = ("id", "email", "role")


class ProjectMemberCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "role")


class ProjectMemberCreateSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(source="user", queryset=User.objects.all())

    def to_internal_value(self, data):
        _reject_unknown_fields(
            serializer=self,
            data=data,
            allowed_fields={"user_id"},
        )
        return super().to_internal_value(data)


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "lat": {"type": "number"},
            "lng": {"type": "number"},
        },
        "required": ["lat", "lng"],
    }
)
class SiteCoordinatesField(serializers.Field):
    default_error_messages = {
        "invalid": "Coordinates must be an object with numeric lat and lng values.",
        "invalid_keys": "Coordinates must contain only lat and lng.",
        "lat_range": "Latitude must be between -90 and 90.",
        "lng_range": "Longitude must be between -180 and 180.",
    }

    def to_representation(self, value: Point):
        return {"lat": value.y, "lng": value.x}

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            self.fail("invalid")

        if set(data.keys()) != {"lat", "lng"}:
            self.fail("invalid_keys")

        lat = data.get("lat")
        lng = data.get("lng")

        if not _is_valid_json_number(lat) or not _is_valid_json_number(lng):
            self.fail("invalid")

        if not math.isfinite(lat):
            self.fail("invalid")

        if not math.isfinite(lng):
            self.fail("invalid")

        if lat < -90 or lat > 90:
            self.fail("lat_range")

        if lng < -180 or lng > 180:
            self.fail("lng_range")

        return Point(lng, lat, srid=4326)


class SiteReadSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField()
    coordinates = SiteCoordinatesField()

    class Meta:
        model = Site
        fields = (
            "id",
            "project_id",
            "name",
            "coordinates",
            "coordinate_reference_system",
            "created_at",
            "updated_at",
        )


class SiteWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=True)
    coordinates = SiteCoordinatesField(required=True)
    coordinate_reference_system = serializers.CharField(max_length=50, required=False)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def validate_coordinate_reference_system(self, value):
        if value != "EPSG:4326":
            raise serializers.ValidationError(
                "coordinate_reference_system must be EPSG:4326."
            )
        return value

    def to_internal_value(self, data):
        _reject_unknown_fields(
            serializer=self,
            data=data,
            allowed_fields={"name", "coordinates", "coordinate_reference_system"},
        )
        return super().to_internal_value(data)


def _reject_unknown_fields(*, serializer: serializers.Serializer, data, allowed_fields: set[str]) -> None:
    if not isinstance(data, Mapping):
        return

    unexpected_fields = sorted(set(data.keys()) - allowed_fields)
    if unexpected_fields:
        raise serializers.ValidationError(
            {
                field: [f"Unexpected field: {field}."]
                for field in unexpected_fields
            }
        )


def _is_valid_json_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )
