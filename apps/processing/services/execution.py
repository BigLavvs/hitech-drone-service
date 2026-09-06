import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import DatabaseError

from apps.files.models import FileFormat
from apps.files.storage import PrivateR2StorageAdapter

from .model_processing import (
    _process_browser_ready_model_file,
    _process_mesh_file,
    _process_point_cloud_file,
)
from .artifacts import (
    _download_private_object,
    _revalidate_primary_content,
    _verify_checksum,
)
from .raster import _process_raster_file
from .lease import (
    AttemptStorage,
    ProcessingHeartbeat as _processing_heartbeat,
    ProcessingLeaseLost,
    refresh_processing_lease as _refresh_processing_lease,
)
from .retry import _mark_job_completed, _mark_job_running, _set_job_progress
from .types import (
    GeneratedArtefacts,
    ProcessingError,
)

logger = logging.getLogger(__name__)


def execute_processing_task(*, processing_job_id: int, lease_token: str | None = None):
    lease_token = lease_token or str(uuid.uuid4())
    processing_job = _mark_job_running(processing_job_id=processing_job_id, lease_token=lease_token)
    if processing_job is None:
        return {"status": "ignored"}

    try:
        with _processing_heartbeat(processing_job_id=processing_job.pk, lease_token=lease_token) as heartbeat:
            storage = AttemptStorage(
                storage=PrivateR2StorageAdapter(), heartbeat=heartbeat,
                survey_id=processing_job.file.survey_id, file_id=processing_job.file_id,
            )
            artefacts = _process_file(
                processing_job=processing_job,
                storage=storage,
                lease_token=lease_token,
            )
            heartbeat.check()
            completed = _mark_job_completed(
                processing_job_id=processing_job.pk,
                lease_token=lease_token,
                preview_path=storage.published_path(artefacts.preview_path),
                converted_path=storage.published_path(artefacts.converted_path),
            )
            return {"status": "completed" if completed else "ignored"}
    except ProcessingLeaseLost:
        # Recovery owns this job now. Never retry/fail a replacement worker's job.
        return {"status": "ignored"}
    except DatabaseError as exc:
        # A worker cannot safely decide whether it still owns the job while the
        # database is unavailable. Let lease reconciliation recover it instead
        # of sending this execution through the ordinary retry/failure path.
        raise ProcessingLeaseLost("Processing worker ownership could not be verified.") from exc
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

def _process_file(*, processing_job, storage, lease_token: str):
    survey_file = processing_job.file
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
        _set_job_progress(processing_job_id=processing_job.pk, progress_percent=40, lease_token=lease_token)

        if survey_file.format in {FileFormat.GEOTIFF, FileFormat.TIFF}:
            return _process_raster_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
                lease_token=lease_token,
            )
        if survey_file.format in {FileFormat.OBJ, FileFormat.STL, FileFormat.PLY}:
            return _process_mesh_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
                lease_token=lease_token,
            )
        if survey_file.format in {FileFormat.LAS, FileFormat.LAZ}:
            return _process_point_cloud_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
                lease_token=lease_token,
            )
        if survey_file.format in {FileFormat.GLB, FileFormat.GLTF}:
            return _process_browser_ready_model_file(
                processing_job=processing_job,
                survey_file=survey_file,
                local_raw_path=local_raw_path,
                temp_dir_path=temp_dir_path,
                storage=storage,
                lease_token=lease_token,
            )
        if survey_file.format in {FileFormat.PNG, FileFormat.JPEG, FileFormat.KML, FileFormat.GEOJSON}:
            _set_job_progress(processing_job_id=processing_job.pk, progress_percent=100, lease_token=lease_token)
            return GeneratedArtefacts()
        raise ProcessingError("Unsupported processing format.")

def _normalize_processing_exception(exc: Exception) -> ProcessingError:
    if isinstance(exc, ProcessingError):
        return exc
    if isinstance(exc, ValidationError):
        return ProcessingError("Downloaded file content failed validation.")
    if isinstance(exc, subprocess.CalledProcessError):
        return ProcessingError("Point-cloud conversion failed.")
    return ProcessingError("File processing failed.")
