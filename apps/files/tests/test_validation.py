from .support import *


class FileValidationTests(TestCase):
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
            ("preview.png", b"\x89PNG\r\n\x1a\nrest", "image/png", FileType.TWO_D, FileFormat.PNG),
            ("photo.jpg", b"\xff\xd8\xff\xe0rest", "image/jpeg", FileType.TWO_D, FileFormat.JPEG),
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
            ("map.png", b"\x89PNG\r\n\x1a\nrest", "application/octet-stream", FileType.TWO_D, FileFormat.PNG),
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
            validate_upload(self.make_upload("photo.jpg", b"\xff\xd8\xff\xe0rest", "text/plain"))

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("scene.glb", b"not-a-glb", "application/octet-stream"))

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("outline.json", b'{"hello":"world"}', "application/octet-stream"))

    def test_conflicting_specific_mime_type_remains_rejected(self):
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("map.png", b"\x89PNG\r\n\x1a\nrest", "image/jpeg"))

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
            self.make_upload("../payload.png", b"\x89PNG\r\n\x1a\nrest", "image/png"),
            self.make_upload(r"..\\payload.png", b"\x89PNG\r\n\x1a\nrest", "image/png"),
            self.make_upload("folder/payload.png", b"\x89PNG\r\n\x1a\nrest", "image/png"),
        ]

        for upload in uploads:
            with self.subTest(name=upload.name):
                with self.assertRaises(FileValidationError):
                    validate_upload(upload)

    @override_settings(MAX_FILE_SIZE_BYTES=4)
    def test_oversize_upload_is_rejected(self):
        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("preview.png", b"\x89PNG\r\n\x1a\nrest", "image/png"))

    def test_validation_reads_only_bounded_prefix(self):
        large_png = TrackingUpload(
            name="preview.png",
            content=b"\x89PNG\r\n\x1a\n" + (b"x" * (MAX_VALIDATION_BYTES + 1024)),
            content_type="image/png",
            fail_above=MAX_VALIDATION_BYTES,
        )

        result = validate_upload(large_png)

        self.assertEqual(result.file_format, FileFormat.PNG)
        self.assertLessEqual(large_png.max_requested_read, MAX_VALIDATION_BYTES)

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
