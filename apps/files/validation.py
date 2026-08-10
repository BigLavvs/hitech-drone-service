import json
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.conf import settings

from apps.files.models import FileFormat, FileType


MAX_VALIDATION_BYTES = 262144

_TIFF_SIGNATURES = (
    b"II*\x00",
    b"MM\x00*",
    b"II+\x00",
    b"MM\x00+",
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_GLB_SIGNATURE = b"glTF"
_LAS_SIGNATURE = b"LASF"

_TEXTUAL_FORMATS = {
    FileFormat.KML,
    FileFormat.GEOJSON,
    FileFormat.OBJ,
    FileFormat.GLTF,
    FileFormat.STL,
}

_BINARY_SIGNATURE_FORMATS = {
    FileFormat.TIFF,
    FileFormat.GEOTIFF,
    FileFormat.PNG,
    FileFormat.JPEG,
    FileFormat.GLB,
    FileFormat.LAS,
    FileFormat.LAZ,
    FileFormat.PLY,
}

_FORMAT_RULES = {
    ".tif": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/tiff", "image/geotiff", "image/x-geotiff"},
        "formats": {FileFormat.TIFF, FileFormat.GEOTIFF},
    },
    ".tiff": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/tiff", "image/geotiff", "image/x-geotiff"},
        "formats": {FileFormat.TIFF, FileFormat.GEOTIFF},
    },
    ".png": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/png"},
        "formats": {FileFormat.PNG},
    },
    ".jpg": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/jpeg"},
        "formats": {FileFormat.JPEG},
    },
    ".jpeg": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/jpeg"},
        "formats": {FileFormat.JPEG},
    },
    ".kml": {
        "file_type": FileType.TWO_D,
        "mime_types": {"application/vnd.google-earth.kml+xml"},
        "formats": {FileFormat.KML},
    },
    ".geojson": {
        "file_type": FileType.TWO_D,
        "mime_types": {"application/geo+json", "application/json"},
        "formats": {FileFormat.GEOJSON},
    },
    ".obj": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/obj", "text/plain"},
        "formats": {FileFormat.OBJ},
    },
    ".glb": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/gltf-binary", "application/octet-stream+gltf"},
        "formats": {FileFormat.GLB},
    },
    ".gltf": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/gltf+json", "application/gltf+json"},
        "formats": {FileFormat.GLTF},
    },
    ".las": {
        "file_type": FileType.THREE_D,
        "mime_types": {"application/vnd.las", "application/x-las"},
        "formats": {FileFormat.LAS},
    },
    ".laz": {
        "file_type": FileType.THREE_D,
        "mime_types": {"application/vnd.laszip", "application/x-laz"},
        "formats": {FileFormat.LAZ},
    },
    ".ply": {
        "file_type": FileType.THREE_D,
        "mime_types": {"application/ply", "model/ply"},
        "formats": {FileFormat.PLY},
    },
    ".stl": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/stl", "application/sla"},
        "formats": {FileFormat.STL},
    },
}

_ASCII_STL_RE = re.compile(r"^\s*solid\b", re.IGNORECASE)
_OBJ_PREFIXES = (
    "#",
    "v ",
    "vt ",
    "vn ",
    "f ",
    "o ",
    "g ",
    "s ",
    "mtllib ",
    "usemtl ",
)
_MTL_PREFIXES = (
    "#",
    "newmtl ",
    "ka ",
    "kd ",
    "ks ",
    "ke ",
    "ns ",
    "ni ",
    "d ",
    "tr ",
    "tf ",
    "illum ",
    "map_",
    "bump ",
    "disp ",
    "decal ",
    "refl ",
)
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


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


def validate_upload(uploaded_file, declared_mime_type=None):
    original_filename = getattr(uploaded_file, "name", "")
    if not original_filename:
        raise FileValidationError("Filename is required.")

    normalized_name = _validate_filename(original_filename)
    extension = PurePosixPath(normalized_name).suffix.lower()
    format_rule = _FORMAT_RULES.get(extension)
    if format_rule is None:
        raise FileValidationError("Unsupported file extension.")

    mime_type = (declared_mime_type or getattr(uploaded_file, "content_type", "") or "").strip().lower()
    if not mime_type:
        raise FileValidationError("MIME type is required.")
    if mime_type == "application/octet-stream" or mime_type not in format_rule["mime_types"]:
        raise FileValidationError("Unsupported or mismatched MIME type.")

    size_bytes = _get_upload_size(uploaded_file)
    if size_bytes > settings.MAX_FILE_SIZE_BYTES:
        raise FileValidationError("File exceeds the configured size limit.")

    header = _read_prefix(uploaded_file, MAX_VALIDATION_BYTES)
    detected_format = _detect_format(extension, header)
    if detected_format not in format_rule["formats"]:
        raise FileValidationError("Filename extension does not match file content.")

    sanitized_filename = sanitize_storage_filename(PurePosixPath(normalized_name).name)
    return ValidatedUpload(
        original_filename=PurePosixPath(normalized_name).name,
        sanitized_filename=sanitized_filename,
        mime_type=mime_type,
        file_type=format_rule["file_type"],
        file_format=detected_format,
        size_bytes=size_bytes,
    )


