import json
import gc
import tracemalloc

from django.test import SimpleTestCase

from .support import *


class FileValidationTests(SimpleTestCase):
    def make_upload(self, name, content, content_type):
        return TrackingUpload(name=name, content=content, content_type=content_type)

    def make_classic_tiff(self, *, endian, first_ifd_offset, entry_tags):
        tag_pack = ">" if endian == "MM" else "<"
        header = (b"MM\x00*" if endian == "MM" else b"II*\x00") + struct.pack(
            f"{tag_pack}I",
            first_ifd_offset,
        )
        entries = b"".join(
            struct.pack(f"{tag_pack}HHII", tag, 3, 1, 1)
            for tag in entry_tags
        )
        ifd = (
            struct.pack(f"{tag_pack}H", len(entry_tags))
            + entries
            + struct.pack(f"{tag_pack}I", 0)
        )
        return header + (b"\x00" * (first_ifd_offset - len(header))) + ifd

    def test_every_allowed_format_family_is_accepted(self):
        cases = [
            ("ortho-geotiff.tif", b"II*\x00\x08\x00\x00\x00\x01\x00\xAF\x87\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00", "image/tiff", FileType.TWO_D, FileFormat.GEOTIFF),
            ("image.tiff", b"II*\x00\x08\x00\x00\x00\x00\x00", "image/tiff", FileType.TWO_D, FileFormat.TIFF),
            ("preview.png", valid_png_bytes(), "image/png", FileType.TWO_D, FileFormat.PNG),
            ("photo.jpg", valid_jpeg_bytes(), "image/jpeg", FileType.TWO_D, FileFormat.JPEG),
            ("area.kml", b'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>', "application/vnd.google-earth.kml+xml", FileType.TWO_D, FileFormat.KML),
            ("outline.geojson", b'{"type":"FeatureCollection","features":[]}', "application/geo+json", FileType.TWO_D, FileFormat.GEOJSON),
            ("outline.json", b'{"type":"FeatureCollection","features":[]}', "application/json", FileType.TWO_D, FileFormat.GEOJSON),
            ("outline.geo.json", b'{"type":"FeatureCollection","features":[]}', "application/json", FileType.TWO_D, FileFormat.GEOJSON),
            ("mesh.obj", b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n", "model/obj", FileType.THREE_D, FileFormat.OBJ),
            ("scene.glb", b"glTF\x02\x00\x00\x00rest", "model/gltf-binary", FileType.THREE_D, FileFormat.GLB),
            ("scene.gltf", b'{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}]}', "model/gltf+json", FileType.THREE_D, FileFormat.GLTF),
            ("cloud.las", b"LASF\x00\x00\x00\x00", "application/vnd.las", FileType.THREE_D, FileFormat.LAS),
            ("cloud.laz", b"LASF\x00\x00\x00\x00", "application/vnd.laszip", FileType.THREE_D, FileFormat.LAZ),
            ("mesh.ply", b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n0\n", "application/ply", FileType.THREE_D, FileFormat.PLY),
            ("shape.stl", b"solid cube\nfacet normal 0 0 0\nouter loop\nendloop\nendfacet\nendsolid cube\n", "model/stl", FileType.THREE_D, FileFormat.STL),
        ]

        for name, content, content_type, expected_type, expected_format in cases:
            with self.subTest(name=name):
                result = validate_upload(self.make_upload(name, content, content_type))
                self.assertEqual(result.file_type, expected_type)
                self.assertEqual(result.file_format, expected_format)
                self.assertEqual(result.mime_type, content_type)

    def test_extension_and_signature_mismatch_is_rejected(self):
        upload = self.make_upload("pretend.png", b"\xff\xd8\xff\xe0rest", "image/png")

        with self.assertRaises(FileValidationError):
            validate_upload(upload)

    def test_mime_mismatch_is_rejected(self):
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("photo.jpg", b"\xff\xd8\xff\xe0rest", "image/png"))

    def test_browser_fallback_mime_is_accepted_only_after_strict_content_validation(self):
        cases = [
            ("map.png", valid_png_bytes(), "application/octet-stream", FileType.TWO_D, FileFormat.PNG),
            ("area.kml", b'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>', "application/octet-stream", FileType.TWO_D, FileFormat.KML),
            ("outline.geojson", b'{"type":"FeatureCollection","features":[]}', "application/octet-stream", FileType.TWO_D, FileFormat.GEOJSON),
            ("outline.json", b'{"type":"FeatureCollection","features":[]}', "text/plain", FileType.TWO_D, FileFormat.GEOJSON),
            ("mesh.obj", b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n", FileFormat.OBJ),
            ("scene.glb", b"glTF\x02\x00\x00\x00rest", FileFormat.GLB),
            ("scene.gltf", b'{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}]}', FileFormat.GLTF),
            ("cloud.las", b"LASF\x00\x00\x00\x00", FileFormat.LAS),
            ("cloud.laz", b"LASF\x00\x00\x00\x00", FileFormat.LAZ),
            ("mesh.ply", b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n0\n", FileFormat.PLY),
            ("shape.stl", b"solid cube\nfacet normal 0 0 0\nouter loop\nendloop\nendfacet\nendsolid cube\n", FileFormat.STL),
        ]
        for case in cases:
            if len(case) == 5:
                name, content, content_type, expected_type, expected_format = case
            else:
                name, content, expected_format = case
                content_type = "application/octet-stream"
                expected_type = FileType.THREE_D
            with self.subTest(name=name):
                result = validate_upload(self.make_upload(name, content, content_type))
                self.assertEqual(result.file_type, expected_type)
                self.assertEqual(result.file_format, expected_format)

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("photo.jpg", valid_jpeg_bytes(), "text/plain"))

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("scene.glb", b"not-a-glb", "application/octet-stream"))

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("outline.json", b'{"hello":"world"}', "application/octet-stream"))

    def test_conflicting_specific_mime_type_remains_rejected(self):
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("map.png", valid_png_bytes(), "image/jpeg"))

    def test_obj_mtl_asset_accepts_browser_generic_mime_only_for_valid_mtl_content(self):
        valid_asset = self.make_upload(
            "materials.mtl",
            b"newmtl roof\nmap_Kd 2222.jpg\n",
            "application/octet-stream",
        )

        result = validate_obj_asset_upload(valid_asset)

        self.assertEqual(result.original_filename, "materials.mtl")
        self.assertEqual(result.sanitized_filename, "materials.mtl")
        self.assertEqual(result.mime_type, "application/octet-stream")

    def test_obj_mtl_asset_with_browser_generic_mime_still_rejects_malformed_content(self):
        invalid_asset = self.make_upload(
            "materials.mtl",
            b"not valid mtl content",
            "application/octet-stream",
        )

        with self.assertRaises(FileValidationError):
            validate_obj_asset_upload(invalid_asset)

    def test_malformed_textual_formats_are_rejected(self):
        cases = [
            self.make_upload("bad.kml", b"<xml>", "application/vnd.google-earth.kml+xml"),
            self.make_upload("bad.geojson", b'{"features":[]}', "application/geo+json"),
            self.make_upload("bad.obj", b"not obj data", "model/obj"),
            self.make_upload("bad.gltf", b'{"scene":0}', "model/gltf+json"),
            self.make_upload("bad.stl", b"solid cube\nendsolid cube\n", "model/stl"),
        ]

        for upload in cases:
            with self.subTest(name=upload.name):
                with self.assertRaises(FileValidationError):
                    validate_upload(upload)

    def test_unsupported_extensions_and_traversal_filenames_are_rejected(self):
        uploads = [
            self.make_upload("payload.exe", b"MZ", "application/octet-stream"),
            self.make_upload("../payload.png", valid_png_bytes(), "image/png"),
            self.make_upload(r"..\\payload.png", valid_png_bytes(), "image/png"),
            self.make_upload("folder/payload.png", valid_png_bytes(), "image/png"),
        ]

        for upload in uploads:
            with self.subTest(name=upload.name):
                with self.assertRaises(FileValidationError):
                    validate_upload(upload)

    @override_settings(MAX_FILE_SIZE_BYTES=4)
    def test_oversize_upload_is_rejected(self):
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("preview.png", valid_png_bytes() + b"12345", "image/png"))

    def test_structured_validation_accepts_valid_content_beyond_legacy_prefix_cap(self):
        content = json.dumps(
            {
                "type": "FeatureCollection",
                "features": [],
                "properties": "x" * (MAX_VALIDATION_BYTES + 1024),
            }
        ).encode()
        large_geojson = TrackingUpload(
            name="large.geojson",
            content=content,
            content_type="application/geo+json",
        )

        result = validate_upload(large_geojson)

        self.assertEqual(result.file_format, FileFormat.GEOJSON)
        complete_read_sizes = [
            size for _position, size in large_geojson.read_calls
            if size != MAX_VALIDATION_BYTES
        ]
        self.assertLessEqual(max(complete_read_sizes), VALIDATION_READ_CHUNK_BYTES)

    def test_structured_validation_accepts_below_at_and_above_legacy_prefix_boundary(self):
        for target_size in (
            MAX_VALIDATION_BYTES - 512,
            MAX_VALIDATION_BYTES,
            MAX_VALIDATION_BYTES + 512,
        ):
            with self.subTest(target_size=target_size):
                properties_size = max(0, target_size - len(b'{"type":"FeatureCollection","features":[],"properties":""}'))
                content = json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [],
                        "properties": "x" * properties_size,
                    },
                    separators=(",", ":"),
                ).encode()
                self.assertGreaterEqual(len(content), target_size - 2)
                self.assertEqual(
                    validate_upload(
                        self.make_upload("boundary.geojson", content, "application/geo+json")
                    ).file_format,
                    FileFormat.GEOJSON,
                )

    def test_structured_validation_handles_utf8_split_across_read_chunks_and_truncation(self):
        content = json.dumps(
            {
                "type": "FeatureCollection",
                "features": [],
                "properties": "x" * (VALIDATION_READ_CHUNK_BYTES - 20) + "é" * 20,
            },
            ensure_ascii=False,
        ).encode()
        self.assertEqual(
            validate_upload(self.make_upload("utf8.geojson", content, "application/geo+json")).file_format,
            FileFormat.GEOJSON,
        )

        truncated = TrackingUpload("truncated.geojson", content, "application/geo+json")
        truncated.size += 1
        with self.assertRaisesMessage(FileValidationError, "Structured file is truncated."):
            validate_upload(truncated)

    def test_structured_and_binary_content_requires_complete_validity(self):
        malformed = [
            self.make_upload("short.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            self.make_upload("point.geojson", b'{"type":"Point"}', "application/geo+json"),
            self.make_upload("not-kml.kml", b"<notkml/>", "application/vnd.google-earth.kml+xml"),
            self.make_upload("comments.obj", b"# only a comment\n", "model/obj"),
            self.make_upload("trailing.png", valid_png_bytes() + b"trailing", "image/png"),
        ]
        for upload in malformed:
            with self.subTest(name=upload.name):
                with self.assertRaises(FileValidationError):
                    validate_upload(upload)

    def test_images_require_decodable_scan_data(self):
        self.assertEqual(
            validate_upload(self.make_upload("interlaced.png", valid_png_bytes(interlaced=True), "image/png")).file_format,
            FileFormat.PNG,
        )
        self.assertEqual(
            validate_upload(self.make_upload("progressive.jpg", valid_progressive_jpeg_bytes(), "image/jpeg")).file_format,
            FileFormat.JPEG,
        )
        with self.assertRaises(FileValidationError):
            validate_upload(
                self.make_upload(
                    "empty-interlaced.png",
                    valid_png_bytes(interlaced=True, scan_data=b""),
                    "image/png",
                )
            )
        with self.assertRaises(FileValidationError):
            validate_upload(
                self.make_upload(
                    "marker-only.jpg",
                    b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xff\xd9",
                    "image/jpeg",
                )
            )

    def test_polygon_rings_are_structurally_complete(self):
        incomplete = b'{"type":"Polygon","coordinates":[[[0,0]]]} '
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("incomplete.geojson", incomplete, "application/geo+json"))

    def test_all_supported_nonempty_geometry_types_are_accepted(self):
        geometries = {
            "Point": [1, 2],
            "MultiPoint": [[1, 2], [3, 4]],
            "LineString": [[1, 2], [3, 4]],
            "MultiLineString": [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            "Polygon": [
                [[0, 0], [4, 0], [4, 4], [0, 0]],
                [[1, 1], [2, 1], [1, 2], [1, 1]],
            ],
            "MultiPolygon": [
                [[[0, 0], [4, 0], [4, 4], [0, 0]]],
                [[[10, 10], [14, 10], [14, 14], [10, 10]]],
            ],
        }

        for geometry_type, coordinates in geometries.items():
            with self.subTest(geometry_type=geometry_type):
                content = json.dumps({"type": geometry_type, "coordinates": coordinates}).encode()
                result = validate_upload(
                    self.make_upload("geometry.geojson", content, "application/geo+json")
                )
                self.assertEqual(result.file_format, FileFormat.GEOJSON)

        collections = {
            "FeatureCollection": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}}
                ],
            },
            "Feature": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[1, 2], [3, 4]]},
            },
            "GeometryCollection": {
                "type": "GeometryCollection",
                "geometries": [{"type": "Point", "coordinates": [1, 2]}],
            },
        }
        for geometry_type, payload in collections.items():
            with self.subTest(geometry_type=geometry_type):
                result = validate_upload(
                    self.make_upload("collection.geojson", json.dumps(payload).encode(), "application/geo+json")
                )
                self.assertEqual(result.file_format, FileFormat.GEOJSON)

    def test_every_sibling_line_and_ring_is_validated(self):
        malformed = {
            "multiline.geojson": {
                "type": "MultiLineString",
                "coordinates": [[[0, 0], [1, 1]], [[2, 2]]],
            },
            "polygon-hole.geojson": {
                "type": "Polygon",
                "coordinates": [
                    [[0, 0], [4, 0], [4, 4], [0, 0]],
                    [[1, 1], [2, 1], [1, 2]],
                ],
            },
            "multipolygon.geojson": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[0, 0], [4, 0], [4, 4], [0, 0]]],
                    [[[10, 10], [14, 10], [14, 14]]],
                ],
            },
        }

        for name, payload in malformed.items():
            with self.subTest(name=name), self.assertRaises(FileValidationError):
                validate_upload(self.make_upload(name, json.dumps(payload).encode(), "application/geo+json"))

    def test_geojson_coordinate_memory_does_not_scale_with_sibling_count(self):
        uploads = []
        for position_count in (2_000, 20_000):
            content = json.dumps(
                {
                    "type": "LineString",
                    "coordinates": [[index % 180, index % 90] for index in range(position_count)],
                },
                separators=(",", ":"),
            ).encode()
            uploads.append(self.make_upload("many-positions.geojson", content, "application/geo+json"))

        peaks = []
        for upload in uploads:
            gc.collect()
            tracemalloc.start()
            validate_upload(upload)
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peaks.append(peak)

        self.assertLess(peaks[1], 1_024 * 1_024)
        self.assertLess(peaks[1] - peaks[0], 512 * 1_024)

    def test_json_syntax_is_strict_for_geojson_and_gltf(self):
        invalid_uploads = [
            self.make_upload(
                "trailing.geojson",
                b'{"type":"FeatureCollection","features":[],}',
                "application/geo+json",
            ),
            self.make_upload(
                "leading-zero.geojson",
                b'{"type":"Point","coordinates":[01,2]}',
                "application/geo+json",
            ),
            self.make_upload(
                "trailing.gltf",
                b'{"asset":{"version":"2.0",}}',
                "model/gltf+json",
            ),
        ]

        for upload in invalid_uploads:
            with self.subTest(name=upload.name), self.assertRaises(FileValidationError):
                validate_upload(upload)

    def test_json_literal_split_across_read_boundary_is_accepted(self):
        prefix = b'{"type":"Feature","padding":"'
        suffix = b'","geometry":'
        content = prefix + b"a" * (VALIDATION_READ_CHUNK_BYTES - 2 - len(prefix) - len(suffix)) + suffix + b"null}"

        result = validate_upload(self.make_upload("boundary.geojson", content, "application/geo+json"))

        self.assertEqual(result.file_format, FileFormat.GEOJSON)

    def test_reproduced_malformed_png_and_jpeg_are_rejected(self):
        def png_chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        indexed_png_without_palette = (
            b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
            + png_chunk(b"IEND", b"")
        )
        malformed_jpeg = bytes.fromhex(
            "ffd8ffc00008080001000101ffda0008010100003f0001ffd9"
        )
        missing_quantization_table = bytes.fromhex(
            "ffd8ffc0000b080001000101011100ffda0008010100003f0001ffd9"
        )
        valid_jpeg = valid_progressive_jpeg_bytes()
        scan_marker = valid_jpeg.index(b"\xff\xda")
        scan_start = scan_marker + 2 + struct.unpack(">H", valid_jpeg[scan_marker + 2 : scan_marker + 4])[0]
        malformed_scan = valid_jpeg[:scan_start] + b"\xff\xd9"

        for name, content in (
            ("missing-palette.png", indexed_png_without_palette),
            ("malformed-components.jpg", malformed_jpeg),
            ("missing-quantization-table.jpg", missing_quantization_table),
            ("malformed-scan.jpg", malformed_scan),
        ):
            with self.subTest(name=name), self.assertRaises(FileValidationError):
                validate_upload(self.make_upload(name, content, "image/png" if name.endswith("png") else "image/jpeg"))

    def test_kml_dtd_and_entity_declarations_are_rejected_across_chunks(self):
        declaration = b'<!DOCTYPE kml [<!ENTITY x "expanded">]>'
        xml_prefix = b'<?xml version="1.0"?>'
        cases = [
            xml_prefix + declaration + b'<kml><name>&x;</name></kml>',
            xml_prefix
            + b" " * (VALIDATION_READ_CHUNK_BYTES - len(xml_prefix) - 2)
            + declaration[:2]
            + declaration[2:]
            + b'<kml><name>&x;</name></kml>',
        ]

        for content in cases:
            with self.assertRaises(FileValidationError):
                validate_upload(self.make_upload("entities.kml", content, "application/vnd.google-earth.kml+xml"))

    def test_positive_image_fixtures_independently_decode_strictly(self):
        import rasterio
        from rasterio.io import MemoryFile

        fixtures = {
            "png": valid_png_bytes(),
            "interlaced-png": valid_png_bytes(interlaced=True),
            "jpeg": valid_jpeg_bytes(),
            "progressive-jpeg": valid_progressive_jpeg_bytes(),
        }
        for name, content in fixtures.items():
            with self.subTest(name=name):
                with rasterio.Env(GDAL_ERROR_ON_LIBJPEG_WARNING="TRUE"):
                    with MemoryFile(content) as memory:
                        with memory.open() as dataset:
                            dataset.read()

    def test_ascii_and_binary_ply_bodies_are_checked(self):
        ascii_ply = (
            b"ply\nformat ascii 1.0\nelement vertex 1\n"
            b"property float x\nproperty float y\nend_header\n1.0 2.0\n"
        )
        binary_header = (
            b"ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
            b"property float x\nproperty float y\nend_header\n"
        )
        binary_ply = binary_header + struct.pack("<ff", 1.0, 2.0)

        self.assertEqual(
            validate_upload(self.make_upload("points.ply", ascii_ply, "application/ply")).file_format,
            FileFormat.PLY,
        )
        self.assertEqual(
            validate_upload(self.make_upload("points-binary.ply", binary_ply, "application/ply")).file_format,
            FileFormat.PLY,
        )
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("truncated.ply", binary_header + b"\x00", "application/ply"))

    def test_tiff_with_distant_first_ifd_is_accepted_with_bounded_offset_reads(self):
        first_ifd_offset = MAX_VALIDATION_BYTES + 8192
        entry_tags = [256, 257, 258, 259, 262, 273, 277, 278, 279, 282, 283, 296, 305, 306, 320, 338, 339, 34735, 42112]
        upload = TrackingUpload(
            name="o41078a5.tif",
            content=self.make_classic_tiff(
                endian="MM",
                first_ifd_offset=first_ifd_offset,
                entry_tags=entry_tags,
            ),
            content_type="image/tiff",
            fail_above=MAX_VALIDATION_BYTES,
        )
        upload.seek(123)

        result = validate_upload(upload)

        self.assertEqual(result.file_format, FileFormat.GEOTIFF)
        self.assertEqual(result.file_type, FileType.TWO_D)
        self.assertEqual(upload.tell(), 123)
        self.assertEqual(
            upload.read_calls,
            [
                (0, MAX_VALIDATION_BYTES),
                (first_ifd_offset, 2),
                (first_ifd_offset + 2, len(entry_tags) * 12),
            ],
        )
        self.assertLessEqual(upload.max_requested_read, MAX_VALIDATION_BYTES)

    def test_tiff_rejects_oversized_declared_ifd_directory_before_oversized_read(self):
        first_ifd_offset = MAX_VALIDATION_BYTES + 4096
        oversized_entry_count = (MAX_VALIDATION_BYTES // 12) + 1
        upload = TrackingUpload(
            name="oversized-ifd.tif",
            content=(
                b"MM\x00*"
                + struct.pack(">I", first_ifd_offset)
                + (b"\x00" * (first_ifd_offset - 8))
                + struct.pack(">H", oversized_entry_count)
            ),
            content_type="image/tiff",
            fail_above=MAX_VALIDATION_BYTES,
        )
        upload.seek(77)

        with self.assertRaisesMessage(
            FileValidationError,
            "TIFF IFD directory exceeds validation read limit.",
        ):
            validate_upload(upload)

        self.assertEqual(upload.tell(), 77)
        self.assertEqual(
            upload.read_calls,
            [
                (0, MAX_VALIDATION_BYTES),
                (first_ifd_offset, 2),
            ],
        )
        self.assertLessEqual(upload.max_requested_read, MAX_VALIDATION_BYTES)

    def test_storage_filename_sanitization_is_explicit_and_small(self):
        self.assertEqual(
            sanitize_storage_filename("Quarterly Survey ../North Block (Final).GLB"),
            "North-Block-Final.glb",
        )
