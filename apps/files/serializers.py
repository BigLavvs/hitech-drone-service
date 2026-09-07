from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers


@extend_schema_serializer(component_name="SurveyFileUpload")
class SurveyFileUploadRequestSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
    assets = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        allow_empty=True,
    )


class SurveyFileUploadResponseSerializer(serializers.Serializer):
    file_id = serializers.IntegerField(source="survey_file.pk")
    job_id = serializers.IntegerField(source="processing_job.pk")


class ProcessingJobSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    progress_percent = serializers.IntegerField()
    retry_count = serializers.IntegerField()
    dispatch_status = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class SurveyFileListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    original_filename = serializers.CharField()
    file_type = serializers.CharField()
    format = serializers.CharField()
    mime_type = serializers.CharField()
    size_bytes = serializers.IntegerField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    processing_job = ProcessingJobSummarySerializer(allow_null=True)


class ProcessingJobDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="summary.id")
    status = serializers.CharField(source="summary.status")
    progress_percent = serializers.IntegerField(source="summary.progress_percent")
    retry_count = serializers.IntegerField(source="summary.retry_count")
    dispatch_status = serializers.CharField(source="summary.dispatch_status")
    started_at = serializers.DateTimeField(source="summary.started_at", allow_null=True)
    completed_at = serializers.DateTimeField(source="summary.completed_at", allow_null=True)
    created_at = serializers.DateTimeField(source="summary.created_at")
    updated_at = serializers.DateTimeField(source="summary.updated_at")
    file = serializers.SerializerMethodField()

    @extend_schema_field(SurveyFileListItemSerializer)
    def get_file(self, obj):
        file = obj.file
        return {
            "id": file.id,
            "original_filename": file.original_filename,
            "file_type": file.file_type,
            "format": file.format,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
            "status": file.status,
            "created_at": file.created_at,
            "updated_at": file.updated_at,
            "processing_job": ProcessingJobSummarySerializer(obj.summary).data,
        }
