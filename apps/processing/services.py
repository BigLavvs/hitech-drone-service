import hashlib
import json
import logging
import math
import mimetypes
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.access_control.models import UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.files.object_keys import (
    build_generated_object_key,
    build_generated_object_prefix,
    build_map_tile_key,
    build_map_tile_metadata_key,
    build_model_metadata_key,
)
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.files.storage import PrivateR2StorageAdapter
from apps.files.validation import (
    get_gltf_external_resource_references,
    sanitize_storage_filename,
    validate_upload,
)
from apps.processing.models import ProcessingJob
from apps.projects.models import ProjectMembership
from apps.projects.services import user_can_view_project
from apps.surveys.models import Survey, SurveyStatus

logger = logging.getLogger(__name__)

MAX_AUTOMATIC_RETRIES = 3
RETRY_DELAYS_MINUTES = (2, 5, 10)
POTREE_METADATA_FILENAME = "metadata.json"
MESH_PREVIEW_TARGET_MAX_FACES = 5000
TILE_SIZE_PX = 256
WEB_MERCATOR_CRS = "EPSG:3857"
WEB_MERCATOR_MAX_LAT = 85.0511287798066
WEB_MERCATOR_INITIAL_RESOLUTION = 156543.03392804097
ASSESSMENT_MAX_GENERATED_TILE_ZOOM = 12


class ProcessingError(Exception):
    pass


class ChecksumMismatchError(ProcessingError):
    pass


class ProcessingConfigurationError(ProcessingError):
    pass


class _NamedValidationFile:
    def __init__(self, file_handle, filename: str):
        self._file_handle = file_handle
        self.name = filename

    def __getattr__(self, attribute):
        return getattr(self._file_handle, attribute)


@dataclass(frozen=True)
class DispatchResult:
    dispatched: bool
    celery_task_id: str | None


def create_queued_processing_job(*, survey_file):
    return ProcessingJob.objects.create(file=survey_file, status="queued")


def register_processing_dispatch_on_commit(*, processing_job_id: int):
    transaction.on_commit(lambda: dispatch_processing_job_safely(processing_job_id=processing_job_id))


def dispatch_processing_job_safely(*, processing_job_id: int) -> DispatchResult:
    try:
        return dispatch_processing_job(processing_job_id=processing_job_id)
    except Exception:
        logger.warning(
            "Processing dispatch unavailable; upload remains queued.",
            extra={"processing_job_id": processing_job_id},
        )
        return DispatchResult(dispatched=False, celery_task_id=None)


def dispatch_processing_job(*, processing_job_id: int) -> DispatchResult:
    processing_job = (
        ProcessingJob.objects.select_related("file")
        .only("id", "status", "celery_task_id", "file__file_type")
        .get(pk=processing_job_id)
    )
    if processing_job.file.file_type == FileType.TWO_D:
        from apps.processing.tasks import process_2d_file

        async_result = process_2d_file.apply_async(args=[processing_job.pk])
    else:
        from apps.processing.tasks import process_3d_file

        async_result = process_3d_file.apply_async(args=[processing_job.pk])

    ProcessingJob.objects.filter(pk=processing_job.pk).update(
        celery_task_id=async_result.id,
        updated_at=timezone.now(),
    )
    return DispatchResult(dispatched=True, celery_task_id=async_result.id)


def execute_processing_task(*, processing_job_id: int):
    processing_job = _mark_job_running(processing_job_id=processing_job_id)
    if processing_job is None:
        return {"status": "ignored"}

    storage = PrivateR2StorageAdapter()
    try:
        artefacts = _process_file(processing_job=processing_job, storage=storage)
    except Exception as exc:
        logger.exception(
            "Processing job failed before exception normalisation.",
            extra={
                "processing_job_id": processing_job.pk,
                "survey_file_id": processing_job.file_id,
                "file_format": processing_job.file.format,
                "file_type": processing_job.file.file_type,
            },
        )
        raise _normalize_processing_exception(exc) from exc

    _mark_job_completed(
        processing_job_id=processing_job.pk,
        preview_path=artefacts.preview_path,
        converted_path=artefacts.converted_path,
    )
    return {"status": "completed"}


