import hashlib
import json
import mimetypes
from io import BytesIO
from pathlib import Path

from apps.files.object_keys import build_generated_object_key, build_generated_object_prefix
from apps.files.validation import validate_upload

from .types import ChecksumMismatchError, _NamedValidationFile


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
