import json
import math
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.files.object_keys import build_map_tile_key, build_map_tile_metadata_key
from apps.files.services import DOWNLOAD_URL_EXPIRY_SECONDS
from apps.files.storage import PrivateR2StorageAdapter
from apps.maps.models import Measurement, MeasurementType
from apps.projects.services import user_can_view_project
from apps.surveys.models import Survey


MAP_SOURCE_FORMATS = {
    FileFormat.PNG,
    FileFormat.JPEG,
    FileFormat.KML,
    FileFormat.GEOJSON,
}
RASTER_TILE_FORMATS = {FileFormat.GEOTIFF, FileFormat.TIFF}
EARTH_RADIUS_METRES = 6371008.8
DECIMAL_QUANTIZE_EIGHT_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class MapTileRedirectResult:
    redirect_url: str


def get_measurements_visible_to_user(*, actor: User, survey_id: int):
    survey = _get_visible_survey(actor=actor, survey_id=survey_id)
    return survey.measurements.select_related("created_by").order_by("-created_at", "-id")


def get_measurement_visible_to_user(*, actor: User, survey_id: int, measurement_id: int) -> Measurement:
    survey = _get_visible_survey(actor=actor, survey_id=survey_id)
    measurement = (
        Measurement.objects.select_related("survey", "survey__project", "created_by")
        .filter(pk=measurement_id, survey=survey)
        .first()
    )
    if measurement is None:
        raise Measurement.DoesNotExist
    return measurement


def create_measurement(
    *,
    actor: User,
    survey_id: int,
    measurement_type: str,
    name: str,
    coordinates: list[list[float]],
) -> Measurement:
    survey = _get_visible_survey(actor=actor, survey_id=survey_id)

    if measurement_type == MeasurementType.DISTANCE:
        calculated_value = _calculate_distance_metres(coordinates)
        unit = "m"
    elif measurement_type == MeasurementType.AREA:
        calculated_value = _calculate_area_square_metres(coordinates)
        unit = "m²"
    else:
        raise ValidationError("Invalid measurement type.")

    with transaction.atomic():
        measurement = Measurement.objects.create(
            survey=survey,
            type=measurement_type,
            name=name,
            coordinates=coordinates,
            calculated_value=calculated_value,
            unit=unit,
            created_by=actor,
        )
        record_audit_event(
            action=AuditAction.MEASUREMENT_CREATED,
            entity_type="measurement",
            entity_id=measurement.pk,
            user=actor,
            project=survey.project,
            survey=survey,
        )

    return measurement


def delete_measurement(*, actor: User, survey_id: int, measurement_id: int) -> None:
    survey = _get_survey_for_measurement_deletion(actor=actor, survey_id=survey_id)
    measurement = (
        Measurement.objects.select_related("survey", "survey__project")
        .filter(pk=measurement_id, survey=survey)
        .first()
    )
    if measurement is None:
        raise Measurement.DoesNotExist

    with transaction.atomic():
        entity_id = measurement.pk
        measurement.delete()
        record_audit_event(
            action=AuditAction.MEASUREMENT_DELETED,
            entity_type="measurement",
            entity_id=entity_id,
            user=actor,
            project=survey.project,
            survey=survey,
        )


def get_survey_map_layers_for_user(*, actor, survey_id: int, storage=None):
    survey = _get_visible_survey(actor=actor, survey_id=survey_id)

    storage = storage or PrivateR2StorageAdapter()
    descriptors = []
    for survey_file in (
        SurveyFile.objects.filter(
            survey=survey,
            file_type=FileType.TWO_D,
            status="ready",
        )
        .order_by("id")
    ):
        if survey_file.format in RASTER_TILE_FORMATS:
            metadata = _load_private_json(
                storage=storage,
                storage_key=build_map_tile_metadata_key(
                    survey_id=survey_file.survey_id,
                    file_id=survey_file.pk,
                ),
            )
            descriptors.append(
                {
                    "id": survey_file.pk,
                    "original_filename": survey_file.original_filename,
                    "format": survey_file.format,
                    "bounds": metadata["bounds"],
                    "zoom_range": metadata["zoom_range"],
                    "tile_url_template": f"/api/v1/map-layers/{survey_file.pk}/tiles/{{z}}/{{x}}/{{y}}",
                }
            )
            continue

        if survey_file.format in MAP_SOURCE_FORMATS:
            descriptor = {
                "id": survey_file.pk,
                "original_filename": survey_file.original_filename,
                "format": survey_file.format,
                "source_url": storage.generate_private_download_url(
                    storage_key=survey_file.storage_path,
                    expires_in=DOWNLOAD_URL_EXPIRY_SECONDS,
                ),
            }
            descriptors.append(descriptor)

    return descriptors