def schedule_processing_retry(*, processing_job_id: int, error_message: str):
    with transaction.atomic():
        processing_job = (
            ProcessingJob.objects.select_for_update()
            .select_related("file", "file__survey", "file__survey__project")
            .get(pk=processing_job_id)
        )
        if processing_job.status == "completed":
            return None
        if processing_job.retry_count >= MAX_AUTOMATIC_RETRIES:
            return None

        processing_job.retry_count += 1
        processing_job.status = "queued"
        processing_job.error_message = error_message
        processing_job.completed_at = None
        processing_job.save(
            update_fields=["retry_count", "status", "error_message", "completed_at", "updated_at"]
        )
        record_audit_event(
            action=AuditAction.PROCESSING_RETRY,
            entity_type="processing_job",
            entity_id=processing_job.pk,
            project=processing_job.file.survey.project,
            survey=processing_job.file.survey,
            details={
                "retry_count": processing_job.retry_count,
                "automatic": True,
                "delay_minutes": RETRY_DELAYS_MINUTES[processing_job.retry_count - 1],
            },
        )
        return RETRY_DELAYS_MINUTES[processing_job.retry_count - 1] * 60


def mark_processing_failed_permanently(*, processing_job_id: int, error_message: str):
    with transaction.atomic():
        processing_job = (
            ProcessingJob.objects.select_for_update()
            .select_related("file", "file__survey", "file__survey__project")
            .get(pk=processing_job_id)
        )
        processing_job.status = "failed"
        processing_job.progress_percent = min(processing_job.progress_percent, 99)
        processing_job.completed_at = timezone.now()
        processing_job.error_message = error_message
        processing_job.save(
            update_fields=[
                "status",
                "progress_percent",
                "completed_at",
                "error_message",
                "updated_at",
            ]
        )

        survey_file = processing_job.file
        survey_file.status = "failed"
        survey_file.save(update_fields=["status", "updated_at"])

        survey = survey_file.survey
        survey.status = SurveyStatus.FAILED
        survey.processing_status = "failed"
        survey.save(update_fields=["status", "processing_status", "updated_at"])

        record_audit_event(
            action=AuditAction.PROCESSING_FAILED,
            entity_type="processing_job",
            entity_id=processing_job.pk,
            project=survey.project,
            survey=survey,
            details={"retry_count": processing_job.retry_count, "error": error_message},
        )


def manual_retry_processing_job(*, actor, processing_job_id: int):
    with transaction.atomic():
        processing_job = (
            ProcessingJob.objects.select_for_update()
            .select_related("file", "file__survey", "file__survey__project")
            .get(pk=processing_job_id)
        )
        _validate_manual_retry_actor(actor=actor, processing_job=processing_job)
        _validate_manual_retry_state(processing_job=processing_job)

        processing_job.retry_count += 1
        processing_job.status = "queued"
        processing_job.progress_percent = 0
        processing_job.started_at = None
        processing_job.completed_at = None
        processing_job.error_message = None
        processing_job.celery_task_id = None
        processing_job.save(
            update_fields=[
                "retry_count",
                "status",
                "progress_percent",
                "started_at",
                "completed_at",
                "error_message",
                "celery_task_id",
                "updated_at",
            ]
        )
        record_audit_event(
            action=AuditAction.PROCESSING_RETRY,
            entity_type="processing_job",
            entity_id=processing_job.pk,
            user=actor,
            project=processing_job.file.survey.project,
            survey=processing_job.file.survey,
            details={"retry_count": processing_job.retry_count, "automatic": False},
        )
        register_processing_dispatch_on_commit(processing_job_id=processing_job.pk)
        return processing_job


def get_processing_job_visible_to_user(*, actor, processing_job_id: int):
    processing_job = (
        ProcessingJob.objects.select_related("file", "file__survey", "file__survey__project")
        .filter(pk=processing_job_id)
        .first()
    )
    if processing_job is None:
        raise ProcessingJob.DoesNotExist
    if not user_can_view_project(actor, processing_job.file.survey.project):
        raise PermissionDenied("You do not have permission to access this processing job.")
    return processing_job


