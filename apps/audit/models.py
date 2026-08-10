from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.projects.models import Project
from apps.surveys.models import Survey


class AuditAction(models.TextChoices):
    PROJECT_CREATED = "PROJECT_CREATED", "Project Created"
    PROJECT_UPDATED = "PROJECT_UPDATED", "Project Updated"
    PROJECT_ARCHIVED = "PROJECT_ARCHIVED", "Project Archived"
    SITE_CREATED = "SITE_CREATED", "Site Created"
    SITE_UPDATED = "SITE_UPDATED", "Site Updated"
    SITE_DELETED = "SITE_DELETED", "Site Deleted"
    SURVEY_CREATED = "SURVEY_CREATED", "Survey Created"
    SURVEY_UPDATED = "SURVEY_UPDATED", "Survey Updated"
    SURVEY_SUBMITTED = "SURVEY_SUBMITTED", "Survey Submitted"
    FILE_UPLOADED = "FILE_UPLOADED", "File Uploaded"
    FILE_UPLOAD_FAILED = "FILE_UPLOAD_FAILED", "File Upload Failed"
    PROCESSING_STARTED = "PROCESSING_STARTED", "Processing Started"
    PROCESSING_FAILED = "PROCESSING_FAILED", "Processing Failed"
    PROCESSING_RETRY = "PROCESSING_RETRY", "Processing Retry"
    PROCESSING_COMPLETED = "PROCESSING_COMPLETED", "Processing Completed"
    APPROVAL_SUBMITTED = "APPROVAL_SUBMITTED", "Approval Submitted"
    APPROVAL_APPROVED = "APPROVAL_APPROVED", "Approval Approved"
    APPROVAL_REJECTED = "APPROVAL_REJECTED", "Approval Rejected"
    SURVEY_ARCHIVED = "SURVEY_ARCHIVED", "Survey Archived"
    FILE_DOWNLOADED = "FILE_DOWNLOADED", "File Downloaded"
    MEASUREMENT_CREATED = "MEASUREMENT_CREATED", "Measurement Created"
    MEASUREMENT_DELETED = "MEASUREMENT_DELETED", "Measurement Deleted"
    ADMIN_ACTION = "ADMIN_ACTION", "Admin Action"


class AuditLog(models.Model):
    action = models.CharField(max_length=50, choices=AuditAction.choices)
    entity_type = models.CharField(max_length=50, db_column="entity_type")
    entity_id = models.IntegerField(db_column="entity_id")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        db_column="user_id",
        blank=True,
        null=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        db_column="project_id",
        blank=True,
        null=True,
    )
    survey = models.ForeignKey(
        Survey,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        db_column="survey_id",
        blank=True,
        null=True,
    )
    details = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        db_column="ip_address",
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["user"]),
            models.Index(fields=["project"]),
            models.Index(fields=["survey"]),
            models.Index(fields=["timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} on {self.entity_type}:{self.entity_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and self._state.adding is False:
            raise ValidationError("Audit records are append-only and cannot be updated.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records are append-only and cannot be deleted.")
