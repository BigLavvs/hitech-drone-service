import json
from io import BytesIO

from django.core.exceptions import PermissionDenied

from apps.files.models import FileFormat, FileType, SurveyFile
from apps.files.object_keys import build_model_metadata_key
from apps.files.services import DOWNLOAD_URL_EXPIRY_SECONDS
from apps.files.storage import PrivateR2StorageAdapter
from apps.projects.services import user_can_view_project
from apps.surveys.models import Survey


def get_survey_models_for_user(*, actor, survey_id: int, storage=None):
    survey = Survey.objects.select_related("project").filter(pk=survey_id).first()
    if survey is None:
        raise Survey.DoesNotExist
    if not user_can_view_project(actor, survey.project):
        raise PermissionDenied("You do not have permission to access this survey.")

    storage = storage or PrivateR2StorageAdapter()
    descriptors = []
    for survey_file in (
        SurveyFile.objects.filter(
            survey=survey,
            file_type=FileType.THREE_D,
            status="ready",
        )
        .order_by("id")
    ):
        source = _resolve_model_source(survey_file=survey_file)
        if source is None:
            continue

        metadata = _load_private_json(
            storage=storage,
            storage_key=build_model_metadata_key(
                survey_id=survey_file.survey_id,
                file_id=survey_file.pk,
            ),
        )
        descriptors.append(
            {
                "id": survey_file.pk,
                "original_filename": survey_file.original_filename,
                "format": survey_file.format,
                "viewer_source_type": source["viewer_source_type"],
                "source_url": storage.generate_private_download_url(
                    storage_key=source["storage_key"],
                    expires_in=DOWNLOAD_URL_EXPIRY_SECONDS,
                ),
                "display_format": metadata.get("display_format"),
                "vertex_count": metadata.get("vertex_count"),
                "bounding_box": metadata.get("bounding_box"),
                "crs": metadata.get("crs"),
            }
        )
    return descriptors


def _resolve_model_source(*, survey_file):
    if survey_file.format in {FileFormat.OBJ, FileFormat.PLY, FileFormat.STL} and survey_file.converted_path:
        return {"viewer_source_type": "glb", "storage_key": survey_file.converted_path}
    if survey_file.format == FileFormat.GLB:
        return {"viewer_source_type": "glb", "storage_key": survey_file.storage_path}
    if survey_file.format == FileFormat.GLTF:
        return {"viewer_source_type": "gltf", "storage_key": survey_file.storage_path}
    if survey_file.format in {FileFormat.LAS, FileFormat.LAZ} and survey_file.converted_path:
        return {"viewer_source_type": "potree", "storage_key": survey_file.converted_path}
    return None


def _load_private_json(*, storage, storage_key: str):
    buffer = BytesIO()
    storage.download_to_fileobj(storage_key=storage_key, file_obj=buffer)
    buffer.seek(0)
    return json.loads(buffer.read().decode("utf-8"))
