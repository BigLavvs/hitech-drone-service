from dataclasses import dataclass
from pathlib import PurePosixPath
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.audit.tasks import dispatch_file_download_audit_event
from apps.files.models import FileFormat, SurveyFile, SurveyFileAsset
from apps.files.storage import PrivateR2StorageAdapter
from apps.files.validation import (
    FileValidationError,
    get_gltf_external_resource_references,
    sanitize_storage_filename,
    validate_gltf_asset_upload,
    validate_obj_asset_upload,
    validate_upload,
)
from apps.processing.services import (
    create_queued_processing_job,
    register_processing_dispatch_on_commit,
)
from apps.projects.models import ProjectMembership
from apps.projects.services import user_can_view_project
from apps.surveys.models import Survey, SurveyStatus

ALLOWED_UPLOAD_SURVEY_STATUSES = {
    SurveyStatus.DRAFT,
    SurveyStatus.UPLOADING,
    SurveyStatus.PROCESSING,
    SurveyStatus.FAILED,
    SurveyStatus.READY,
}

DOWNLOAD_URL_EXPIRY_SECONDS = 300
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadAdmissionResult:
    survey_file: SurveyFile
    processing_job: object
    created: bool


@dataclass(frozen=True)
class _UploadedAsset:
    validated_upload: object
    staged_upload: object


@dataclass(frozen=True)
class SurveyFileDownloadResult:
    survey_file: SurveyFile
    download_url: str


