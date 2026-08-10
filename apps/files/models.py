from django.conf import settings
from django.db import models

from apps.surveys.models import Survey


class FileType(models.TextChoices):
    TWO_D = "TWO_D", "2D"
    THREE_D = "THREE_D", "3D"


class FileFormat(models.TextChoices):
    GEOTIFF = "GEOTIFF", "GeoTIFF"
    TIFF = "TIFF", "TIFF"
    PNG = "PNG", "PNG"
    JPEG = "JPEG", "JPEG"
    KML = "KML", "KML"
    GEOJSON = "GEOJSON", "GeoJSON"
    OBJ = "OBJ", "OBJ"
    GLB = "GLB", "GLB"
    GLTF = "GLTF", "GLTF"
    LAS = "LAS", "LAS"
    LAZ = "LAZ", "LAZ"
    PLY = "PLY", "PLY"
    STL = "STL", "STL"


class SurveyFile(models.Model):
    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="files",
        db_column="survey_id",
    )
    original_filename = models.CharField(max_length=255)
    stored_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=50, choices=FileType.choices)
    format = models.CharField(max_length=50, choices=FileFormat.choices)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.BigIntegerField()
    sha256_checksum = models.CharField(max_length=64)
    storage_path = models.CharField(max_length=500)
    preview_path = models.CharField(max_length=500, blank=True, null=True)
    converted_path = models.CharField(max_length=500, blank=True, null=True)
    status = models.CharField(max_length=50, default="uploading")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="files_uploaded",
        db_column="uploaded_by_id",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "survey_file"
        indexes = [
            models.Index(fields=["survey"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["sha256_checksum", "survey"],
                name="survey_file_unique_checksum_per_survey",
            ),
        ]

    def __str__(self) -> str:
        return self.original_filename


class SurveyFileAsset(models.Model):
    survey_file = models.ForeignKey(
        SurveyFile,
        on_delete=models.CASCADE,
        related_name="assets",
        db_column="survey_file_id",
    )
    original_filename = models.CharField(max_length=255)
    stored_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.BigIntegerField()
    sha256_checksum = models.CharField(max_length=64)
    storage_path = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "survey_file_asset"
        indexes = [
            models.Index(fields=["survey_file"]),
            models.Index(fields=["sha256_checksum"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["survey_file", "stored_filename"],
                name="survey_file_asset_unique_name_per_file",
            ),
            models.UniqueConstraint(
                fields=["survey_file", "sha256_checksum"],
                name="survey_file_asset_unique_checksum_per_file",
            ),
            models.UniqueConstraint(
                fields=["survey_file", "storage_path"],
                name="survey_file_asset_unique_path_per_file",
            ),
        ]

    def __str__(self) -> str:
        return self.original_filename


class UploadSession(models.Model):
    id = models.CharField(max_length=255, primary_key=True, db_column="session_id")
    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
        db_column="survey_id",
    )
    file = models.OneToOneField(
        SurveyFile,
        on_delete=models.SET_NULL,
        related_name="upload_session",
        db_column="file_id",
        blank=True,
        null=True,
    )
    file_type = models.CharField(max_length=50, choices=FileType.choices)
    total_size_bytes = models.BigIntegerField()
    uploaded_bytes = models.BigIntegerField(default=0)
    progress_percent = models.IntegerField(default=0)
    status = models.CharField(max_length=50, default="in_progress")
    checksum_expected = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "upload_session"
        indexes = [
            models.Index(fields=["survey"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.id
