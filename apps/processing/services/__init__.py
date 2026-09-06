from apps.files.storage import PrivateR2StorageAdapter
from apps.files.validation import validate_upload

from . import execution, model_processing, raster
from .dispatch import (
    dispatch_processing_job,
    dispatch_processing_job_safely,
    _mark_dispatch_failed,
    reconcile_stale_queued_processing_jobs,
    reconcile_expired_running_processing_jobs,
    create_queued_processing_job,
    register_processing_dispatch_on_commit,
)
from .artifacts import (
    _build_generated_key,
    _build_generated_prefix,
    _download_private_object,
    _revalidate_primary_content,
    _upload_generated_directory,
    _upload_generated_file,
    _upload_json_sidecar,
    _verify_checksum,
)
from .execution import (
    _normalize_processing_exception,
    _process_file,
    _processing_heartbeat,
    _refresh_processing_lease,
    execute_processing_task,
)
from .model_processing import (
    _build_mesh_metadata_payload,
    _build_point_cloud_metadata_payload,
    _export_reduced_glb_preview,
    _normalize_bounding_box,
    _process_browser_ready_model_file,
    _process_mesh_file,
    _process_point_cloud_file,
    _stage_gltf_external_assets,
)
from .raster import (
    _derive_max_zoom,
    _generate_xyz_tile_pyramid,
    _lonlat_to_tile,
    _mercator_tile_bounds,
    _normalize_preview_array,
    _process_raster_file,
    _tile_range_for_bounds,
)
from .retry import (
    _mark_job_completed,
    _mark_job_running,
    _set_job_progress,
    _validate_manual_retry_actor,
    _validate_manual_retry_state,
    get_survey_job_readiness,
    get_processing_job_summaries_for_file_ids,
    get_processing_job_for_file,
    get_processing_job_visible_to_user,
    get_processing_job_for_response,
    manual_retry_processing_job,
    mark_processing_failed_permanently,
    schedule_processing_retry,
)
from .types import (
    ASSESSMENT_MAX_GENERATED_TILE_ZOOM,
    MAX_AUTOMATIC_RETRIES,
    MESH_PREVIEW_TARGET_MAX_FACES,
    POTREE_METADATA_FILENAME,
    RETRY_DELAYS_MINUTES,
    TILE_SIZE_PX,
    WEB_MERCATOR_CRS,
    WEB_MERCATOR_INITIAL_RESOLUTION,
    WEB_MERCATOR_MAX_LAT,
    ChecksumMismatchError,
    DispatchResult,
    GeneratedArtefacts,
    ProcessingConfigurationError,
    ProcessingError,
    _NamedValidationFile,
)

logger = execution.logger
subprocess = model_processing.subprocess

__all__ = [
    "create_queued_processing_job",
    "register_processing_dispatch_on_commit",
    "dispatch_processing_job_safely",
    "dispatch_processing_job",
    "reconcile_stale_queued_processing_jobs",
    "reconcile_expired_running_processing_jobs",
    "execute_processing_task",
    "schedule_processing_retry",
    "mark_processing_failed_permanently",
    "manual_retry_processing_job",
    "get_processing_job_visible_to_user",
    "get_processing_job_for_response",
    "get_survey_job_readiness",
    "get_processing_job_summaries_for_file_ids",
    "get_processing_job_for_file",
    "ProcessingError",
    "ChecksumMismatchError",
    "ProcessingConfigurationError",
    "DispatchResult",
    "GeneratedArtefacts",
    "MAX_AUTOMATIC_RETRIES",
    "RETRY_DELAYS_MINUTES",
    "POTREE_METADATA_FILENAME",
    "ASSESSMENT_MAX_GENERATED_TILE_ZOOM",
]