def _validate_manual_retry_actor(*, actor, processing_job):
    if not actor.is_active:
        raise PermissionDenied("Only eligible active users can retry failed processing jobs.")
    if actor.role == UserRole.ADMINISTRATOR:
        return
    if actor.role == UserRole.SURVEY_ENGINEER and ProjectMembership.objects.filter(
        project=processing_job.file.survey.project,
        user=actor,
    ).exists():
        return
    raise PermissionDenied("Only eligible active users can retry failed processing jobs.")


def _validate_manual_retry_state(*, processing_job):
    if processing_job.status != "failed":
        raise ValidationError("Only permanently failed processing jobs can be retried manually.")
    if processing_job.retry_count >= MAX_AUTOMATIC_RETRIES:
        raise ValidationError("The processing job has reached the retry limit.")


def _mark_job_running(*, processing_job_id: int):
    with transaction.atomic():
        processing_job = (
            ProcessingJob.objects.select_for_update()
            .select_related("file", "file__survey", "file__survey__project")
            .get(pk=processing_job_id)
        )
        if processing_job.status in {"running", "completed"}:
            return None
        if processing_job.status != "queued":
            return None

        now = timezone.now()
        processing_job.status = "running"
        processing_job.progress_percent = 10
        processing_job.started_at = processing_job.started_at or now
        processing_job.completed_at = None
        processing_job.error_message = None
        processing_job.save(
            update_fields=[
                "status",
                "progress_percent",
                "started_at",
                "completed_at",
                "error_message",
                "updated_at",
            ]
        )

        survey_file = processing_job.file
        survey_file.status = "processing"
        survey_file.save(update_fields=["status", "updated_at"])

        survey = survey_file.survey
        survey.status = SurveyStatus.PROCESSING
        survey.processing_status = "processing"
        survey.save(update_fields=["status", "processing_status", "updated_at"])

        record_audit_event(
            action=AuditAction.PROCESSING_STARTED,
            entity_type="processing_job",
            entity_id=processing_job.pk,
            project=survey.project,
            survey=survey,
            details={"retry_count": processing_job.retry_count},
        )
        return processing_job


@dataclass(frozen=True)
class GeneratedArtefacts:
    preview_path: str | None = None
    converted_path: str | None = None


def _process_file(*, processing_job, storage):
    survey_file = SurveyFile.objects.prefetch_related("assets").get(pk=processing_job.file_id)
    with tempfile.TemporaryDirectory(prefix="processing-job-") as temp_dir:
        temp_dir_path = Path(temp_dir)
        local_raw_path = temp_dir_path / survey_file.stored_filename
        _download_private_object(
            storage=storage,
            storage_key=survey_file.storage_path,
            destination_path=local_raw_path,
        )
        _verify_checksum(path=local_raw_path, expected_sha256=survey_file.sha256_checksum)
        _revalidate_primary_content(survey_file=survey_file, local_raw_path=local_raw_path)
        _set_job_progress(processing_job_id=processing_job.pk, progress_percent=40)

        if survey_file.format in {FileFormat.GEOTIFF, FileFormat.TIFF}:
            return _process_raster_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
            )
        if survey_file.format in {FileFormat.OBJ, FileFormat.STL, FileFormat.PLY}:
            return _process_mesh_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
            )
        if survey_file.format in {FileFormat.LAS, FileFormat.LAZ}:
            return _process_point_cloud_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
            )
        if survey_file.format in {FileFormat.GLB, FileFormat.GLTF}:
            return _process_browser_ready_model_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
            )
        if survey_file.format in {FileFormat.PNG, FileFormat.JPEG, FileFormat.KML, FileFormat.GEOJSON}:
            _set_job_progress(processing_job_id=processing_job.pk, progress_percent=100)
            return GeneratedArtefacts()
        raise ProcessingError("Unsupported processing format.")


