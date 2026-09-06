import math
from pathlib import Path

from apps.files.object_keys import build_map_tile_key, build_map_tile_metadata_key

from .artifacts import (
    _build_generated_key,
    _upload_generated_file,
    _upload_json_sidecar,
)
from .retry import _set_job_progress
from .types import (
    ASSESSMENT_MAX_GENERATED_TILE_ZOOM,
    GeneratedArtefacts,
    TILE_SIZE_PX,
    WEB_MERCATOR_CRS,
    WEB_MERCATOR_INITIAL_RESOLUTION,
    WEB_MERCATOR_MAX_LAT,
)


def _process_raster_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage, lease_token: str):
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
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70, lease_token=lease_token)

    tile_metadata = _generate_xyz_tile_pyramid(
        survey_file=survey_file,
        local_raw_path=local_raw_path,
        temp_dir_path=temp_dir_path,
        storage=storage,
    )
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=85, lease_token=lease_token)

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
