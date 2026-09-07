import json
import math
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.parsers import expat

from apps.files.models import FileFormat

from .rules import (
    MAX_VALIDATION_BYTES,
    MAX_JPEG_DECODE_BYTES,
    MAX_JPEG_DECODE_PIXELS,
    _ASCII_STL_RE,
    _GLB_SIGNATURE,
    _JPEG_SIGNATURE,
    _LAS_SIGNATURE,
    _MTL_PREFIXES,
    _OBJ_PREFIXES,
    _PNG_SIGNATURE,
    _TIFF_SIGNATURES,
)
from .streams import (
    VALIDATION_READ_CHUNK_BYTES,
    _iter_content_chunks,
    _read_exact_bytes,
)
from .types import FileValidationError

def _detect_format(extension, header, uploaded_file=None, size_bytes=None, content=None):
    content = content if content is not None or uploaded_file is not None else header
    if extension in {".tif", ".tiff"}:
        return _detect_tiff_format(
            header,
            uploaded_file=uploaded_file,
            size_bytes=size_bytes,
        )
    if extension == ".png":
        _validate_png(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
        return FileFormat.PNG
    if extension in {".jpg", ".jpeg"}:
        _validate_jpeg(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
        return FileFormat.JPEG
    if extension == ".kml":
        _validate_kml(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
        return FileFormat.KML
    if extension in {".geojson", ".json", ".geo.json"}:
        _validate_geojson(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
        return FileFormat.GEOJSON
    if extension == ".obj":
        _validate_obj(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
        return FileFormat.OBJ
    if extension == ".glb":
        _ensure(header.startswith(_GLB_SIGNATURE), "Invalid GLB signature.")
        return FileFormat.GLB
    if extension == ".gltf":
        _validate_gltf(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
        return FileFormat.GLTF
    if extension == ".las":
        _ensure(header.startswith(_LAS_SIGNATURE), "Invalid LAS signature.")
        return FileFormat.LAS
    if extension == ".laz":
        _ensure(header.startswith(_LAS_SIGNATURE), "Invalid LAZ signature.")
        return FileFormat.LAZ
    if extension == ".ply":
        _validate_ply(uploaded_file=uploaded_file, size_bytes=size_bytes, header=header)
        return FileFormat.PLY
    if extension == ".stl":
        _validate_ascii_stl(content, uploaded_file=uploaded_file, size_bytes=size_bytes)
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

def _validate_kml(content=None, *, uploaded_file=None, size_bytes=None):
    root_name = None
    depth = 0

    def on_start(name, _attributes):
        nonlocal root_name, depth
        if root_name is None:
            root_name = name.rsplit(":", 1)[-1].lower()
        depth += 1

    def on_end(_name):
        nonlocal depth
        depth -= 1

    def reject_declaration(*_args):
        raise FileValidationError("KML entities are not supported.")

    parser = expat.ParserCreate()
    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_declaration
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        for chunk in _iter_content_chunks(uploaded_file, size_bytes=size_bytes, content=content):
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except FileValidationError:
        raise
    except expat.ExpatError as exc:
        raise FileValidationError("Malformed KML content.") from exc
    _ensure(root_name == "kml" and depth == 0, "KML root element is required.")


def _validate_geojson(content=None, *, uploaded_file=None, size_bytes=None):
    reader = _JsonReader(uploaded_file=uploaded_file, size_bytes=size_bytes, content=content)
    try:
        _parse_geojson_object(reader)
        reader.ensure_eof()
    except _JsonParseError as exc:
        raise FileValidationError("Malformed GeoJSON content.") from exc


def _validate_gltf(content=None, *, uploaded_file=None, size_bytes=None):
    reader = _JsonReader(uploaded_file=uploaded_file, size_bytes=size_bytes, content=content)
    try:
        _parse_gltf_object(reader, collect_references=False)
        reader.ensure_eof()
    except _JsonParseError as exc:
        raise FileValidationError("Malformed GLTF content.") from exc


def _load_gltf_payload(header):
    """Compatibility helper for callers that already have a small JSON header."""
    payload = _load_json_text(header, "Malformed GLTF content.")
    _ensure(isinstance(payload, dict), "GLTF must be a JSON object.")
    _ensure(isinstance(payload.get("asset"), dict), "GLTF asset metadata is required.")
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

def _validate_obj(content=None, *, uploaded_file=None, size_bytes=None):
    saw_content = saw_geometry = False
    for line in _iter_text_lines(uploaded_file, size_bytes=size_bytes, content=content):
        line = line.strip()
        if not line:
            continue
        saw_content = True
        saw_geometry |= line.startswith(_OBJ_PREFIXES[1:])
    _ensure(saw_content, "OBJ content is empty.")
    _ensure(saw_geometry, "OBJ content is malformed.")


def _validate_mtl(content=None, *, uploaded_file=None, size_bytes=None):
    saw_content = saw_material = False
    for line in _iter_text_lines(uploaded_file, size_bytes=size_bytes, content=content):
        line = line.strip()
        if not line:
            continue
        saw_content = True
        saw_material |= line.lower().startswith(_MTL_PREFIXES)
    _ensure(saw_content, "MTL content is empty.")
    _ensure(saw_material, "MTL content is malformed.")

def _validate_ply(*, uploaded_file, size_bytes, header):
    header_bytes, body_offset = _read_ply_header(
        uploaded_file=uploaded_file, size_bytes=size_bytes, initial_header=header
    )
    header_text = _decode_text(header_bytes)
    elements, format_name = _parse_ply_header(header_text)
    if format_name == "ascii":
        _validate_ascii_ply_stream(
            uploaded_file=uploaded_file,
            size_bytes=size_bytes,
            body_offset=body_offset,
            elements=elements,
        )
        return

    endian = "<" if format_name == "binary_little_endian" else ">"
    _validate_binary_ply_body(
        uploaded_file=uploaded_file,
        size_bytes=size_bytes,
        body_offset=body_offset,
        elements=elements,
        endian=endian,
    )


_PLY_SCALAR_SIZES = {
    "char": 1, "int8": 1, "uchar": 1, "uint8": 1,
    "short": 2, "int16": 2, "ushort": 2, "uint16": 2,
    "int": 4, "int32": 4, "uint": 4, "uint32": 4,
    "float": 4, "float32": 4, "double": 8, "float64": 8,
}


def _read_ply_header(*, uploaded_file, size_bytes, initial_header):
    current_position = uploaded_file.tell()
    try:
        uploaded_file.seek(0)
        data = bytearray()
        while len(data) <= min(size_bytes, MAX_VALIDATION_BYTES):
            marker = bytes(data).find(b"\nend_header\n")
            if marker >= 0:
                end = marker + len(b"\nend_header\n")
                return bytes(data[:end]), end
            marker = bytes(data).find(b"\nend_header\r\n")
            if marker >= 0:
                end = marker + len(b"\nend_header\r\n")
                return bytes(data[:end]), end
            chunk = uploaded_file.read(min(VALIDATION_READ_CHUNK_BYTES, MAX_VALIDATION_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        raise FileValidationError("PLY end_header marker is required.")
    finally:
        uploaded_file.seek(current_position)


def _parse_ply_header(text):
    raw_lines = text.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    _ensure(lines and lines[0] == "ply", "PLY header is required.")
    format_line = next((line for line in lines if line.startswith("format ")), None)
    _ensure(format_line in {
        "format ascii 1.0",
        "format binary_little_endian 1.0",
        "format binary_big_endian 1.0",
    }, "PLY format header is required.")
    format_name = format_line.split()[1]
    elements = []
    current_element = None
    for line in lines[1:]:
        if line.startswith("comment ") or line.startswith("obj_info ") or line.startswith("format "):
            continue
        parts = line.split()
        if parts[0] == "element":
            _ensure(len(parts) == 3 and parts[1] and parts[2].isdigit(), "PLY element declaration is invalid.")
            current_element = {"name": parts[1], "count": int(parts[2]), "properties": []}
            elements.append(current_element)
        elif parts[0] == "property":
            _ensure(current_element is not None, "PLY property must follow an element declaration.")
            if len(parts) == 3:
                _ensure(parts[1] in _PLY_SCALAR_SIZES, "PLY property type is unsupported.")
                current_element["properties"].append(("scalar", parts[1], parts[2]))
            elif len(parts) == 5 and parts[1] == "list":
                _ensure(parts[2] in _PLY_SCALAR_SIZES and parts[3] in _PLY_SCALAR_SIZES, "PLY list property type is unsupported.")
                current_element["properties"].append(("list", parts[2], parts[3], parts[4]))
            else:
                raise FileValidationError("PLY property declaration is invalid.")
        elif parts[0] == "end_header":
            break
        else:
            raise FileValidationError("PLY header contains an unsupported declaration.")
    _ensure(elements, "PLY element declaration is required.")
    _ensure(all(element["properties"] for element in elements), "PLY properties are required.")
    return elements, format_name


def _validate_ascii_ply_body(*, elements, text):
    rows = [line.split() for line in text.splitlines() if line.strip()]
    row_index = 0
    for element in elements:
        for _ in range(element["count"]):
            _ensure(row_index < len(rows), "PLY body is truncated.")
            tokens = rows[row_index]
            token_index = 0
            for property_spec in element["properties"]:
                if property_spec[0] == "scalar":
                    _ensure(token_index < len(tokens), "PLY body is truncated.")
                    _validate_ply_scalar_token(tokens[token_index], property_spec[1])
                    token_index += 1
                else:
                    _ensure(token_index < len(tokens), "PLY list property is truncated.")
                    try:
                        list_count = int(tokens[token_index])
                    except ValueError as exc:
                        raise FileValidationError("PLY list count is invalid.") from exc
                    _ensure(0 <= list_count <= 10_000_000, "PLY list count exceeds the validation resource limit.")
                    _ensure(
                        token_index + 1 + list_count <= len(tokens),
                        "PLY list property is truncated.",
                    )
                    for value in tokens[token_index + 1:token_index + 1 + list_count]:
                        _validate_ply_scalar_token(value, property_spec[2])
                    token_index += 1 + list_count
            _ensure(token_index == len(tokens), "PLY row contains unexpected values.")
            row_index += 1
    _ensure(row_index == len(rows), "PLY body contains trailing data.")


def _validate_binary_ply_body(*, uploaded_file, size_bytes, body_offset, elements, endian):
    current_position = uploaded_file.tell()
    cursor = body_offset
    try:
        for element in elements:
            for _ in range(element["count"]):
                for property_spec in element["properties"]:
                    if property_spec[0] == "scalar":
                        cursor += _PLY_SCALAR_SIZES[property_spec[1]]
                    else:
                        count_size = _PLY_SCALAR_SIZES[property_spec[1]]
                        uploaded_file.seek(cursor)
                        count_bytes = uploaded_file.read(count_size)
                        _ensure(len(count_bytes) == count_size, "PLY binary body is truncated.")
                        count_format = _ply_struct_format(property_spec[1], endian)
                        list_count = struct.unpack(count_format, count_bytes)[0]
                        _ensure(0 <= list_count <= 10_000_000, "PLY list count exceeds the validation resource limit.")
                        cursor += count_size + list_count * _PLY_SCALAR_SIZES[property_spec[2]]
                    _ensure(cursor <= size_bytes, "PLY binary body is truncated.")
        _ensure(cursor == size_bytes, "PLY binary body contains trailing data.")
    finally:
        uploaded_file.seek(current_position)


def _ply_struct_format(type_name, endian):
    if type_name in {"char", "int8"}: return f"{endian}b"
    if type_name in {"uchar", "uint8"}: return f"{endian}B"
    if type_name in {"short", "int16"}: return f"{endian}h"
    if type_name in {"ushort", "uint16"}: return f"{endian}H"
    if type_name in {"int", "int32"}: return f"{endian}i"
    if type_name in {"uint", "uint32"}: return f"{endian}I"
    if type_name in {"float", "float32"}: return f"{endian}f"
    return f"{endian}d"


def _validate_ply_scalar_token(value, type_name):
    try:
        if type_name in {"float", "float32", "double", "float64"}:
            parsed = float(value)
            _ensure(math.isfinite(parsed), "PLY numeric value is invalid.")
        else:
            int(value)
    except (TypeError, ValueError) as exc:
        raise FileValidationError("PLY numeric value is invalid.") from exc

def _validate_ascii_stl(content=None, *, uploaded_file=None, size_bytes=None):
    saw_first_nonempty = saw_solid = saw_facet = False
    for line in _iter_text_lines(uploaded_file, size_bytes=size_bytes, content=content):
        if line.strip() and not saw_first_nonempty:
            saw_first_nonempty = True
            saw_solid = bool(_ASCII_STL_RE.search(line))
        saw_facet |= "facet normal" in line.lower()
    _ensure(saw_solid, "ASCII STL must start with 'solid'.")
    _ensure(saw_facet, "ASCII STL facet data is required.")

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


_GEOJSON_TYPES = {
    "FeatureCollection", "Feature", "GeometryCollection", "Point", "MultiPoint",
    "LineString", "MultiLineString", "Polygon", "MultiPolygon",
}
_GEOJSON_COORDINATE_DEPTH = {
    "Point": 1,
    "MultiPoint": 2,
    "LineString": 2,
    "MultiLineString": 3,
    "Polygon": 3,
    "MultiPolygon": 4,
}


@dataclass(frozen=True)
class _CoordinateSummary:
    kind: str
    count: int = 0
    first: object = None
    last: object = None
    child: object = None
    minimum_counts: tuple = ()
    all_closed: tuple = ()

    @property
    def shape(self):
        return self.kind, self.child.shape if self.child is not None else None


def _parse_geojson_object(reader):
    reader.expect("{")
    fields = set()
    geojson_type = None
    coordinates = None
    features_count = None
    geometries_count = None
    geometry_present = False
    geometry_is_null = False
    if not reader.consume("}"):
        while True:
            key = reader.read_string(capture=True)
            reader.expect(":")
            _ensure(key not in fields, "GeoJSON object contains duplicate fields.")
            fields.add(key)
            if key == "type":
                geojson_type = reader.read_string(capture=True)
            elif key == "coordinates":
                coordinates = _parse_coordinate_summary(reader)
            elif key == "features":
                features_count = _parse_object_array(reader, expected_type="Feature")
            elif key == "geometries":
                geometries_count = _parse_object_array(reader)
            elif key == "geometry":
                geometry_present = True
                if reader.consume("null"):
                    geometry_is_null = True
                else:
                    _parse_geojson_object(reader)
            else:
                reader.skip_value()
            if reader.consume("}"):
                break
            reader.expect(",")
            _ensure(reader.peek() != "}", "GeoJSON object has a trailing comma.")

    _ensure(geojson_type in _GEOJSON_TYPES, "GeoJSON type is required.")
    if geojson_type == "FeatureCollection":
        _ensure(features_count is not None, "GeoJSON FeatureCollection features are required.")
    elif geojson_type == "Feature":
        _ensure(geometry_present, "GeoJSON feature geometry is required.")
        _ensure(geometry_is_null or geometry_present, "GeoJSON feature geometry is invalid.")
    elif geojson_type == "GeometryCollection":
        _ensure(geometries_count is not None, "GeoJSON geometries are required.")
    else:
        _ensure(coordinates is not None, "GeoJSON coordinates are required.")
        _validate_coordinate_summary(coordinates, geojson_type, _GEOJSON_COORDINATE_DEPTH[geojson_type])
    reader.last_object_type = geojson_type


def _parse_object_array(reader, *, expected_type=None):
    reader.expect("[")
    count = 0
    if reader.consume("]"):
        return count
    while True:
        reader.expect("{")
        reader.pushback_object_start()
        _parse_geojson_object(reader)
        if expected_type is not None:
            _ensure(reader.last_object_type == expected_type, "GeoJSON feature is invalid.")
        count += 1
        if reader.consume("]"):
            break
        reader.expect(",")
        _ensure(reader.peek() != "]", "GeoJSON array has a trailing comma.")
    return count


def _parse_coordinate_summary(reader):
    return _parse_coordinate_value(reader)


def _parse_coordinate_value(reader):
    if reader.consume("["):
        _ensure(not reader.consume("]"), "GeoJSON coordinates are required.")
        first = last = _parse_coordinate_value(reader)
        count = 1
        minimum_counts = list(first.minimum_counts)
        all_closed = list(first.all_closed)
        while not reader.consume("]"):
            reader.expect(",")
            _ensure(reader.peek() != "]", "GeoJSON coordinate array has a trailing comma.")
            child = _parse_coordinate_value(reader)
            _ensure(child.shape == first.shape, "GeoJSON coordinate nesting is inconsistent.")
            count += 1
            last = child
            for index, child_count in enumerate(child.minimum_counts):
                if index == len(minimum_counts):
                    minimum_counts.append(child_count)
                    all_closed.append(child.all_closed[index])
                else:
                    minimum_counts[index] = min(minimum_counts[index], child_count)
                    all_closed[index] &= child.all_closed[index]
        first_position = first.first
        last_position = last.last
        return _CoordinateSummary(
            "array",
            count=count,
            first=first_position,
            last=last_position,
            child=first,
            minimum_counts=(count, *minimum_counts),
            all_closed=(first_position == last_position, *all_closed),
        )

    position = reader.read_number_array_tail()
    _ensure(len(position) >= 2, "GeoJSON positions require longitude and latitude.")
    longitude, latitude = position[:2]
    _ensure(
        math.isfinite(longitude) and math.isfinite(latitude)
        and -180 <= longitude <= 180 and -90 <= latitude <= 90,
        "GeoJSON positions must contain finite WGS84 coordinates.",
    )
    return _CoordinateSummary("position", count=1, first=position, last=position)


def _validate_coordinate_summary(summary, geojson_type, depth):
    _ensure(summary.kind == ("position" if depth == 0 else "array"), "GeoJSON coordinates are malformed.")
    if depth == 0:
        return

    _ensure(summary.minimum_counts and len(summary.minimum_counts) == depth, "GeoJSON coordinates are malformed.")
    if geojson_type in {"LineString", "MultiLineString"}:
        line_level = depth - 2
        _ensure(summary.minimum_counts[line_level] >= 2, "GeoJSON lines require at least two positions.")
    if geojson_type in {"Polygon", "MultiPolygon"}:
        ring_level = depth - 2
        _ensure(summary.minimum_counts[ring_level] >= 4, "GeoJSON linear rings require at least four positions.")
        _ensure(summary.all_closed[ring_level], "GeoJSON linear rings must be closed.")

    child = summary.child
    for child_depth in range(depth - 1, -1, -1):
        _ensure(child.kind == ("position" if child_depth == 0 else "array"), "GeoJSON coordinates are malformed.")
        child = child.child


class _JsonParseError(ValueError):
    pass


_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\Z")
_JSON_WHITESPACE = " \t\r\n"
_JSON_TOKEN_DELIMITERS = ",]}" + _JSON_WHITESPACE


class _JsonReader:
    def __init__(self, *, uploaded_file, size_bytes, content=None):
        self._chunks = iter(_iter_content_chunks(uploaded_file, size_bytes=size_bytes, content=content))
        self._decoder = __import__("codecs").getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._eof = False
        self.last_object_type = None

    def _fill(self):
        if self._eof:
            return False
        try:
            chunk = next(self._chunks)
        except StopIteration:
            try:
                self._buffer += self._decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise _JsonParseError from exc
            self._eof = True
            return bool(self._buffer)
        try:
            self._buffer += self._decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise _JsonParseError from exc
        return True

    def peek(self):
        while not self._buffer and self._fill():
            pass
        return self._buffer[:1]

    def consume(self, token):
        self.skip_whitespace()
        self._fill_to_length(len(token))
        if self._buffer.startswith(token):
            if token in {"true", "false", "null"}:
                self._fill_to_length(len(token) + 1)
                if len(self._buffer) > len(token) and self._buffer[len(token)] not in _JSON_TOKEN_DELIMITERS:
                    return False
            self._buffer = self._buffer[len(token):]
            return True
        return False

    def expect(self, token):
        self.skip_whitespace()
        if not self.consume(token):
            raise _JsonParseError

    def skip_whitespace(self):
        while True:
            if not self._buffer:
                if self._eof or not self._fill():
                    return
            index = 0
            while index < len(self._buffer) and self._buffer[index] in _JSON_WHITESPACE:
                index += 1
            self._buffer = self._buffer[index:]
            if self._buffer or self._eof:
                return

    def _fill_to_length(self, length):
        while len(self._buffer) < length and not self._eof:
            self._fill()

    def read_string(self, *, capture, max_capture=None):
        self.skip_whitespace()
        if not self.consume('"'):
            raise _JsonParseError
        result = []
        while True:
            if not self._buffer and not self._fill():
                raise _JsonParseError
            char = self._buffer[0]
            self._buffer = self._buffer[1:]
            if char == '"':
                return "".join(result) if capture else None
            if ord(char) < 0x20:
                raise _JsonParseError
            if char != "\\":
                if capture and (max_capture is None or len(result) < max_capture):
                    result.append(char)
                continue
            if not self._buffer and not self._fill():
                raise _JsonParseError
            escape = self._buffer[0]
            self._buffer = self._buffer[1:]
            if escape == "u":
                code = self._take(4)
                try:
                    decoded = chr(int(code, 16))
                except ValueError as exc:
                    raise _JsonParseError from exc
                if capture and (max_capture is None or len(result) < max_capture):
                    result.append(decoded)
            elif escape in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
                if capture and (max_capture is None or len(result) < max_capture):
                    result.append({"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(escape, escape))
            else:
                raise _JsonParseError

    def _take(self, count):
        while len(self._buffer) < count and self._fill():
            pass
        if len(self._buffer) < count:
            raise _JsonParseError
        value, self._buffer = self._buffer[:count], self._buffer[count:]
        return value

    def read_number_array_tail(self):
        values = []
        while True:
            token = self.read_scalar_token()
            try:
                value = float(token)
            except ValueError as exc:
                raise _JsonParseError from exc
            _ensure(math.isfinite(value), "GeoJSON positions must contain finite WGS84 coordinates.")
            values.append(value)
            self.skip_whitespace()
            if not self.consume(","):
                break
            self.skip_whitespace()
            if self.peek() in {"", "]"}:
                raise _JsonParseError
        return values

    def read_scalar_token(self):
        self.skip_whitespace()
        token = []
        while True:
            if not self._buffer and not self._fill():
                break
            if self._buffer[:1] in _JSON_TOKEN_DELIMITERS:
                break
            # JSON numbers and literals are intentionally bounded tokens.
            if len(token) >= 256:
                raise _JsonParseError
            token.append(self._buffer[0])
            self._buffer = self._buffer[1:]
        if not token:
            raise _JsonParseError
        value = "".join(token)
        if value not in {"true", "false", "null"} and not _JSON_NUMBER_RE.fullmatch(value):
            raise _JsonParseError
        return value

    def skip_value(self):
        self.skip_whitespace()
        char = self.peek()
        if char == '"':
            self.read_string(capture=False)
        elif char == "{":
            self.expect("{")
            if self.consume("}"):
                return
            while True:
                self.read_string(capture=False)
                self.expect(":")
                self.skip_value()
                if self.consume("}"):
                    break
                self.expect(",")
                if self.peek() == "}":
                    raise _JsonParseError
        elif char == "[":
            self.expect("[")
            if self.consume("]"):
                return
            while True:
                self.skip_value()
                if self.consume("]"):
                    break
                self.expect(",")
                if self.peek() == "]":
                    raise _JsonParseError
        else:
            token = self.read_scalar_token()
            if token not in {"true", "false", "null"}:
                try:
                    value = float(token)
                except ValueError as exc:
                    raise _JsonParseError from exc
                _ensure(math.isfinite(value), "JSON number is invalid.")

    def ensure_eof(self):
        self.skip_whitespace()
        if self.peek():
            raise _JsonParseError

    def pushback_object_start(self):
        # The object opener is consumed by the array parser only to assert its type.
        # Reinsert it so the normal object parser can remain responsible for fields.
        self._buffer = "{" + self._buffer


def _parse_gltf_object(reader, *, collect_references):
    reader.expect("{")
    asset_present = False
    references = []
    if not reader.consume("}"):
        while True:
            key = reader.read_string(capture=True)
            reader.expect(":")
            if key == "asset":
                _ensure(reader.peek() == "{", "GLTF asset metadata is required.")
                reader.skip_value()
                asset_present = True
            elif key in {"buffers", "images"}:
                references.extend(_parse_gltf_resource_array(reader, collect_references=collect_references))
            else:
                reader.skip_value()
            if reader.consume("}"):
                break
            reader.expect(",")
            _ensure(reader.peek() != "}", "GLTF object has a trailing comma.")
    _ensure(asset_present, "GLTF asset metadata is required.")
    return references


def _parse_gltf_resource_array(reader, *, collect_references):
    reader.expect("[")
    references = []
    if reader.consume("]"):
        return references
    while True:
        reader.expect("{")
        uri = None
        if not reader.consume("}"):
            while True:
                key = reader.read_string(capture=True)
                reader.expect(":")
                if key == "uri":
                    if not reader.consume("null"):
                        uri = reader.read_string(capture=True, max_capture=4096)
                else:
                    reader.skip_value()
                if reader.consume("}"):
                    break
                reader.expect(",")
                _ensure(reader.peek() != "}", "GLTF resource object has a trailing comma.")
        if collect_references and uri is not None:
            references.append(uri)
        if reader.consume("]"):
            break
        reader.expect(",")
        _ensure(reader.peek() != "]", "GLTF resource array has a trailing comma.")
    return references


def get_gltf_external_resource_uris(uploaded_file, *, size_bytes):
    reader = _JsonReader(uploaded_file=uploaded_file, size_bytes=size_bytes)
    try:
        references = _parse_gltf_object(reader, collect_references=True)
        reader.ensure_eof()
    except _JsonParseError as exc:
        raise FileValidationError("Malformed GLTF content.") from exc
    return references


def _iter_text_lines(uploaded_file, *, size_bytes, content=None, start_offset=0):
    decoder = __import__("codecs").getincrementaldecoder("utf-8")()
    pending = ""
    try:
        for chunk in _iter_content_chunks(
            uploaded_file,
            size_bytes=size_bytes,
            content=content,
            start_offset=start_offset,
        ):
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                yield line.rstrip("\r")
        pending += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise FileValidationError("Text-based file must be UTF-8 encoded.") from exc
    if pending:
        yield pending


def _validate_ascii_ply_stream(*, uploaded_file, size_bytes, body_offset, elements):
    current_position = uploaded_file.tell()
    try:
        uploaded_file.seek(body_offset)
        rows = _iter_text_lines(uploaded_file, size_bytes=size_bytes, start_offset=body_offset)
        row_index = 0
        for element in elements:
            for _ in range(element["count"]):
                try:
                    line = next(rows)
                except StopIteration as exc:
                    raise FileValidationError("PLY body is truncated.") from exc
                tokens = line.split()
                token_index = 0
                for property_spec in element["properties"]:
                    if property_spec[0] == "scalar":
                        _ensure(token_index < len(tokens), "PLY body is truncated.")
                        _validate_ply_scalar_token(tokens[token_index], property_spec[1])
                        token_index += 1
                    else:
                        _ensure(token_index < len(tokens), "PLY list property is truncated.")
                        try:
                            list_count = int(tokens[token_index])
                        except ValueError as exc:
                            raise FileValidationError("PLY list count is invalid.") from exc
                        _ensure(0 <= list_count <= 10_000_000, "PLY list count exceeds the validation resource limit.")
                        _ensure(token_index + 1 + list_count <= len(tokens), "PLY list property is truncated.")
                        for value in tokens[token_index + 1:token_index + 1 + list_count]:
                            _validate_ply_scalar_token(value, property_spec[2])
                        token_index += 1 + list_count
                _ensure(token_index == len(tokens), "PLY row contains unexpected values.")
                row_index += 1
        for line in rows:
            _ensure(not line.strip(), "PLY body contains trailing data.")
    finally:
        uploaded_file.seek(current_position)


class _BinaryReader:
    def __init__(self, *, uploaded_file, size_bytes, content=None):
        self._chunks = iter(_iter_content_chunks(uploaded_file, size_bytes=size_bytes, content=content))
        self._buffer = b""
        self._eof = False

    def read(self, size):
        while len(self._buffer) < size and not self._eof:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                self._eof = True
        if len(self._buffer) < size:
            raise FileValidationError("Binary image content is truncated.")
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result

    def read_byte(self):
        return self.read(1)[0]

    def at_end(self):
        if self._buffer:
            return False
        if self._eof:
            return True
        try:
            self._buffer = next(self._chunks)
        except StopIteration:
            self._eof = True
            return True
        return False


def _png_expected_scan_bytes(width, height, bit_depth, color_type, interlace):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]

    def pass_size(pass_width, pass_height):
        if not pass_width or not pass_height:
            return 0
        row_bytes = (pass_width * channels * bit_depth + 7) // 8
        return (row_bytes + 1) * pass_height

    if not interlace:
        return pass_size(width, height)
    passes = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    total = 0
    for x_start, y_start, x_step, y_step in passes:
        pass_width = max(0, (width - x_start + x_step - 1) // x_step)
        pass_height = max(0, (height - y_start + y_step - 1) // y_step)
        total += pass_size(pass_width, pass_height)
    return total


def _validate_png(content=None, *, uploaded_file=None, size_bytes=None):
    reader = _BinaryReader(uploaded_file=uploaded_file, size_bytes=size_bytes, content=content)
    _ensure(reader.read(len(_PNG_SIGNATURE)) == _PNG_SIGNATURE, "Invalid PNG signature.")
    saw_ihdr = saw_plte = saw_idat = saw_iend = False
    dimensions = None
    decompressor = zlib.decompressobj()
    decompressed_size = 0
    expected_size = None
    filter_rows = []
    filter_row_index = 0
    filter_row_offset = 0

    def validate_filters(output):
        nonlocal filter_row_index, filter_row_offset
        for value in output:
            if filter_row_index >= len(filter_rows):
                _ensure(False, "PNG image data exceeds its declared dimensions.")
            row_bytes, rows = filter_rows[filter_row_index]
            if filter_row_offset == 0:
                _ensure(value <= 4, "PNG scanline filter is invalid.")
            filter_row_offset += 1
            if filter_row_offset == row_bytes + 1:
                if rows > 1:
                    filter_rows[filter_row_index] = (row_bytes, rows - 1)
                else:
                    filter_row_index += 1
                filter_row_offset = 0

    def feed_idat(data):
        nonlocal decompressed_size
        pending = data
        while pending:
            try:
                output = decompressor.decompress(pending, VALIDATION_READ_CHUNK_BYTES)
            except zlib.error as exc:
                raise FileValidationError("PNG image data is invalid.") from exc
            validate_filters(output)
            decompressed_size += len(output)
            _ensure(expected_size is None or decompressed_size <= expected_size, "PNG image data exceeds its declared dimensions.")
            pending = decompressor.unconsumed_tail
            _ensure(not decompressor.unused_data, "PNG image data contains trailing compressed data.")

    while not reader.at_end():
        length = struct.unpack(">I", reader.read(4))[0]
        chunk_type = reader.read(4)
        checksum = zlib.crc32(chunk_type)
        chunk_data = bytearray()
        if chunk_type == b"IHDR":
            _ensure(not saw_ihdr and length == 13, "PNG IHDR is invalid.")
            chunk_data.extend(reader.read(length))
            width, height = struct.unpack(">II", chunk_data[:8])
            bit_depth, color_type, compression, filter_method, interlace = chunk_data[8:13]
            _ensure(width > 0 and height > 0, "PNG dimensions are invalid.")
            _ensure(bit_depth in {1, 2, 4, 8, 16}, "PNG bit depth is invalid.")
            _ensure(color_type in {0, 2, 3, 4, 6}, "PNG color type is invalid.")
            _ensure(compression == 0 and filter_method == 0 and interlace in {0, 1}, "PNG encoding is invalid.")
            dimensions = (width, height, bit_depth, color_type, interlace)
            expected_size = _png_expected_scan_bytes(*dimensions)
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
            pass_specs = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
            for x_start, y_start, x_step, y_step in (pass_specs if interlace else ((0, 0, 1, 1),)):
                pass_width = width if not interlace else max(0, (width - x_start + x_step - 1) // x_step)
                pass_height = height if not interlace else max(0, (height - y_start + y_step - 1) // y_step)
                if pass_width and pass_height:
                    filter_rows.append(((pass_width * channels * bit_depth + 7) // 8, pass_height))
            saw_ihdr = True
            checksum = zlib.crc32(chunk_data, checksum)
        elif chunk_type == b"PLTE":
            _ensure(saw_ihdr and not saw_plte and not saw_idat and not saw_iend, "PNG palette is out of order.")
            _ensure(3 <= length <= 768 and length % 3 == 0, "PNG palette is invalid.")
            palette = reader.read(length)
            entries = length // 3
            _ensure(dimensions is not None, "PNG palette is out of order.")
            _ensure(dimensions[3] != 3 or entries <= (1 << dimensions[2]), "PNG palette is invalid.")
            checksum = zlib.crc32(palette, checksum)
            saw_plte = True
        elif chunk_type == b"IDAT":
            _ensure(saw_ihdr and not saw_iend, "PNG IDAT is out of order.")
            _ensure(dimensions is not None and (dimensions[3] != 3 or saw_plte), "PNG palette is required.")
            saw_idat = True
            remaining = length
            while remaining:
                part = reader.read(min(VALIDATION_READ_CHUNK_BYTES, remaining))
                checksum = zlib.crc32(part, checksum)
                feed_idat(part)
                remaining -= len(part)
        else:
            remaining = length
            while remaining:
                part = reader.read(min(VALIDATION_READ_CHUNK_BYTES, remaining))
                checksum = zlib.crc32(part, checksum)
                if len(chunk_data) < 13:
                    chunk_data.extend(part[: 13 - len(chunk_data)])
                remaining -= len(part)
            if chunk_type == b"IEND":
                _ensure(length == 0, "PNG IEND is invalid.")
                saw_iend = True
        expected_crc = struct.unpack(">I", reader.read(4))[0]
        _ensure(checksum & 0xffffffff == expected_crc, "PNG chunk checksum is invalid.")
        if saw_iend:
            _ensure(reader.at_end(), "PNG image data is incomplete.")
            break

    _ensure(saw_ihdr and saw_idat and saw_iend and decompressor.eof, "PNG image data is incomplete.")
    _ensure(dimensions is not None and (dimensions[3] != 3 or saw_plte), "PNG palette is required.")
    _ensure(decompressed_size == expected_size, "PNG image data is incomplete.")
    _ensure(filter_row_index == len(filter_rows) and filter_row_offset == 0, "PNG image data is incomplete.")


def _validate_jpeg(content=None, *, uploaded_file=None, size_bytes=None):
    reader = _BinaryReader(uploaded_file=uploaded_file, size_bytes=size_bytes, content=content)
    _ensure(reader.read(2) == b"\xff\xd8", "Invalid JPEG signature.")
    saw_frame = saw_scan = saw_scan_data = False
    frame_component_ids = set()
    frame_quantization_tables = set()
    quantization_tables = set()
    huffman_tables = set()
    dimensions = None
    progressive_frame = False
    pending_marker = None
    while True:
        if pending_marker is None:
            _ensure(reader.read_byte() == 0xFF, "JPEG marker is invalid.")
            marker = reader.read_byte()
            while marker == 0xFF:
                marker = reader.read_byte()
        else:
            marker, pending_marker = pending_marker, None
        _ensure(marker != 0x00, "JPEG marker is invalid.")
        if marker == 0xD9:
            _ensure(saw_frame and saw_scan and saw_scan_data and reader.at_end(), "JPEG image data is incomplete.")
            _validate_jpeg_with_decoder(
                content=content,
                uploaded_file=uploaded_file,
                size_bytes=size_bytes,
                dimensions=dimensions,
            )
            return
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            _ensure(marker != 0xD8, "JPEG restart marker is invalid.")
            continue
        segment_length = struct.unpack(">H", reader.read(2))[0]
        _ensure(segment_length >= 2, "JPEG segment is invalid.")
        segment = reader.read(segment_length - 2)
        if marker == 0xDB:
            _parse_jpeg_quantization_tables(segment, quantization_tables)
        elif marker == 0xC4:
            _parse_jpeg_huffman_tables(segment, huffman_tables)
        elif 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB or 0xCD <= marker <= 0xCF:
            _ensure(len(segment) >= 6, "JPEG frame segment is invalid.")
            precision, height, width, component_count = struct.unpack(">BHHB", segment[:6])
            _ensure(precision in {8, 12}, "JPEG frame precision is invalid.")
            _ensure(width and height and 1 <= component_count <= 4, "JPEG frame dimensions are invalid.")
            _ensure(len(segment) == 6 + 3 * component_count, "JPEG frame segment is invalid.")
            component_ids = [segment[index] for index in range(6, len(segment), 3)]
            _ensure(len(set(component_ids)) == component_count, "JPEG frame components are invalid.")
            for index in range(6, len(segment), 3):
                sampling = segment[index + 1]
                quantization_table = segment[index + 2]
                _ensure(1 <= (sampling >> 4) <= 4 and 1 <= (sampling & 0x0F) <= 4, "JPEG frame sampling is invalid.")
                _ensure(0 <= quantization_table <= 3, "JPEG quantization table is invalid.")
            dimensions = (width, height)
            progressive_frame = marker in {0xC2, 0xC6, 0xCA, 0xCE}
            frame_component_ids.update(component_ids)
            frame_quantization_tables = {
                segment[index + 2] for index in range(6, len(segment), 3)
            }
            saw_frame = True
        if marker == 0xDA:
            _ensure(saw_frame, "JPEG scan appears before a frame.")
            _ensure(frame_quantization_tables.issubset(quantization_tables), "JPEG quantization table is missing.")
            _ensure(len(segment) >= 4, "JPEG scan segment is invalid.")
            scan_component_count = segment[0]
            _ensure(1 <= scan_component_count <= 4, "JPEG scan segment is invalid.")
            _ensure(len(segment) == 4 + 2 * scan_component_count, "JPEG scan segment is invalid.")
            scan_component_ids = [segment[index] for index in range(1, 1 + 2 * scan_component_count, 2)]
            _ensure(set(scan_component_ids).issubset(frame_component_ids), "JPEG scan components are invalid.")
            spectral_start, spectral_end, successive = segment[-3:]
            _ensure(spectral_start <= spectral_end <= 63, "JPEG scan spectral selection is invalid.")
            _ensure(successive >> 4 <= 13 and (successive & 0x0F) <= 13, "JPEG scan successive approximation is invalid.")
            uses_dc_table = not progressive_frame or spectral_start == 0
            uses_ac_table = not progressive_frame or spectral_start > 0
            for index in range(2, 2 + 2 * scan_component_count, 2):
                table_selectors = segment[index]
                dc_table = table_selectors >> 4
                ac_table = table_selectors & 0x0F
                _ensure(
                    (not uses_dc_table or (0, dc_table) in huffman_tables)
                    and (not uses_ac_table or (1, ac_table) in huffman_tables)
                    and dc_table <= 3
                    and ac_table <= 3,
                    "JPEG Huffman table is missing.",
                )
            marker, scan_has_data = _read_jpeg_scan(reader)
            saw_scan_data |= scan_has_data
            saw_scan = True
            pending_marker = marker


def _parse_jpeg_quantization_tables(segment, tables):
    offset = 0
    while offset < len(segment):
        table_info = segment[offset]
        offset += 1
        precision = table_info >> 4
        table_id = table_info & 0x0F
        _ensure(precision in {0, 1} and table_id <= 3, "JPEG quantization table is invalid.")
        value_size = 2 if precision else 1
        table_size = 64 * value_size
        _ensure(offset + table_size <= len(segment), "JPEG quantization table is truncated.")
        offset += table_size
        tables.add(table_id)
    _ensure(offset == len(segment), "JPEG quantization table is invalid.")


def _parse_jpeg_huffman_tables(segment, tables):
    offset = 0
    while offset < len(segment):
        table_info = segment[offset]
        offset += 1
        table_class = table_info >> 4
        table_id = table_info & 0x0F
        _ensure(table_class in {0, 1} and table_id <= 3, "JPEG Huffman table is invalid.")
        _ensure(offset + 16 <= len(segment), "JPEG Huffman table is truncated.")
        symbol_count = sum(segment[offset : offset + 16])
        offset += 16
        _ensure(symbol_count > 0 and symbol_count <= 256 and offset + symbol_count <= len(segment), "JPEG Huffman table is invalid.")
        offset += symbol_count
        tables.add((table_class, table_id))
    _ensure(offset == len(segment), "JPEG Huffman table is invalid.")


def _validate_jpeg_with_decoder(content=None, *, uploaded_file=None, size_bytes=None, dimensions=None):
    if size_bytes is None:
        size_bytes = len(content or b"")
    if size_bytes > MAX_JPEG_DECODE_BYTES or not dimensions:
        return
    width, height = dimensions
    if width * height > MAX_JPEG_DECODE_PIXELS:
        return

    try:
        import rasterio
        from rasterio.io import MemoryFile
    except ImportError:
        return

    image_bytes = b"".join(
        _iter_content_chunks(uploaded_file, size_bytes=size_bytes, content=content)
    )
    try:
        with rasterio.Env(GDAL_ERROR_ON_LIBJPEG_WARNING="TRUE"):
            with MemoryFile(image_bytes) as memory:
                with memory.open() as dataset:
                    dataset.read()
    except Exception as exc:
        raise FileValidationError("JPEG image data is invalid.") from exc


def _read_jpeg_scan(reader):
    saw_data = False
    while True:
        value = reader.read_byte()
        if value != 0xFF:
            saw_data = True
            continue
        marker = reader.read_byte()
        while marker == 0xFF:
            marker = reader.read_byte()
        if marker == 0x00:
            saw_data = True
            continue
        if 0xD0 <= marker <= 0xD7:
            continue
        return marker, saw_data

def _ensure(condition, message):
    if not condition:
        raise FileValidationError(message)
