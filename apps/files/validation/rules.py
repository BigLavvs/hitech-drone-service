import re

from apps.files.models import FileFormat, FileType


MAX_VALIDATION_BYTES = 262144
VALIDATION_READ_CHUNK_BYTES = 64 * 1024
# JPEG validation only hands small, bounded images to the maintained decoder.
# Larger uploads still receive the streaming structural validation below.
MAX_JPEG_DECODE_BYTES = 16 * 1024 * 1024
MAX_JPEG_DECODE_PIXELS = 25_000_000

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
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.TIFF, FileFormat.GEOTIFF},
    },
    ".tiff": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/tiff", "image/geotiff", "image/x-geotiff"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.TIFF, FileFormat.GEOTIFF},
    },
    ".png": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/png"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.PNG},
    },
    ".jpg": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/jpeg"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.JPEG},
    },
    ".jpeg": {
        "file_type": FileType.TWO_D,
        "mime_types": {"image/jpeg"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.JPEG},
    },
    ".kml": {
        "file_type": FileType.TWO_D,
        "mime_types": {"application/vnd.google-earth.kml+xml"},
        "fallback_mime_types": {"application/octet-stream", "text/plain"},
        "formats": {FileFormat.KML},
    },
    ".geojson": {
        "file_type": FileType.TWO_D,
        "mime_types": {"application/geo+json", "application/json"},
        "fallback_mime_types": {"application/octet-stream", "text/plain"},
        "formats": {FileFormat.GEOJSON},
    },
    ".json": {
        "file_type": FileType.TWO_D,
        "mime_types": {"application/geo+json", "application/json"},
        "fallback_mime_types": {"application/octet-stream", "text/plain"},
        "formats": {FileFormat.GEOJSON},
    },
    ".geo.json": {
        "file_type": FileType.TWO_D,
        "mime_types": {"application/geo+json", "application/json"},
        "fallback_mime_types": {"application/octet-stream", "text/plain"},
        "formats": {FileFormat.GEOJSON},
    },
    ".obj": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/obj", "text/plain"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.OBJ},
    },
    ".glb": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/gltf-binary", "application/octet-stream+gltf"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.GLB},
    },
    ".gltf": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/gltf+json", "application/gltf+json"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.GLTF},
    },
    ".las": {
        "file_type": FileType.THREE_D,
        "mime_types": {"application/vnd.las", "application/x-las"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.LAS},
    },
    ".laz": {
        "file_type": FileType.THREE_D,
        "mime_types": {"application/vnd.laszip", "application/x-laz"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.LAZ},
    },
    ".ply": {
        "file_type": FileType.THREE_D,
        "mime_types": {"application/ply", "model/ply"},
        "fallback_mime_types": {"application/octet-stream"},
        "formats": {FileFormat.PLY},
    },
    ".stl": {
        "file_type": FileType.THREE_D,
        "mime_types": {"model/stl", "application/sla"},
        "fallback_mime_types": {"application/octet-stream"},
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
