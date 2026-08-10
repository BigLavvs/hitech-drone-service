from django.db import models

from apps.files.models import SurveyFile


class ProcessingJob(models.Model):
    file = models.OneToOneField(
        SurveyFile,
        on_delete=models.CASCADE,
        related_name="processing_job",
        db_column="file_id",
    )
    status = models.CharField(max_length=50, default="queued")
    progress_percent = models.IntegerField(default=0, db_column="progress_percent")
    retry_count = models.IntegerField(default=0, db_column="retry_count")
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    celery_task_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "processing_job"
        indexes = [
            models.Index(fields=["file"]),
            models.Index(fields=["status"]),
            models.Index(fields=["celery_task_id"]),
        ]

    def __str__(self) -> str:
        return f"Processing job {self.pk} for file {self.file_id}"
