from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.files.models import SurveyFile
from apps.processing.models import ProcessingJob


class SurveyFileUploadResponseSerializer(serializers.Serializer):
    file_id = serializers.IntegerField(source="survey_file.pk")
    job_id = serializers.IntegerField(source="processing_job.pk")


class ProcessingJobSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingJob
        fields = (
            "id",
            "status",
            "progress_percent",
            "retry_count",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )


class SurveyFileListItemSerializer(serializers.ModelSerializer):
    processing_job = ProcessingJobSummarySerializer(read_only=True)

    class Meta:
        model = SurveyFile
        fields = (
            "id",
            "original_filename",
            "file_type",
            "format",
            "mime_type",
            "size_bytes",
            "status",
            "created_at",
            "updated_at",
            "processing_job",
        )


class ProcessingJobDetailSerializer(serializers.ModelSerializer):
    file = serializers.SerializerMethodField()

    class Meta:
        model = ProcessingJob
        fields = (
            "id",
            "status",
            "progress_percent",
            "retry_count",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
            "file",
        )

    @extend_schema_field(SurveyFileListItemSerializer)
    def get_file(self, obj):
        return SurveyFileListItemSerializer(obj.file).data