def _process_raster_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage):
    import numpy
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.shutil import copy as rasterio_copy

    preview_local_path = temp_dir_path / "preview.png"
    converted_local_path = temp_dir_path / "cog.tif"

    rasterio_copy(str(local_raw_path), str(converted_local_path), driver="COG")
    with rasterio.open(local_raw_path) as dataset:
        preview_count = 1 if dataset.count == 1 else min(dataset.count, 3)
        band_indexes = tuple(range(1, preview_count + 1))
        target_height = max(1, min(dataset.height, 256))
        target_width = max(1, min(dataset.width, 256))
        preview_data = dataset.read(
            indexes=band_indexes,
            out_shape=(preview_count, target_height, target_width),
            resampling=Resampling.nearest,
            masked=True,
        )
        preview_uint8 = _normalize_preview_array(preview_data, numpy=numpy)
        with rasterio.open(
            preview_local_path,
            "w",
            driver="PNG",
            width=target_width,
            height=target_height,
            count=preview_count,
            dtype="uint8",
        ) as preview_dataset:
            preview_dataset.write(preview_uint8)
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70)

    tile_metadata = _generate_xyz_tile_pyramid(
        survey_file=survey_file,
        local_raw_path=local_raw_path,
        temp_dir_path=temp_dir_path,
        storage=storage,
    )
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=85)

    preview_key = _build_generated_key(survey_file=survey_file, filename="preview.png")
    converted_key = _build_generated_key(survey_file=survey_file, filename="cog.tif")
    _upload_generated_file(storage=storage, source_path=preview_local_path, destination_key=preview_key, content_type="image/png")
    _upload_generated_file(storage=storage, source_path=converted_local_path, destination_key=converted_key, content_type=survey_file.mime_type)
    _upload_json_sidecar(
        storage=storage,
        destination_key=build_map_tile_metadata_key(
            survey_id=survey_file.survey_id,
            file_id=survey_file.pk,
        ),
        payload=tile_metadata,
    )
    return GeneratedArtefacts(preview_path=preview_key, converted_path=converted_key)


def _process_mesh_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage):
    import trimesh

    if survey_file.format == FileFormat.OBJ:
        for asset in survey_file.assets.all():
            asset_path = temp_dir_path / asset.stored_filename
            _download_private_object(
                storage=storage,
                storage_key=asset.storage_path,
                destination_path=asset_path,
            )
            _verify_checksum(path=asset_path, expected_sha256=asset.sha256_checksum)

    converted_local_path = temp_dir_path / "model.glb"
    mesh = trimesh.load(str(local_raw_path), force="mesh")
    mesh.export(str(converted_local_path), file_type="glb")
    preview_local_path = temp_dir_path / "preview.glb"
    _export_reduced_glb_preview(mesh=mesh, destination_path=preview_local_path)
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70)

    preview_key = _build_generated_key(survey_file=survey_file, filename="preview.glb")
    converted_key = _build_generated_key(survey_file=survey_file, filename="model.glb")
    _upload_json_sidecar(
        storage=storage,
        destination_key=build_model_metadata_key(
            survey_id=survey_file.survey_id,
            file_id=survey_file.pk,
        ),
        payload=_build_mesh_metadata_payload(mesh=mesh, display_format="GLB"),
    )
    _upload_generated_file(
        storage=storage,
        source_path=preview_local_path,
        destination_key=preview_key,
        content_type="model/gltf-binary",
    )
    _upload_generated_file(
        storage=storage,
        source_path=converted_local_path,
        destination_key=converted_key,
        content_type="model/gltf-binary",
    )
    return GeneratedArtefacts(preview_path=preview_key, converted_path=converted_key)


