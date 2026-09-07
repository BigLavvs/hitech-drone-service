from pathlib import PurePosixPath

from .rules import VALIDATION_READ_CHUNK_BYTES, _FORMAT_RULES, _SAFE_FILENAME_RE
from .types import FileValidationError

def sanitize_storage_filename(filename):
    base_name = PurePosixPath(filename).name
    stem = PurePosixPath(base_name).stem
    suffix = PurePosixPath(base_name).suffix.lower()
    cleaned_stem = _SAFE_FILENAME_RE.sub("-", stem).strip("-._")
    cleaned_stem = cleaned_stem or "file"
    return f"{cleaned_stem}{suffix}"

def _get_primary_extension(filename):
    path = PurePosixPath(filename)
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if len(suffixes) >= 2:
        combined_suffix = "".join(suffixes[-2:])
        if combined_suffix in _FORMAT_RULES:
            return combined_suffix
    if suffixes:
        return suffixes[-1]
    return ""

def _validate_filename(filename):
    candidate = filename.replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not path.name
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts[:-1])
        or len(path.parts) != 1
    ):
        raise FileValidationError("Invalid filename.")
    return path.name

def _get_upload_size(uploaded_file):
    explicit_size = getattr(uploaded_file, "size", None)
    if explicit_size is not None:
        return explicit_size

    current_position = uploaded_file.tell()
    uploaded_file.seek(0, 2)
    size_bytes = uploaded_file.tell()
    uploaded_file.seek(current_position)
    return size_bytes

def _read_prefix(uploaded_file, limit):
    current_position = uploaded_file.tell()
    uploaded_file.seek(0)
    header = uploaded_file.read(limit)
    uploaded_file.seek(current_position)
    return header


def _iter_content_chunks(uploaded_file, *, size_bytes, content=None, start_offset=0):
    """Yield an upload in bounded chunks while preserving its file position."""
    if content is not None:
        content = content[start_offset:]
        for offset in range(0, len(content), VALIDATION_READ_CHUNK_BYTES):
            yield content[offset : offset + VALIDATION_READ_CHUNK_BYTES]
        return

    current_position = uploaded_file.tell()
    try:
        uploaded_file.seek(start_offset)
        remaining = size_bytes - start_offset
        while remaining:
            chunk = uploaded_file.read(min(VALIDATION_READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise FileValidationError("Structured file is truncated.")
            yield chunk
            remaining -= len(chunk)
    finally:
        uploaded_file.seek(current_position)

def _read_exact_bytes(uploaded_file, *, offset, size):
    current_position = uploaded_file.tell()
    try:
        uploaded_file.seek(offset)
        data = uploaded_file.read(size)
    finally:
        uploaded_file.seek(current_position)
    if len(data) != size:
        raise FileValidationError("Incomplete TIFF metadata.")
    return data
