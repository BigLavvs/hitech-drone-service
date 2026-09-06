from dataclasses import dataclass
from pathlib import PurePosixPath
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

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
from apps.surveys.models import Survey, SurveyStatus
from apps.surveys.services import get_survey_visible_to_user

ALLOWED_UPLOAD_SURVEY_STATUSES = {
    SurveyStatus.DRAFT,
    SurveyStatus.UPLOADING,
    SurveyStatus.PROCESSING,
    SurveyStatus.FAILED,
    SurveyStatus.READY,
}

DOWNLOAD_URL_EXPIRY_SECONDS = 300
_UNSET = object()
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


@dataclass(frozen=True)
class SurveyFileReadiness:
    file_count: int
    all_files_ready: bool


@dataclass(frozen=True)
class ProcessingFileAssetSnapshot:
    original_filename: str
    stored_filename: str
    sha256_checksum: str
    storage_path: str
    mime_type: str


@dataclass(frozen=True)
class ProcessingFileSnapshot:
    id: int
    survey_id: int
    original_filename: str
    stored_filename: str
    file_type: str
    format: str
    mime_type: str
    sha256_checksum: str
    size_bytes: int
    status: str
    created_at: object
    updated_at: object
    storage_path: str
    preview_path: str | None
    converted_path: str | None
    assets: tuple[ProcessingFileAssetSnapshot, ...]

    @property
    def pk(self):
        return self.id


@dataclass(frozen=True)
class ReadySurveyFileSnapshot:
    id: int
    survey_id: int
    original_filename: str
    format: str
    storage_path: str
    converted_path: str | None
    preview_path: str | None

    @property
    def pk(self):
        return self.id


@dataclass(frozen=True)
class SurveyFileListSnapshot:
    id: int
    original_filename: str
    file_type: str
    format: str
    mime_type: str
    size_bytes: int
    status: str
    created_at: object
    updated_at: object
    processing_job: object | None


def admit_uploaded_file(
    *,
    actor: User,
    survey: Survey,
    uploaded_file,
    asset_files=None,
    declared_mime_type=None,
    storage=None,
):
    from apps.surveys.services import get_survey_workflow_snapshot
    from apps.processing.services import create_queued_processing_job, register_processing_dispatch_on_commit

    workflow = get_survey_workflow_snapshot(survey_id=survey.pk)
    _validate_upload_actor(actor=actor, survey=workflow)
    _validate_survey_accepts_uploads(survey=workflow)
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
        survey_id=survey.pk,
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
            from apps.surveys.services import lock_survey_for_workflow

            locked_survey = lock_survey_for_workflow(survey_id=survey.pk)
            _validate_survey_accepts_uploads(survey=locked_survey)
            _validate_survey_total_size_limit(
                survey_id=locked_survey.id,
                incoming_size_bytes=total_incoming_size_bytes,
            )

            duplicate_file = (
                SurveyFile.objects
                .filter(
                    survey_id=locked_survey.id,
                    sha256_checksum=staged_upload.sha256_checksum,
                )
                .first()
            )
            if duplicate_file is not None:
                from apps.processing.services import get_processing_job_for_file

                duplicate_result = UploadAdmissionResult(
                    survey_file=duplicate_file,
                    processing_job=get_processing_job_for_file(file_id=duplicate_file.pk),
                    created=False,
                )
            else:
                survey_file = SurveyFile.objects.create(
                    survey_id=locked_survey.id,
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
                    project_id=locked_survey.project_id,
                    survey_id=locked_survey.id,
                    details={
                        "filename": survey_file.original_filename,
                        "size_bytes": survey_file.size_bytes,
                        "processing_job_id": processing_job.pk,
                    },
                )

                from apps.surveys.services import update_survey_processing_state

                update_survey_processing_state(
                    survey_id=locked_survey.id,
                    status=SurveyStatus.UPLOADING,
                    processing_status="queued",
                )

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
    from apps.projects.services import user_can_view_project_id
    from apps.processing.services import get_processing_job_summaries_for_file_ids
    from apps.surveys.services import get_survey_workflow_snapshot

    workflow = get_survey_workflow_snapshot(survey_id=survey_id)
    if not user_can_view_project_id(user=actor, project_id=workflow.project_id):
        raise PermissionDenied("You do not have permission to access this survey.")
    rows = list(
        SurveyFile.objects.filter(survey_id=survey_id).order_by("id").values(
            "id", "original_filename", "file_type", "format", "mime_type", "size_bytes",
            "status", "created_at", "updated_at",
        )
    )
    summaries = get_processing_job_summaries_for_file_ids(file_ids=[row["id"] for row in rows])
    return tuple(
        SurveyFileListSnapshot(**row, processing_job=summaries.get(row["id"]))
        for row in rows
    )


def get_survey_file_download_for_user(
    *,
    actor: User,
    survey_id: int,
    file_id: int,
    storage=None,
):
    from apps.projects.services import user_can_view_project_id
    from apps.surveys.services import get_survey_workflow_snapshot

    survey = get_survey_workflow_snapshot(survey_id=survey_id)
    if not user_can_view_project_id(user=actor, project_id=survey.project_id):
        raise PermissionDenied("You do not have permission to access this survey.")

    survey_file = (
        SurveyFile.objects
        .filter(pk=file_id, survey_id=survey_id)
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
        survey_id=survey_id,
        survey_file_id=survey_file.pk,
    )
    return SurveyFileDownloadResult(survey_file=survey_file, download_url=download_url)


