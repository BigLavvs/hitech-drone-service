from dataclasses import dataclass


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


@dataclass(frozen=True)
class GeneratedArtefacts:
    preview_path: str | None = None
    converted_path: str | None = None
