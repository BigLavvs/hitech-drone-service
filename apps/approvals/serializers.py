from rest_framework import serializers

from apps.projects.serializers import _reject_unknown_fields


class EmptyApprovalActionSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        _reject_unknown_fields(serializer=self, data=data, allowed_fields=set())
        return super().to_internal_value(data)


class ApprovalRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True)

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def to_internal_value(self, data):
        _reject_unknown_fields(serializer=self, data=data, allowed_fields={"reason"})
        return super().to_internal_value(data)


class ApprovalSummarySerializer(serializers.Serializer):
    survey_id = serializers.IntegerField()
    current_status = serializers.CharField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by = serializers.IntegerField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by = serializers.IntegerField(allow_null=True)
    rejection_reason = serializers.CharField(allow_null=True, allow_blank=True)
    history = serializers.ListField(child=serializers.JSONField())
