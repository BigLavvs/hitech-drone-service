from django.conf import settings
from django.db import models

from apps.surveys.models import Survey


class MeasurementType(models.TextChoices):
    DISTANCE = "DISTANCE", "Distance"
    AREA = "AREA", "Area"


class Measurement(models.Model):
    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="measurements",
        db_column="survey_id",
    )
    type = models.CharField(max_length=50, choices=MeasurementType.choices)
    name = models.CharField(max_length=255)
    coordinates = models.JSONField()
    calculated_value = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        db_column="calculated_value",
    )
    unit = models.CharField(max_length=50)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="measurements_created",
        db_column="created_by_id",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "measurement"
        indexes = [
            models.Index(fields=["survey"]),
            models.Index(fields=["type"]),
        ]

    def __str__(self) -> str:
        return self.name
