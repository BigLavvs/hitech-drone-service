from dataclasses import dataclass


class FileValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedUpload:
    original_filename: str
    sanitized_filename: str
    mime_type: str
    file_type: str
    file_format: str
    size_bytes: int


@dataclass(frozen=True)
class ValidatedAssetUpload:
    original_filename: str
    sanitized_filename: str
    mime_type: str
    size_bytes: int