def _process_browser_ready_model_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage):
    import trimesh

    uses_external_gltf_assets = survey_file.format == FileFormat.GLTF and survey_file.assets.exists()
    if survey_file.format == FileFormat.GLTF:
        _stage_gltf_external_assets(
            survey_file=survey_file,
            local_raw_path=local_raw_path,
            temp_dir_path=temp_dir_path,
            storage=storage,
        )

    preview_local_path = temp_dir_path / "preview.glb"
    mesh = trimesh.load(str(local_raw_path), force="mesh")
    _export_reduced_glb_preview(mesh=mesh, destination_path=preview_local_path)
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70)

    preview_key = _build_generated_key(survey_file=survey_file, filename="preview.glb")
    converted_key = None
    if uses_external_gltf_assets:
        converted_local_path = temp_dir_path / "model.glb"
        mesh.export(str(converted_local_path), file_type="glb")
        converted_key = _build_generated_key(survey_file=survey_file, filename="model.glb")

    _upload_json_sidecar(
        storage=storage,
        destination_key=build_model_metadata_key(
            survey_id=survey_file.survey_id,
            file_id=survey_file.pk,
        ),
        payload=_build_mesh_metadata_payload(
            mesh=mesh,
            display_format=FileFormat.GLB if converted_key else survey_file.format,
        ),
    )
    _upload_generated_file(
        storage=storage,
        source_path=preview_local_path,
        destination_key=preview_key,
        content_type="model/gltf-binary",
    )
    if converted_key:
        _upload_generated_file(
            storage=storage,
            source_path=converted_local_path,
            destination_key=converted_key,
            content_type="model/gltf-binary",
        )
    return GeneratedArtefacts(preview_path=preview_key, converted_path=converted_key)


def _stage_gltf_external_assets(*, survey_file, local_raw_path, temp_dir_path, storage):
    with local_raw_path.open("rb") as raw_file:
        references = get_gltf_external_resource_references(
            _NamedValidationFile(raw_file, survey_file.stored_filename)
        )

    assets_by_filename = {
        asset.stored_filename: asset
        for asset in survey_file.assets.all()
    }
    expected_filenames = {sanitize_storage_filename(reference.name) for reference in references}
    if set(assets_by_filename) != expected_filenames:
        raise ProcessingError("GLTF related assets no longer match the GLTF manifest.")

    for reference in references:
        asset = assets_by_filename[sanitize_storage_filename(reference.name)]
        destination_path = temp_dir_path.joinpath(*PurePosixPath(reference).parts)
        _download_private_object(
            storage=storage,
            storage_key=asset.storage_path,
            destination_path=destination_path,
        )
        _verify_checksum(path=destination_path, expected_sha256=asset.sha256_checksum)


def _process_point_cloud_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage):
    converter_path = settings.POTREE_CONVERTER_PATH.strip()
    if not converter_path or not Path(converter_path).exists():
        raise ProcessingConfigurationError("Point-cloud conversion is not configured on the worker.")

    output_dir = temp_dir_path / "potree"
    subprocess.run(
        [converter_path, str(local_raw_path), "-o", str(output_dir)],
        check=True,
        capture_output=True,
    )
    if not output_dir.exists():
        raise ProcessingError("Point-cloud conversion did not produce output.")
    metadata_path = output_dir / POTREE_METADATA_FILENAME
    if not metadata_path.exists():
        raise ProcessingError("Point-cloud conversion did not produce metadata output.")

    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70)
    output_prefix = _build_generated_prefix(survey_file=survey_file, prefix="potree")
    uploaded_keys = _upload_generated_directory(
        storage=storage,
        source_dir=output_dir,
        destination_prefix=output_prefix,
    )
    converted_key = uploaded_keys[POTREE_METADATA_FILENAME]
    _upload_json_sidecar(
        storage=storage,
        destination_key=build_model_metadata_key(
            survey_id=survey_file.survey_id,
            file_id=survey_file.pk,
        ),
        payload=_build_point_cloud_metadata_payload(metadata_path=metadata_path),
    )
    return GeneratedArtefacts(preview_path=converted_key, converted_path=converted_key)


def _download_private_object(*, storage, storage_key, destination_path):
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with destination_path.open("wb") as destination_file:
        storage.download_to_fileobj(storage_key=storage_key, file_obj=destination_file)


def _verify_checksum(*, path: Path, expected_sha256: str):
    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != expected_sha256:
        raise ChecksumMismatchError("Stored file integrity verification failed.")


def _revalidate_primary_content(*, survey_file, local_raw_path: Path):
    with local_raw_path.open("rb") as downloaded_file:
        validate_upload(
            _NamedValidationFile(downloaded_file, survey_file.original_filename),
            survey_file.mime_type,
        )


