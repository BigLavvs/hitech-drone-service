from django.conf import settings
from django.db import models

from apps.projects.models import Project, Site


class SurveyStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    UPLOADING = "UPLOADING", "Uploading"
    PROCESSING = "PROCESSING", "Processing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    ARCHIVED = "ARCHIVED", "Archived"


class Survey(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="surveys",
        db_column="project_id",
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="surveys",
        db_column="site_id",
    )
    name = models.CharField(max_length=255)
    survey_date = models.DateField(db_column="survey_date")
    drone_model = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_column="drone_model",
    )
    pilot = models.CharField(max_length=255, blank=True, null=True)
    coordinate_reference_system = models.CharField(
        max_length=50,
        default="EPSG:4326",
        db_column="coordinate_reference_system",
    )
    status = models.CharField(
        max_length=50,
        choices=SurveyStatus.choices,
        default=SurveyStatus.DRAFT,
    )
    processing_status = models.CharField(
        max_length=50,
        default="pending",
        db_column="processing_status",
    )
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="surveys_created",
        db_column="created_by_id",
        blank=True,
        null=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="surveys_approved",
        db_column="approved_by_id",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "survey"
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["site"]),
            models.Index(fields=["status"]),
            models.Index(fields=["survey_date"]),
        ]

    def __str__(self) -> str:
        return self.name
