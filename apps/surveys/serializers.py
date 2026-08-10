from collections.abc import Mapping

from django.core.exceptions import ObjectDoesNotExist
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.projects.models import Project, Site
from apps.projects.serializers import _reject_unknown_fields
from apps.surveys.models import Survey, SurveyStatus


class SurveyReadSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField()
    site_id = serializers.IntegerField()
    created_by = serializers.IntegerField(source="created_by_id", allow_null=True)
    approved_by = serializers.IntegerField(source="approved_by_id", allow_null=True)
    rejection_reason = serializers.SerializerMethodField()

    class Meta:
        model = Survey
        fields = (
            "id",
            "project_id",
            "site_id",
            "name",
            "survey_date",
            "drone_model",
            "pilot",
            "coordinate_reference_system",
            "status",
            "processing_status",
            "notes",
            "created_by",
            "approved_by",
            "rejection_reason",
            "created_at",
            "updated_at",
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_rejection_reason(self, obj: Survey):
        try:
            approval = obj.approval
        except ObjectDoesNotExist:
            return None
        return approval.rejection_reason


class SurveyCreateSerializer(serializers.Serializer):
    project_id = serializers.PrimaryKeyRelatedField(source="project", queryset=Project.objects.all())
    site_id = serializers.PrimaryKeyRelatedField(source="site", queryset=Site.objects.all())
    name = serializers.CharField(max_length=255, required=True)
    survey_date = serializers.DateField(required=True)
    drone_model = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )
    pilot = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )
    coordinate_reference_system = serializers.CharField(max_length=50, required=False)
    notes = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def to_internal_value(self, data):
        _reject_unknown_fields(
            serializer=self,
            data=data,
            allowed_fields={
                "project_id",
                "site_id",
                "name",
                "survey_date",
                "drone_model",
                "pilot",
                "coordinate_reference_system",
                "notes",
            },
        )
        return super().to_internal_value(data)


class SurveyUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    survey_date = serializers.DateField(required=False)
    drone_model = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )
    pilot = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )
    coordinate_reference_system = serializers.CharField(max_length=50, required=False)
    notes = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def to_internal_value(self, data):
        _reject_unknown_fields(
            serializer=self,
            data=data,
            allowed_fields={
                "name",
                "survey_date",
                "drone_model",
                "pilot",
                "coordinate_reference_system",
                "notes",
            },
        )
        return super().to_internal_value(data)


class SurveyListQuerySerializer(serializers.Serializer):
    project_id = serializers.IntegerField(min_value=1, required=False)
    site_id = serializers.IntegerField(min_value=1, required=False)
    status = serializers.ChoiceField(choices=SurveyStatus.values, required=False)
    from_date = serializers.DateField(required=False)
    sort = serializers.ChoiceField(choices=["survey_date"], required=False)
    order = serializers.ChoiceField(choices=["asc", "desc"], required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)
    offset = serializers.IntegerField(min_value=0, required=False)

    def validate(self, attrs):
        sort = attrs.get("sort")
        order = attrs.get("order")
        if (sort is None) != (order is None):
            raise serializers.ValidationError(
                {"order" if sort else "sort": ["sort and order must be provided together."]}
            )
        return attrs

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            _reject_unknown_fields(
                serializer=self,
                data=data,
                allowed_fields={
                    "project_id",
                    "site_id",
                    "status",
                    "from_date",
                    "sort",
                    "order",
                    "limit",
                    "offset",
                },
            )
        return super().to_internal_value(data)