def sanitize_storage_filename(filename):
    base_name = PurePosixPath(filename).name
    stem = PurePosixPath(base_name).stem
    suffix = PurePosixPath(base_name).suffix.lower()
    cleaned_stem = _SAFE_FILENAME_RE.sub("-", stem).strip("-._")
    cleaned_stem = cleaned_stem or "file"
    return f"{cleaned_stem}{suffix}"


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
        if mime_type not in {"text/plain", "model/mtl", "text/mtl"}:
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _validate_mtl(header)
    elif extension == ".png":
        if mime_type != "image/png":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _ensure(header.startswith(_PNG_SIGNATURE), "Invalid PNG signature.")
    elif extension in {".jpg", ".jpeg"}:
        if mime_type != "image/jpeg":
            raise FileValidationError("Unsupported or mismatched asset MIME type.")
        _ensure(header.startswith(_JPEG_SIGNATURE), "Invalid JPEG signature.")
    else:
        raise FileValidationError("Unsupported OBJ asset extension.")

    sanitized_filename = sanitize_storage_filename(PurePosixPath(normalized_name).name)
    return ValidatedAssetUpload(
        original_filename=PurePosixPath(normalized_name).name,
        sanitized_filename=sanitized_filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )


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


def _detect_format(extension, header):
    if extension in {".tif", ".tiff"}:
        return _detect_tiff_format(header)
    if extension == ".png":
        _ensure(header.startswith(_PNG_SIGNATURE), "Invalid PNG signature.")
        return FileFormat.PNG
    if extension in {".jpg", ".jpeg"}:
        _ensure(header.startswith(_JPEG_SIGNATURE), "Invalid JPEG signature.")
        return FileFormat.JPEG
    if extension == ".kml":
        _validate_kml(header)
        return FileFormat.KML
    if extension == ".geojson":
        _validate_geojson(header)
        return FileFormat.GEOJSON
    if extension == ".obj":
        _validate_obj(header)
        return FileFormat.OBJ
    if extension == ".glb":
        _ensure(header.startswith(_GLB_SIGNATURE), "Invalid GLB signature.")
        return FileFormat.GLB
    if extension == ".gltf":
        _validate_gltf(header)
        return FileFormat.GLTF
    if extension == ".las":
        _ensure(header.startswith(_LAS_SIGNATURE), "Invalid LAS signature.")
        return FileFormat.LAS
    if extension == ".laz":
        _ensure(header.startswith(_LAS_SIGNATURE), "Invalid LAZ signature.")
        return FileFormat.LAZ
    if extension == ".ply":
        _validate_ply(header)
        return FileFormat.PLY
    if extension == ".stl":
        _validate_ascii_stl(header)
        return FileFormat.STL
    raise FileValidationError("Unsupported file extension.")


def _detect_tiff_format(header):
    signature = header[:4]
    _ensure(signature in _TIFF_SIGNATURES, "Invalid TIFF signature.")

    if signature in {b"II+\x00", b"MM\x00+"}:
        return FileFormat.TIFF

    endian = "<" if signature == b"II*\x00" else ">"
    ifd_offset = struct.unpack(f"{endian}I", header[4:8])[0]
    entry_count_offset = ifd_offset
    _ensure(len(header) >= entry_count_offset + 2, "Incomplete TIFF header.")
    entry_count = struct.unpack(f"{endian}H", header[entry_count_offset : entry_count_offset + 2])[0]
    entries_offset = entry_count_offset + 2
    needed_length = entries_offset + (entry_count * 12)
    _ensure(len(header) >= needed_length, "Incomplete TIFF metadata.")

    for index in range(entry_count):
        start = entries_offset + (index * 12)
        tag = struct.unpack(f"{endian}H", header[start : start + 2])[0]
        if tag == 34735:
            return FileFormat.GEOTIFF
    return FileFormat.TIFF


def _validate_kml(header):
    text = _decode_text(header)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FileValidationError("Malformed KML content.") from exc
    root_tag = root.tag.lower()
    _ensure(root_tag.endswith("kml"), "KML root element is required.")


def _validate_geojson(header):
    payload = _load_json_text(header, "Malformed GeoJSON content.")
    _ensure(isinstance(payload, dict), "GeoJSON must be a JSON object.")
    _ensure(payload.get("type") in {"FeatureCollection", "Feature", "GeometryCollection", "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}, "GeoJSON type is required.")


def _validate_gltf(header):
    payload = _load_json_text(header, "Malformed GLTF content.")
    asset = payload.get("asset")
    _ensure(isinstance(asset, dict), "GLTF asset metadata is required.")


def _validate_obj(header):
    text = _decode_text(header)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    _ensure(lines, "OBJ content is empty.")
    _ensure(any(line.startswith(_OBJ_PREFIXES) for line in lines), "OBJ content is malformed.")


def _validate_mtl(header):
    text = _decode_text(header)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    _ensure(lines, "MTL content is empty.")
    _ensure(any(line.lower().startswith(_MTL_PREFIXES) for line in lines), "MTL content is malformed.")


def _validate_ply(header):
    text = _decode_text(header)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    _ensure(lines and lines[0] == "ply", "PLY header is required.")
    _ensure(
        any(line.startswith("format ascii 1.0") or line.startswith("format binary_little_endian 1.0") or line.startswith("format binary_big_endian 1.0") for line in lines[1:4]),
        "PLY format header is required.",
    )


def _validate_ascii_stl(header):
    text = _decode_text(header)
    _ensure(_ASCII_STL_RE.search(text), "ASCII STL must start with 'solid'.")
    _ensure("facet normal" in text.lower(), "ASCII STL facet data is required.")


def _load_json_text(header, error_message):
    text = _decode_text(header)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FileValidationError(error_message) from exc


def _decode_text(header):
    try:
        return header.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileValidationError("Text-based file must be UTF-8 encoded.") from exc


def _ensure(condition, message):
    if not condition:
        raise FileValidationError(message)