def get_map_tile_redirect_for_user(*, actor, file_id: int, z: int, x: int, y: int, storage=None):
    survey_file = (
        SurveyFile.objects.select_related("survey", "survey__project")
        .filter(
            pk=file_id,
            file_type=FileType.TWO_D,
            status="ready",
            format__in=RASTER_TILE_FORMATS,
        )
        .first()
    )
    if survey_file is None:
        raise SurveyFile.DoesNotExist
    if not user_can_view_project(actor, survey_file.survey.project):
        raise PermissionDenied("You do not have permission to access this survey.")

    storage = storage or PrivateR2StorageAdapter()
    metadata = _load_private_json(
        storage=storage,
        storage_key=build_map_tile_metadata_key(
            survey_id=survey_file.survey_id,
            file_id=survey_file.pk,
        ),
    )
    _validate_tile_request(metadata=metadata, z=z, x=x, y=y)

    redirect_url = storage.generate_private_download_url(
        storage_key=build_map_tile_key(
            survey_id=survey_file.survey_id,
            file_id=survey_file.pk,
            z=z,
            x=x,
            y=y,
        ),
        expires_in=DOWNLOAD_URL_EXPIRY_SECONDS,
    )
    return MapTileRedirectResult(redirect_url=redirect_url)


def _load_private_json(*, storage, storage_key: str):
    buffer = BytesIO()
    storage.download_to_fileobj(storage_key=storage_key, file_obj=buffer)
    buffer.seek(0)
    return json.loads(buffer.read().decode("utf-8"))


def _validate_tile_request(*, metadata: dict, z: int, x: int, y: int):
    zoom_range = metadata.get("zoom_range") or {}
    if z < int(zoom_range.get("min", 0)) or z > int(zoom_range.get("max", -1)):
        raise SurveyFile.DoesNotExist

    tile_matrix_bounds = metadata.get("tile_matrix_bounds") or {}
    zoom_bounds = tile_matrix_bounds.get(str(z))
    if zoom_bounds is None:
        raise SurveyFile.DoesNotExist
    if (
        x < int(zoom_bounds["x_min"])
        or x > int(zoom_bounds["x_max"])
        or y < int(zoom_bounds["y_min"])
        or y > int(zoom_bounds["y_max"])
    ):
        raise SurveyFile.DoesNotExist


def _get_visible_survey(*, actor: User, survey_id: int) -> Survey:
    survey = Survey.objects.select_related("project").filter(pk=survey_id).first()
    if survey is None:
        raise Survey.DoesNotExist
    if not user_can_view_project(actor, survey.project):
        raise PermissionDenied("You do not have permission to access this survey.")
    return survey


def _get_survey_for_measurement_deletion(*, actor: User, survey_id: int) -> Survey:
    survey = Survey.objects.select_related("project").filter(pk=survey_id).first()
    if survey is None:
        raise Survey.DoesNotExist

    if not actor.is_active:
        raise PermissionDenied(
            "Only active administrators and the owning project manager can delete measurements."
        )

    if actor.role == UserRole.ADMINISTRATOR:
        return survey

    if actor.role == UserRole.PROJECT_MANAGER and survey.project.project_manager_id == actor.pk:
        return survey

    raise PermissionDenied(
        "Only active administrators and the owning project manager can delete measurements."
    )


def _calculate_distance_metres(coordinates: list[list[float]]) -> Decimal:
    distance = 0.0
    for start, end in zip(coordinates, coordinates[1:]):
        distance += _great_circle_distance(start=start, end=end)
    return _to_decimal(distance)


def _calculate_area_square_metres(coordinates: list[list[float]]) -> Decimal:
    if coordinates[0] != coordinates[-1]:
        closed_coordinates = [*coordinates, coordinates[0]]
    else:
        closed_coordinates = coordinates

    total = 0.0
    for start, end in zip(closed_coordinates, closed_coordinates[1:]):
        lon1, lat1 = map(math.radians, start)
        lon2, lat2 = map(math.radians, end)
        total += (lon2 - lon1) * (2.0 + math.sin(lat1) + math.sin(lat2))

    area = abs(total) * (EARTH_RADIUS_METRES ** 2) / 2.0
    max_area = 4.0 * math.pi * (EARTH_RADIUS_METRES ** 2)
    if area > max_area / 2.0:
        area = max_area - area

    return _to_decimal(area)


def _great_circle_distance(*, start: list[float], end: list[float]) -> float:
    start_lon, start_lat = map(math.radians, start)
    end_lon, end_lat = map(math.radians, end)

    delta_lon = end_lon - start_lon
    delta_lat = end_lat - start_lat

    sin_delta_lat = math.sin(delta_lat / 2.0)
    sin_delta_lon = math.sin(delta_lon / 2.0)
    a = (
        sin_delta_lat * sin_delta_lat
        + math.cos(start_lat) * math.cos(end_lat) * sin_delta_lon * sin_delta_lon
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METRES * c


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(DECIMAL_QUANTIZE_EIGHT_PLACES, rounding=ROUND_HALF_UP)
