import json
import struct
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from apps.files.models import FileFormat

from .rules import (
    MAX_VALIDATION_BYTES,
    _ASCII_STL_RE,
    _GLB_SIGNATURE,
    _JPEG_SIGNATURE,
    _LAS_SIGNATURE,
    _MTL_PREFIXES,
    _OBJ_PREFIXES,
    _PNG_SIGNATURE,
    _TIFF_SIGNATURES,
)
from .streams import _read_exact_bytes
from .types import FileValidationError

def _detect_format(extension, header, uploaded_file=None, size_bytes=None):
    if extension in {".tif", ".tiff"}:
        return _detect_tiff_format(
            header,
            uploaded_file=uploaded_file,
            size_bytes=size_bytes,
        )
    if extension == ".png":
        _ensure(header.startswith(_PNG_SIGNATURE), "Invalid PNG signature.")
        return FileFormat.PNG
    if extension in {".jpg", ".jpeg"}:
        _ensure(header.startswith(_JPEG_SIGNATURE), "Invalid JPEG signature.")
        return FileFormat.JPEG
    if extension == ".kml":
        _validate_kml(header)
        return FileFormat.KML
    if extension in {".geojson", ".json", ".geo.json"}:
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

def _detect_tiff_format(header, uploaded_file=None, size_bytes=None):
    signature = header[:4]
    _ensure(signature in _TIFF_SIGNATURES, "Invalid TIFF signature.")

    if signature in {b"II+\x00", b"MM\x00+"}:
        return FileFormat.TIFF

    _ensure(len(header) >= 8, "Incomplete TIFF header.")
    endian = "<" if signature == b"II*\x00" else ">"
    ifd_offset = struct.unpack(f"{endian}I", header[4:8])[0]

    size_bytes = size_bytes if size_bytes is not None else len(header)
    _ensure(ifd_offset + 2 <= size_bytes, "Incomplete TIFF header.")

    entry_count_data = _read_tiff_bytes(
        header=header,
        uploaded_file=uploaded_file,
        offset=ifd_offset,
        size=2,
        size_bytes=size_bytes,
    )
    entry_count = struct.unpack(f"{endian}H", entry_count_data)[0]
    entries_offset = ifd_offset + 2
    entries_size = entry_count * 12
    _ensure(
        entries_size <= MAX_VALIDATION_BYTES,
        "TIFF IFD directory exceeds validation read limit.",
    )
    needed_length = entries_offset + entries_size
    _ensure(needed_length <= size_bytes, "Incomplete TIFF metadata.")

    entries = _read_tiff_bytes(
        header=header,
        uploaded_file=uploaded_file,
        offset=entries_offset,
        size=entries_size,
        size_bytes=size_bytes,
    )

    for index in range(entry_count):
        start = index * 12
        tag = struct.unpack(f"{endian}H", entries[start : start + 2])[0]
        if tag == 34735:
            return FileFormat.GEOTIFF
    return FileFormat.TIFF

def _read_tiff_bytes(*, header, uploaded_file, offset, size, size_bytes):
    _ensure(offset >= 0 and size >= 0, "Incomplete TIFF metadata.")
    _ensure(offset + size <= size_bytes, "Incomplete TIFF metadata.")
    if offset + size <= len(header):
        return header[offset : offset + size]

    _ensure(uploaded_file is not None, "Incomplete TIFF metadata.")
    return _read_exact_bytes(uploaded_file, offset=offset, size=size)

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
    _load_gltf_payload(header)

def _load_gltf_payload(header):
    payload = _load_json_text(header, "Malformed GLTF content.")
    _ensure(isinstance(payload, dict), "GLTF must be a JSON object.")
    asset = payload.get("asset")
    _ensure(isinstance(asset, dict), "GLTF asset metadata is required.")
    return payload

def _validate_gltf_resource_uri(uri: str) -> PurePosixPath:
    if "\\" in uri or ":" in uri or uri.startswith(("/", "\\")) or any(
        marker in uri for marker in ("?", "#")
    ):
        raise FileValidationError("GLTF resource URI must be a relative file path.")

    path = PurePosixPath(uri)
    if not path.name or path.is_absolute() or ".." in path.parts:
        raise FileValidationError("GLTF resource URI must not escape the upload bundle.")
    return path

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