def _export_reduced_glb_preview(*, mesh, destination_path: Path):
    import fast_simplification
    import trimesh

    face_count = len(mesh.faces)
    if face_count == 0:
        raise ProcessingError("Mesh does not contain any faces.")

    target_face_count = max(1, min(MESH_PREVIEW_TARGET_MAX_FACES, face_count // 4))
    if target_face_count < face_count:
        simplified_vertices, simplified_faces = fast_simplification.simplify(
            mesh.vertices,
            mesh.faces,
            target_count=target_face_count,
        )
        preview_mesh = trimesh.Trimesh(
            vertices=simplified_vertices,
            faces=simplified_faces,
            process=False,
        )
    else:
        preview_mesh = mesh.copy()

    preview_mesh.export(str(destination_path), file_type="glb")


def _upload_generated_file(*, storage, source_path: Path, destination_key: str, content_type: str):
    with source_path.open("rb") as generated_file:
        storage.upload_generated_fileobj(
            destination_key=destination_key,
            file_obj=generated_file,
            content_type=content_type,
        )


def _upload_generated_directory(*, storage, source_dir: Path, destination_prefix: str):
    uploaded_keys = {}
    for source_path in sorted(path for path in source_dir.rglob("*") if path.is_file()):
        relative_path = source_path.relative_to(source_dir).as_posix()
        destination_key = f"{destination_prefix}/{relative_path}"
        content_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        _upload_generated_file(
            storage=storage,
            source_path=source_path,
            destination_key=destination_key,
            content_type=content_type,
        )
        uploaded_keys[relative_path] = destination_key
    return uploaded_keys


def _build_generated_key(*, survey_file, filename: str):
    return build_generated_object_key(
        survey_id=survey_file.survey_id,
        file_id=survey_file.pk,
        filename=filename,
    )


def _build_generated_prefix(*, survey_file, prefix: str):
    return build_generated_object_prefix(
        survey_id=survey_file.survey_id,
        file_id=survey_file.pk,
        prefix=prefix,
    )


def _upload_json_sidecar(*, storage, destination_key: str, payload: dict):
    file_obj = BytesIO(json.dumps(payload, sort_keys=True).encode("utf-8"))
    storage.upload_generated_fileobj(
        destination_key=destination_key,
        file_obj=file_obj,
        content_type="application/json",
    )


def _generate_xyz_tile_pyramid(*, survey_file, local_raw_path: Path, temp_dir_path: Path, storage):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import transform_bounds

    tiles_dir = temp_dir_path / "tiles"
    tile_ranges = {}
    generated_tiles = 0

    with rasterio.open(local_raw_path) as dataset:
        geographic_bounds = transform_bounds(
            dataset.crs,
            "EPSG:4326",
            *dataset.bounds,
            densify_pts=21,
        )
        mercator_bounds = transform_bounds(
            dataset.crs,
            WEB_MERCATOR_CRS,
            *dataset.bounds,
            densify_pts=21,
        )
        max_zoom = _derive_max_zoom(mercator_bounds=mercator_bounds, width=dataset.width, height=dataset.height)
        band_count = 1 if dataset.count == 1 else min(dataset.count, 3)
        band_indexes = tuple(range(1, band_count + 1))

        with WarpedVRT(dataset, crs=WEB_MERCATOR_CRS, resampling=Resampling.bilinear) as vrt:
            for z in range(0, max_zoom + 1):
                x_min, x_max, y_min, y_max = _tile_range_for_bounds(geographic_bounds=geographic_bounds, z=z)
                tile_ranges[str(z)] = {
                    "x_min": x_min,
                    "x_max": x_max,
                    "y_min": y_min,
                    "y_max": y_max,
                }
                for x in range(x_min, x_max + 1):
                    for y in range(y_min, y_max + 1):
                        left, bottom, right, top = _mercator_tile_bounds(z=z, x=x, y=y)
                        tile_transform = from_bounds(
                            left,
                            bottom,
                            right,
                            top,
                            TILE_SIZE_PX,
                            TILE_SIZE_PX,
                        )
                        with WarpedVRT(
                            dataset,
                            crs=WEB_MERCATOR_CRS,
                            transform=tile_transform,
                            width=TILE_SIZE_PX,
                            height=TILE_SIZE_PX,
                            resampling=Resampling.bilinear,
                        ) as tile_vrt:
                            tile_data = tile_vrt.read(
                                indexes=band_indexes,
                                out_shape=(band_count, TILE_SIZE_PX, TILE_SIZE_PX),
                                masked=True,
                                fill_value=0,
                            )
                        if getattr(tile_data, "mask", None) is not None and bool(tile_data.mask.all()):
                            continue

                        tile_path = tiles_dir / str(z) / str(x) / f"{y}.png"
                        tile_path.parent.mkdir(parents=True, exist_ok=True)
                        with rasterio.open(
                            tile_path,
                            "w",
                            driver="PNG",
                            width=TILE_SIZE_PX,
                            height=TILE_SIZE_PX,
                            count=band_count,
                            dtype="uint8",
                        ) as tile_dataset:
                            tile_dataset.write(_normalize_preview_array(tile_data, numpy=__import__("numpy")))

                        _upload_generated_file(
                            storage=storage,
                            source_path=tile_path,
                            destination_key=build_map_tile_key(
                                survey_id=survey_file.survey_id,
                                file_id=survey_file.pk,
                                z=z,
                                x=x,
                                y=y,
                            ),
                            content_type="image/png",
                        )
                        generated_tiles += 1

    west, south, east, north = geographic_bounds
    return {
        "bounds": [west, south, east, north],
        "zoom_range": {"min": 0, "max": max_zoom},
        "tile_matrix_bounds": tile_ranges,
        "generated_tile_count": generated_tiles,
    }


def _derive_max_zoom(*, mercator_bounds, width: int, height: int) -> int:
    left, bottom, right, top = mercator_bounds
    width_m = max(abs(right - left), 1.0)
    height_m = max(abs(top - bottom), 1.0)
    pixel_size_m = max(width_m / max(width, 1), height_m / max(height, 1))
    zoom = math.ceil(math.log2(WEB_MERCATOR_INITIAL_RESOLUTION / pixel_size_m))
    return max(0, min(zoom, ASSESSMENT_MAX_GENERATED_TILE_ZOOM))


def _tile_range_for_bounds(*, geographic_bounds, z: int):
    west, south, east, north = geographic_bounds
    west = max(-180.0, min(180.0, west))
    east = max(-180.0, min(180.0, east))
    south = max(-WEB_MERCATOR_MAX_LAT, min(WEB_MERCATOR_MAX_LAT, south))
    north = max(-WEB_MERCATOR_MAX_LAT, min(WEB_MERCATOR_MAX_LAT, north))
    x_min, y_max = _lonlat_to_tile(lon=west, lat=north, z=z)
    x_max, y_min = _lonlat_to_tile(lon=east, lat=south, z=z)
    return min(x_min, x_max), max(x_min, x_max), min(y_min, y_max), max(y_min, y_max)


def _lonlat_to_tile(*, lon: float, lat: float, z: int):
    lat_rad = math.radians(lat)
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _mercator_tile_bounds(*, z: int, x: int, y: int):
    origin_shift = WEB_MERCATOR_INITIAL_RESOLUTION * TILE_SIZE_PX / 2.0
    tile_span = (origin_shift * 2.0) / (2**z)
    left = -origin_shift + x * tile_span
    right = left + tile_span
    top = origin_shift - y * tile_span
    bottom = top - tile_span
    return left, bottom, right, top


def _build_mesh_metadata_payload(*, mesh, display_format: str):
    bounds = getattr(mesh, "bounds", None)
    metadata = getattr(mesh, "metadata", {}) or {}
    crs = metadata.get("crs") or metadata.get("CRS")
    return {
        "display_format": display_format,
        "vertex_count": int(len(getattr(mesh, "vertices", ()))),
        "bounding_box": _normalize_bounding_box(bounds),
        "crs": crs,
    }


def _build_point_cloud_metadata_payload(*, metadata_path: Path):
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    bounding_box = metadata.get("boundingBox") or metadata.get("bounding_box") or {}
    vertex_count = metadata.get("points") or metadata.get("numPoints") or 0
    crs = metadata.get("crs") or metadata.get("projection")
    return {
        "display_format": "POTREE",
        "vertex_count": int(vertex_count or 0),
        "bounding_box": _normalize_bounding_box(bounding_box),
        "crs": crs,
    }


def _normalize_bounding_box(bounds):
    if bounds is None:
        return None
    if hasattr(bounds, "tolist"):
        bounds = bounds.tolist()
    if isinstance(bounds, dict):
        if {"lx", "ly", "lz", "ux", "uy", "uz"}.issubset(bounds):
            return {
                "min": [bounds["lx"], bounds["ly"], bounds["lz"]],
                "max": [bounds["ux"], bounds["uy"], bounds["uz"]],
            }
        if "min" in bounds and "max" in bounds:
            return {"min": list(bounds["min"]), "max": list(bounds["max"])}
        return None
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        return {"min": list(bounds[0]), "max": list(bounds[1])}
    return None


def _normalize_preview_array(preview_data, *, numpy):
    preview_array = preview_data.filled(0).astype("float32", copy=False)
    normalized_bands = []
    for band in preview_array:
        finite_mask = numpy.isfinite(band)
        if not finite_mask.any():
            normalized_bands.append(numpy.zeros_like(band, dtype="uint8"))
            continue
        finite_values = band[finite_mask]
        min_value = float(finite_values.min())
        max_value = float(finite_values.max())
        if max_value <= min_value:
            normalized_bands.append(numpy.zeros_like(band, dtype="uint8"))
            continue
        scaled = ((band - min_value) / (max_value - min_value)) * 255.0
        scaled = numpy.clip(scaled, 0, 255)
        normalized_bands.append(scaled.astype("uint8"))
    return numpy.stack(normalized_bands, axis=0)


def _set_job_progress(*, processing_job_id: int, progress_percent: int):
    ProcessingJob.objects.filter(pk=processing_job_id, status="running").update(
        progress_percent=progress_percent,
        updated_at=timezone.now(),
    )


def _mark_job_completed(*, processing_job_id: int, preview_path: str | None, converted_path: str | None):
    with transaction.atomic():
        processing_job = (
            ProcessingJob.objects.select_for_update()
            .select_related("file", "file__survey", "file__survey__project")
            .get(pk=processing_job_id)
        )
        survey_file = processing_job.file
        survey_file.status = "ready"
        survey_file.preview_path = preview_path
        survey_file.converted_path = converted_path
        survey_file.save(update_fields=["status", "preview_path", "converted_path", "updated_at"])

        processing_job.status = "completed"
        processing_job.progress_percent = 100
        processing_job.completed_at = timezone.now()
        processing_job.error_message = None
        processing_job.save(
            update_fields=["status", "progress_percent", "completed_at", "error_message", "updated_at"]
        )

        survey = survey_file.survey
        remaining_files = survey.files.exclude(status="ready").exists()
        if remaining_files:
            survey.status = SurveyStatus.PROCESSING
            survey.processing_status = "processing"
        else:
            survey.status = SurveyStatus.READY
            survey.processing_status = "completed"
        survey.save(update_fields=["status", "processing_status", "updated_at"])

        record_audit_event(
            action=AuditAction.PROCESSING_COMPLETED,
            entity_type="processing_job",
            entity_id=processing_job.pk,
            project=survey.project,
            survey=survey,
            details={"retry_count": processing_job.retry_count},
        )


def _normalize_processing_exception(exc: Exception) -> ProcessingError:
    if isinstance(exc, ProcessingError):
        return exc
    if isinstance(exc, ValidationError):
        return ProcessingError("Downloaded file content failed validation.")
    if isinstance(exc, subprocess.CalledProcessError):
        return ProcessingError("Point-cloud conversion failed.")
    return ProcessingError("File processing failed.")