def _validate_upload_actor(*, actor: User, survey):
    if not actor.is_active:
        raise PermissionDenied(
            "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files."
        )

    if actor.role == UserRole.ADMINISTRATOR:
        return

    if actor.role == UserRole.PROJECT_MANAGER and survey.project_manager_id == actor.pk:
        return

    from apps.projects.services import user_can_view_project_id

    if actor.role == UserRole.SURVEY_ENGINEER and user_can_view_project_id(
        user=actor,
        project_id=survey.project_id,
    ):
        return

    raise PermissionDenied(
        "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files."
    )


def _validate_survey_accepts_uploads(*, survey):
    if survey.project_status != "active":
        raise ValidationError("Uploads are not allowed for archived projects.")

    if survey.status not in ALLOWED_UPLOAD_SURVEY_STATUSES:
        raise ValidationError("Uploads are not allowed for surveys in the current state.")


def _validate_survey_total_size_limit(*, survey_id: int | None = None, survey: Survey | None = None, incoming_size_bytes: int):
    survey_id = survey_id or survey.pk
    current_file_total_size = (
        SurveyFile.objects.filter(survey_id=survey_id).aggregate(
            total_size=Coalesce(Sum("size_bytes"), 0)
        )["total_size"]
        or 0
    )
    current_asset_total_size = (
        SurveyFileAsset.objects.filter(survey_file__survey_id=survey_id).aggregate(
            total_size=Coalesce(Sum("size_bytes"), 0)
        )["total_size"]
        or 0
    )
    current_total_size = current_file_total_size + current_asset_total_size
    if current_total_size + incoming_size_bytes > settings.MAX_SURVEY_TOTAL_SIZE_BYTES:
        raise ValidationError("Survey exceeds the configured total upload size limit.")


def get_ready_survey_files(*, survey_id: int, file_type: str):
    return tuple(
        ReadySurveyFileSnapshot(
            id=row["id"],
            survey_id=row["survey_id"],
            original_filename=row["original_filename"],
            format=row["format"],
            storage_path=row["storage_path"],
            converted_path=row["converted_path"], preview_path=row["preview_path"],
        )
        for row in SurveyFile.objects.filter(
            survey_id=survey_id,
            file_type=file_type,
            status="ready",
        ).order_by("id").values(
            "id", "survey_id", "original_filename", "format", "storage_path", "converted_path", "preview_path"
        )
    )


def get_ready_survey_file(*, file_id: int, file_type: str, file_formats: set[str]):
    survey_file = (
        SurveyFile.objects
        .filter(
            pk=file_id,
            file_type=file_type,
            status="ready",
            format__in=file_formats,
        )
        .first()
    )
    if survey_file is None:
        raise SurveyFile.DoesNotExist
    return ReadySurveyFileSnapshot(
        id=survey_file.pk,
        survey_id=survey_file.survey_id,
        original_filename=survey_file.original_filename,
        format=survey_file.format,
        storage_path=survey_file.storage_path,
        converted_path=survey_file.converted_path, preview_path=survey_file.preview_path,
    )


def get_survey_file_for_processing(*, file_id: int):
    return SurveyFile.objects.prefetch_related("assets").get(pk=file_id)


def get_survey_file_readiness(*, survey_id: int) -> SurveyFileReadiness:
    statuses = list(SurveyFile.objects.filter(survey_id=survey_id).values_list("status", flat=True))
    return SurveyFileReadiness(
        file_count=len(statuses),
        all_files_ready=bool(statuses) and all(status == "ready" for status in statuses),
    )


def get_survey_file_ids(*, survey_id: int) -> list[int]:
    return list(
        SurveyFile.objects.filter(survey_id=survey_id).values_list("id", flat=True)
    )


def get_processing_file_snapshot(*, file_id: int) -> ProcessingFileSnapshot:
    survey_file = SurveyFile.objects.prefetch_related("assets").get(pk=file_id)
    return ProcessingFileSnapshot(
        id=survey_file.pk,
        survey_id=survey_file.survey_id,
        original_filename=survey_file.original_filename,
        stored_filename=survey_file.stored_filename,
        file_type=survey_file.file_type,
        format=survey_file.format,
        mime_type=survey_file.mime_type,
        sha256_checksum=survey_file.sha256_checksum,
        size_bytes=survey_file.size_bytes,
        status=survey_file.status,
        created_at=survey_file.created_at,
        updated_at=survey_file.updated_at,
        storage_path=survey_file.storage_path,
        preview_path=survey_file.preview_path,
        converted_path=survey_file.converted_path,
        assets=tuple(
            ProcessingFileAssetSnapshot(
                original_filename=asset.original_filename,
                stored_filename=asset.stored_filename,
                sha256_checksum=asset.sha256_checksum,
                storage_path=asset.storage_path,
                mime_type=asset.mime_type,
            )
            for asset in survey_file.assets.all()
        ),
    )


def update_file_processing_state(
    *, file_id: int, status: str, preview_path=_UNSET, converted_path=_UNSET
):
    update_fields = {"status": status, "updated_at": timezone.now()}
    if preview_path is not _UNSET:
        update_fields["preview_path"] = preview_path
    if converted_path is not _UNSET:
        update_fields["converted_path"] = converted_path
    SurveyFile.objects.filter(pk=file_id).update(**update_fields)


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
