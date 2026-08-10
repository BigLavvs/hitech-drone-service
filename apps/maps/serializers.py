import math
from collections.abc import Mapping

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.maps.models import Measurement, MeasurementType
from apps.projects.serializers import _is_valid_json_number, _reject_unknown_fields


class MapLayerDescriptorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    original_filename = serializers.CharField()
    format = serializers.CharField()
    bounds = serializers.ListField(child=serializers.FloatField(), required=False, allow_null=True)
    zoom_range = serializers.DictField(required=False, allow_null=True)
    tile_url_template = serializers.CharField(required=False, allow_null=True)
    source_url = serializers.URLField(required=False, allow_null=True)

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        return {key: value for key, value in payload.items() if value is not None}


@extend_schema_field(
    {
        "type": "array",
        "items": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 2,
        },
    }
)
class MeasurementCoordinatesField(serializers.Field):
    default_error_messages = {
        "invalid": "Coordinates must be a list of [longitude, latitude] positions.",
        "position": "Each coordinate must be a [longitude, latitude] pair.",
        "longitude_range": "Longitude must be between -180 and 180.",
        "latitude_range": "Latitude must be between -90 and 90.",
    }

    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        if not isinstance(data, list):
            self.fail("invalid")

        coordinates = []
        for position in data:
            if not isinstance(position, list) or len(position) != 2:
                self.fail("position")

            longitude, latitude = position
            if not _is_valid_json_number(longitude) or not _is_valid_json_number(latitude):
                self.fail("position")

            if not math.isfinite(longitude) or not math.isfinite(latitude):
                self.fail("position")

            if longitude < -180 or longitude > 180:
                self.fail("longitude_range")

            if latitude < -90 or latitude > 90:
                self.fail("latitude_range")

            coordinates.append([float(longitude), float(latitude)])

        return coordinates


class MeasurementReadSerializer(serializers.ModelSerializer):
    survey_id = serializers.IntegerField()
    created_by = serializers.IntegerField(source="created_by_id", allow_null=True)
    coordinates = MeasurementCoordinatesField()

    class Meta:
        model = Measurement
        fields = (
            "id",
            "survey_id",
            "type",
            "name",
            "coordinates",
            "calculated_value",
            "unit",
            "created_by",
            "created_at",
        )


class MeasurementWriteSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=MeasurementType.values, required=True)
    name = serializers.CharField(max_length=255, required=True)
    coordinates = MeasurementCoordinatesField(required=True)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def validate(self, attrs):
        coordinates = attrs["coordinates"]
        measurement_type = attrs["type"]

        if measurement_type == MeasurementType.DISTANCE and len(coordinates) < 2:
            raise serializers.ValidationError(
                {"coordinates": ["DISTANCE measurements require at least two positions."]}
            )

        if measurement_type == MeasurementType.AREA and len(coordinates) < 3:
            raise serializers.ValidationError(
                {"coordinates": ["AREA measurements require at least three positions."]}
            )

        return attrs

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            _reject_unknown_fields(
                serializer=self,
                data=data,
                allowed_fields={"type", "name", "coordinates"},
            )

        return super().to_internal_value(data)