def admit_uploaded_file(
    *,
    actor: User,
    survey: Survey,
    uploaded_file,
    asset_files=None,
    declared_mime_type=None,
    storage=None,
):
    _validate_upload_actor(actor=actor, survey=survey)
    _validate_survey_accepts_uploads(survey=survey)
    asset_files = list(asset_files or [])

    validated_upload = validate_upload(uploaded_file, declared_mime_type)
    validated_assets = _validate_related_assets(
        primary_upload=validated_upload,
        primary_file=uploaded_file,
        asset_files=asset_files,
    )
    total_incoming_size_bytes = validated_upload.size_bytes + sum(
        asset.size_bytes for asset in validated_assets
    )
    _validate_survey_total_size_limit(
        survey=survey,
        incoming_size_bytes=total_incoming_size_bytes,
    )

    storage = storage or PrivateR2StorageAdapter()
    staged_upload = None
    staged_assets = []
    canonical_key = None
    canonical_asset_keys = []
    duplicate_result = None

    try:
        staged_upload = storage.upload_to_staging(
            survey_id=survey.pk,
            filename=validated_upload.sanitized_filename,
            file_obj=uploaded_file,
            content_type=validated_upload.mime_type,
        )
        for validated_asset, asset_file in zip(validated_assets, asset_files, strict=True):
            staged_assets.append(
                _UploadedAsset(
                    validated_upload=validated_asset,
                    staged_upload=storage.upload_to_staging(
                        survey_id=survey.pk,
                        filename=validated_asset.sanitized_filename,
                        file_obj=asset_file,
                        content_type=validated_asset.mime_type,
                    ),
                )
            )

        with transaction.atomic():
            locked_survey = (
                Survey.objects.select_for_update()
                .select_related("project")
                .get(pk=survey.pk)
            )
            _validate_survey_accepts_uploads(survey=locked_survey)
            _validate_survey_total_size_limit(
                survey=locked_survey,
                incoming_size_bytes=total_incoming_size_bytes,
            )

            duplicate_file = (
                SurveyFile.objects.select_related("survey", "survey__project")
                .filter(
                    survey=locked_survey,
                    sha256_checksum=staged_upload.sha256_checksum,
                )
                .first()
            )
            if duplicate_file is not None:
                duplicate_result = UploadAdmissionResult(
                    survey_file=duplicate_file,
                    processing_job=duplicate_file.processing_job,
                    created=False,
                )
            else:
                survey_file = SurveyFile.objects.create(
                    survey=locked_survey,
                    original_filename=validated_upload.original_filename,
                    stored_filename=validated_upload.sanitized_filename,
                    file_type=validated_upload.file_type,
                    format=validated_upload.file_format,
                    mime_type=validated_upload.mime_type,
                    size_bytes=validated_upload.size_bytes,
                    sha256_checksum=staged_upload.sha256_checksum,
                    storage_path="",
                    uploaded_by=actor,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                canonical_key = storage.build_canonical_key(
                    survey_id=locked_survey.pk,
                    file_id=survey_file.pk,
                    extension=PurePosixPath(validated_upload.sanitized_filename).suffix,
                )
                storage.promote_object(
                    source_key=staged_upload.storage_key,
                    destination_key=canonical_key,
                    content_type=validated_upload.mime_type,
                )
                survey_file.storage_path = canonical_key
                survey_file.save(update_fields=["storage_path", "updated_at"])

                for staged_asset in staged_assets:
                    asset_key = _build_asset_storage_key(
                        survey_id=locked_survey.pk,
                        file_id=survey_file.pk,
                        filename=staged_asset.validated_upload.sanitized_filename,
                    )
                    canonical_asset_keys.append(asset_key)
                    storage.promote_object(
                        source_key=staged_asset.staged_upload.storage_key,
                        destination_key=asset_key,
                        content_type=staged_asset.validated_upload.mime_type,
                    )
                    SurveyFileAsset.objects.create(
                        survey_file=survey_file,
                        original_filename=staged_asset.validated_upload.original_filename,
                        stored_filename=staged_asset.validated_upload.sanitized_filename,
                        mime_type=staged_asset.validated_upload.mime_type,
                        size_bytes=staged_asset.validated_upload.size_bytes,
                        sha256_checksum=staged_asset.staged_upload.sha256_checksum,
                        storage_path=asset_key,
                    )

                record_audit_event(
                    action=AuditAction.FILE_UPLOADED,
                    entity_type="survey_file",
                    entity_id=survey_file.pk,
                    user=actor,
                    project=locked_survey.project,
                    survey=locked_survey,
                    details={
                        "filename": survey_file.original_filename,
                        "size_bytes": survey_file.size_bytes,
                        "processing_job_id": processing_job.pk,
                    },
                )

                locked_survey.status = SurveyStatus.UPLOADING
                locked_survey.processing_status = "queued"
                locked_survey.save(update_fields=["status", "processing_status", "updated_at"])

                register_processing_dispatch_on_commit(processing_job_id=processing_job.pk)

                return UploadAdmissionResult(
                    survey_file=survey_file,
                    processing_job=processing_job,
                    created=True,
                )

        storage.delete_object(staged_upload.storage_key)
        for staged_asset in staged_assets:
            storage.delete_object(staged_asset.staged_upload.storage_key)
        return duplicate_result
    except Exception:
        _cleanup_storage_objects(
            storage=storage,
            staged_key=staged_upload.storage_key if staged_upload is not None else None,
            canonical_key=canonical_key,
            staged_asset_keys=[staged_asset.staged_upload.storage_key for staged_asset in staged_assets],
            canonical_asset_keys=canonical_asset_keys,
        )
        raise


def get_survey_files_visible_to_user(*, actor: User, survey_id: int):
    survey = (
        Survey.objects.select_related("project")
        .prefetch_related("files__processing_job")
        .filter(pk=survey_id)
        .first()
    )
    if survey is None:
        raise Survey.DoesNotExist
    if not user_can_view_project(actor, survey.project):
        raise PermissionDenied("You do not have permission to access this survey.")
    return survey.files.select_related("processing_job").order_by("id")


def get_survey_file_download_for_user(
    *,
    actor: User,
    survey_id: int,
    file_id: int,
    storage=None,
):
    survey = (
        Survey.objects.select_related("project")
        .filter(pk=survey_id)
        .first()
    )
    if survey is None:
        raise Survey.DoesNotExist

    if not user_can_view_project(actor, survey.project):
        raise PermissionDenied("You do not have permission to access this survey.")

    survey_file = (
        SurveyFile.objects.select_related("survey", "survey__project")
        .filter(pk=file_id, survey=survey)
        .first()
    )
    if survey_file is None:
        raise SurveyFile.DoesNotExist

    if survey.status != SurveyStatus.APPROVED:
        raise PermissionDenied("Downloads are allowed only for approved surveys.")

    storage = storage or PrivateR2StorageAdapter()
    download_url = storage.generate_private_download_url(
        storage_key=survey_file.storage_path,
        expires_in=DOWNLOAD_URL_EXPIRY_SECONDS,
    )
    dispatch_file_download_audit_event(
        user_id=actor.pk,
        project_id=survey.project_id,
        survey_id=survey.pk,
        survey_file_id=survey_file.pk,
    )
    return SurveyFileDownloadResult(survey_file=survey_file, download_url=download_url)


def _validate_upload_actor(*, actor: User, survey: Survey):
    if not actor.is_active:
        raise PermissionDenied(
            "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files."
        )

    if actor.role == UserRole.ADMINISTRATOR:
        return

    if actor.role == UserRole.PROJECT_MANAGER and survey.project.project_manager_id == actor.pk:
        return

    if actor.role == UserRole.SURVEY_ENGINEER and ProjectMembership.objects.filter(
        project=survey.project,
        user=actor,
    ).exists():
        return

    raise PermissionDenied(
        "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files."
    )


def _validate_survey_accepts_uploads(*, survey: Survey):
    if survey.project.status != "active":
        raise ValidationError("Uploads are not allowed for archived projects.")

    if survey.status not in ALLOWED_UPLOAD_SURVEY_STATUSES:
        raise ValidationError("Uploads are not allowed for surveys in the current state.")


def _validate_survey_total_size_limit(*, survey: Survey, incoming_size_bytes: int):
    current_file_total_size = (
        SurveyFile.objects.filter(survey=survey).aggregate(
            total_size=Coalesce(Sum("size_bytes"), 0)
        )["total_size"]
        or 0
    )
    current_asset_total_size = (
        SurveyFileAsset.objects.filter(survey_file__survey=survey).aggregate(
            total_size=Coalesce(Sum("size_bytes"), 0)
        )["total_size"]
        or 0
    )
    current_total_size = current_file_total_size + current_asset_total_size
    if current_total_size + incoming_size_bytes > settings.MAX_SURVEY_TOTAL_SIZE_BYTES:
        raise ValidationError("Survey exceeds the configured total upload size limit.")


def _validate_related_assets(*, primary_upload, primary_file, asset_files):
    if primary_upload.file_format == FileFormat.OBJ:
        validator = validate_obj_asset_upload
        asset_label = "OBJ"
        required_filenames = None
    elif primary_upload.file_format == FileFormat.GLTF:
        validator = validate_gltf_asset_upload
        asset_label = "GLTF"
        try:
            required_filenames = {
                sanitize_storage_filename(reference.name)
                for reference in get_gltf_external_resource_references(primary_file)
            }
        except FileValidationError as exc:
            raise ValidationError(str(exc)) from exc
    else:
        if asset_files:
            raise ValidationError("Related assets are allowed only for OBJ or GLTF primary uploads.")
        return []

    try:
        validated_assets = [validator(asset_file) for asset_file in asset_files]
    except FileValidationError as exc:
        raise ValidationError(str(exc)) from exc
    seen_filenames = set()
    seen_checksums = set()
    for validated_asset, asset_file in zip(validated_assets, asset_files, strict=True):
        if validated_asset.sanitized_filename in seen_filenames:
            raise ValidationError(f"Duplicate {asset_label} asset filename is not allowed.")
        seen_filenames.add(validated_asset.sanitized_filename)

        asset_file.seek(0)
        checksum = storage_sha256(asset_file)
        asset_file.seek(0)
        if checksum in seen_checksums:
            raise ValidationError(f"Duplicate {asset_label} asset checksum is not allowed.")
        seen_checksums.add(checksum)

    if required_filenames is not None and seen_filenames != required_filenames:
        raise ValidationError(
            "GLTF related assets must exactly match the external buffers and images referenced by the GLTF manifest."
        )

    return validated_assets


def _build_asset_storage_key(*, survey_id: int, file_id: int, filename: str):
    return f"surveys/{survey_id}/files/{file_id}/assets/{filename}"


def _cleanup_storage_objects(
    *,
    storage,
    staged_key,
    canonical_key,
    staged_asset_keys=None,
    canonical_asset_keys=None,
):
    for asset_key in canonical_asset_keys or []:
        storage.delete_object(asset_key)
    if canonical_key:
        storage.delete_object(canonical_key)
    for staged_asset_key in staged_asset_keys or []:
        storage.delete_object(staged_asset_key)
    if staged_key:
        storage.delete_object(staged_key)


def storage_sha256(file_obj):
    import hashlib

    hasher = hashlib.sha256()
    current_position = file_obj.tell()
    file_obj.seek(0)
    for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
        hasher.update(chunk)
    file_obj.seek(current_position)
    return hasher.hexdigest()
