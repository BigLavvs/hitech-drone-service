from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.surveys.models import Survey


class Approval(models.Model):
    survey = models.OneToOneField(
        Survey,
        on_delete=models.CASCADE,
        related_name="approval",
        db_column="survey_id",
    )
    submitted_at = models.DateTimeField(
        blank=True,
        null=True,
        db_column="submitted_at",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approvals_submitted",
        db_column="submitted_by_id",
        blank=True,
        null=True,
    )
    approved_at = models.DateTimeField(
        blank=True,
        null=True,
        db_column="approved_at",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approvals_approved",
        db_column="approved_by_id",
        blank=True,
        null=True,
    )
    rejection_reason = models.TextField(
        blank=True,
        null=True,
        db_column="rejection_reason",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "approval"

    def __str__(self) -> str:
        return f"Approval for {self.survey_id}"


class ApprovalHistory(models.Model):
    approval = models.ForeignKey(
        Approval,
        on_delete=models.CASCADE,
        related_name="history",
        db_column="approval_id",
    )
    action = models.CharField(max_length=50)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        db_column="actor_id",
        blank=True,
        null=True,
    )
    reason = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approval_history"
        indexes = [
            models.Index(fields=["approval"]),
            models.Index(fields=["actor"]),
        ]

    def __str__(self) -> str:
        return f"{self.action}:{self.approval_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and self._state.adding is False:
            raise ValidationError("Approval history is append-only and cannot be updated.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Approval history is append-only and cannot be deleted.")
