from pathlib import PurePosixPath

from django.conf import settings

from .content import (
    _detect_format,
    _ensure,
    _validate_jpeg,
    _validate_gltf_resource_uri,
    _validate_mtl,
    _validate_png,
    get_gltf_external_resource_uris,
)
from .rules import MAX_VALIDATION_BYTES, _FORMAT_RULES
from .streams import (
    _get_primary_extension,
    _get_upload_size,
    _read_prefix,
    _validate_filename,
    sanitize_storage_filename,
)
from .types import FileValidationError, ValidatedAssetUpload, ValidatedUpload

def validate_upload(uploaded_file, declared_mime_type=None):
    original_filename = getattr(uploaded_file, "name", "")
    if not original_filename:
        raise FileValidationError("Filename is required.")

    normalized_name = _validate_filename(original_filename)
    extension = _get_primary_extension(normalized_name)
    format_rule = _FORMAT_RULES.get(extension)
    if format_rule is None:
        raise FileValidationError("Unsupported file extension.")

    mime_type = (declared_mime_type or getattr(uploaded_file, "content_type", "") or "").strip().lower()
    if not mime_type:
        raise FileValidationError("MIME type is required.")

    size_bytes = _get_upload_size(uploaded_file)
    if size_bytes > settings.MAX_FILE_SIZE_BYTES:
        raise FileValidationError("File exceeds the configured size limit.")

    header = _read_prefix(uploaded_file, MAX_VALIDATION_BYTES)
    detected_format = _detect_format(
        extension,
        header,
        uploaded_file=uploaded_file,
        size_bytes=size_bytes,
    )
    if detected_format not in format_rule["formats"]:
        raise FileValidationError("Filename extension does not match file content.")
    if (
        mime_type not in format_rule["mime_types"]
        and mime_type not in format_rule["fallback_mime_types"]
    ):
        raise FileValidationError("Unsupported or mismatched MIME type.")

    sanitized_filename = sanitize_storage_filename(PurePosixPath(normalized_name).name)
    return ValidatedUpload(
        original_filename=PurePosixPath(normalized_name).name,
        sanitized_filename=sanitized_filename,
        mime_type=mime_type,
        file_type=format_rule["file_type"],
        file_format=detected_format,
        size_bytes=size_bytes,
    )

def validate_obj_asset_upload(uploaded_file, declared_mime_type=None):
    original_filename = getattr(uploaded_file, "name", "")
    if not original_filename:
        raise FileValidationError("Asset filename is required.")

    normalized_name = _validate_filename(original_filename)
    extension = PurePosixPath(normalized_name).suffix.lower()
    mime_type = (declared_mime_type or getattr(uploaded_file, "content_type", "") or "").strip().lower()
    if not mime_type:
        raise FileValidationError("Asset MIME type is required.")

    size_bytes = _get_upload_size(uploaded_file)
    if size_bytes > settings.MAX_FILE_SIZE_BYTES:
        raise FileValidationError("Asset exceeds the configured size limit.")

    header = _read_prefix(uploaded_file, MAX_VALIDATION_BYTES)
    if extension == ".mtl":
        if mime_type not in {"text/plain", "model/mtl", "text/mtl", "application/octet-stream"}:
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _validate_mtl(uploaded_file=uploaded_file, size_bytes=size_bytes)
    elif extension == ".png":
        if mime_type != "image/png":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _validate_png(uploaded_file=uploaded_file, size_bytes=size_bytes)
    elif extension in {".jpg", ".jpeg"}:
        if mime_type != "image/jpeg":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _validate_jpeg(uploaded_file=uploaded_file, size_bytes=size_bytes)
    else:
        raise FileValidationError("Unsupported OBJ asset extension.")

    sanitized_filename = sanitize_storage_filename(PurePosixPath(normalized_name).name)
    return ValidatedAssetUpload(
        original_filename=PurePosixPath(normalized_name).name,
        sanitized_filename=sanitized_filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )

def validate_gltf_asset_upload(uploaded_file, declared_mime_type=None):
    """Validate a binary buffer or texture explicitly referenced by a GLTF manifest."""
    original_filename = getattr(uploaded_file, "name", "")
    if not original_filename:
        raise FileValidationError("Asset filename is required.")

    normalized_name = _validate_filename(original_filename)
    extension = PurePosixPath(normalized_name).suffix.lower()
    mime_type = (declared_mime_type or getattr(uploaded_file, "content_type", "") or "").strip().lower()
    if not mime_type:
        raise FileValidationError("Asset MIME type is required.")

    size_bytes = _get_upload_size(uploaded_file)
    if size_bytes > settings.MAX_FILE_SIZE_BYTES:
        raise FileValidationError("Asset exceeds the configured size limit.")

    header = _read_prefix(uploaded_file, MAX_VALIDATION_BYTES)
    if extension == ".bin":
        if mime_type != "application/octet-stream":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
    elif extension == ".png":
        if mime_type != "image/png":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _validate_png(uploaded_file=uploaded_file, size_bytes=size_bytes)
    elif extension in {".jpg", ".jpeg"}:
        if mime_type != "image/jpeg":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _validate_jpeg(uploaded_file=uploaded_file, size_bytes=size_bytes)
    else:
        raise FileValidationError("Unsupported GLTF asset extension.")

    sanitized_filename = sanitize_storage_filename(PurePosixPath(normalized_name).name)
    return ValidatedAssetUpload(
        original_filename=PurePosixPath(normalized_name).name,
        sanitized_filename=sanitized_filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )

def get_gltf_external_resource_references(uploaded_file) -> list[PurePosixPath]:
    """Return safe, non-data URIs from GLTF buffers and images.

    Browser file inputs expose companion files by basename only. Duplicate referenced
    basenames are therefore rejected so they cannot be confused during worker staging.
    """
    size_bytes = _get_upload_size(uploaded_file)
    raw_references = get_gltf_external_resource_uris(uploaded_file, size_bytes=size_bytes)
    references = []
    seen_filenames = set()
    for uri in raw_references:
        _ensure(isinstance(uri, str) and uri.strip(), "GLTF resource URI is invalid.")
        if uri.startswith("data:"):
            continue
        reference = _validate_gltf_resource_uri(uri)
        filename = sanitize_storage_filename(reference.name)
        _ensure(filename not in seen_filenames, "GLTF companion filenames must be unique.")
        seen_filenames.add(filename)
        references.append(reference)
    return references
