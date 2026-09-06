import json
import subprocess
from pathlib import Path, PurePosixPath

from django.conf import settings

from apps.files.object_keys import build_model_metadata_key
from apps.files.models import FileFormat
from apps.files.validation import get_gltf_external_resource_references, sanitize_storage_filename

from .artifacts import (
    _build_generated_key,
    _build_generated_prefix,
    _download_private_object,
    _upload_generated_directory,
    _upload_generated_file,
    _upload_json_sidecar,
    _verify_checksum,
)
from .retry import _set_job_progress
from .types import (
    MESH_PREVIEW_TARGET_MAX_FACES,
    POTREE_METADATA_FILENAME,
    GeneratedArtefacts,
    ProcessingConfigurationError,
    ProcessingError,
    _NamedValidationFile,
)


def _process_mesh_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage, lease_token: str):
    import trimesh

    if survey_file.format == FileFormat.OBJ:
        for asset in survey_file.assets:
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
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70, lease_token=lease_token)

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

def _process_browser_ready_model_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage, lease_token: str):
    import trimesh

    uses_external_gltf_assets = survey_file.format == FileFormat.GLTF and bool(survey_file.assets)
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
    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70, lease_token=lease_token)

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
        for asset in survey_file.assets
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

def _process_point_cloud_file(*, processing_job, survey_file, local_raw_path, temp_dir_path, storage, lease_token: str):
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

    _set_job_progress(processing_job_id=processing_job.pk, progress_percent=70, lease_token=lease_token)
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
