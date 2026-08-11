import hashlib
from datetime import datetime, timedelta, timezone
from datetime import date
from io import BytesIO
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileFormat, FileType, SurveyFile, SurveyFileAsset, UploadSession
from apps.files.services import admit_uploaded_file, get_survey_file_download_for_user
from apps.files.storage import PrivateR2StorageAdapter
from apps.files.validation import (
    MAX_VALIDATION_BYTES,
    FileValidationError,
    sanitize_storage_filename,
    validate_upload,
)
from apps.processing.models import ProcessingJob
from apps.projects.models import Project, ProjectMembership, Site
from apps.surveys.models import Survey, SurveyStatus


FAKE_R2_SETTINGS = {
    "R2_ENDPOINT_URL": "https://example.invalid",
    "R2_ACCESS_KEY_ID": "test-access-key",
    "R2_SECRET_ACCESS_KEY": "test-secret-key",
    "R2_BUCKET_NAME": "test-bucket",
    "R2_PUBLIC_URL": "",
    "UPLOAD_CHUNK_SIZE_BYTES": 8 * 1024 * 1024,
}


class TrackingUpload(BytesIO):
    def __init__(self, name, content, content_type, fail_above=None):
        super().__init__(content)
        self.name = name
        self.content_type = content_type
        self.size = len(content)
        self.fail_above = fail_above
        self.max_requested_read = 0

    def read(self, size=-1):
        if size > self.max_requested_read:
            self.max_requested_read = size
        if self.fail_above is not None and size > self.fail_above:
            raise AssertionError(f"Attempted to read beyond bounded prefix: {size}")
        return super().read(size)


class FakePrivateStorageAdapter:
    def __init__(self):
        self.objects = {}
        self.uploaded = []
        self.promoted = []
        self.deleted = []

    def build_staging_key(self, survey_id, filename, identifier=None):
        suffix = identifier or "stage"
        sanitized_filename = sanitize_storage_filename(filename)
        return f"surveys/{survey_id}/staging/{suffix}_{sanitized_filename}"

    def build_canonical_key(self, survey_id, file_id, extension):
        normalized_extension = extension.lower().lstrip(".")
        return f"surveys/{survey_id}/files/{file_id}/raw.{normalized_extension}"

    def upload_to_staging(self, *, survey_id, filename, file_obj, content_type, identifier=None):
        file_obj.seek(0)
        digest = hashlib.sha256()
        content = bytearray()

        while True:
            chunk = file_obj.read(4)
            if not chunk:
                break
            digest.update(chunk)
            content.extend(chunk)

        storage_key = self.build_staging_key(
            survey_id=survey_id,
            filename=filename,
            identifier=identifier,
        )
        self.objects[storage_key] = {
            "content": bytes(content),
            "content_type": content_type,
        }
        self.uploaded.append(storage_key)
        return type(
            "StagedUpload",
            (),
            {
                "storage_key": storage_key,
                "sha256_checksum": digest.hexdigest(),
            },
        )()

    def promote_object(self, *, source_key, destination_key, content_type):
        source_object = self.objects[source_key]
        self.objects[destination_key] = {
            "content": source_object["content"],
            "content_type": content_type,
        }
        self.promoted.append((source_key, destination_key))
        self.delete_object(source_key)

    def delete_object(self, storage_key):
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)


class FileModelsTestCase(TestCase):
    def setUp(self):
        self.uploader = User.objects.create_user(
            email="uploader@example.com",
            external_id="uploader-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(name="Inspection Corridor")
        self.site = Site.objects.create(
            project=self.project,
            name="South Block",
            coordinates=Point(3.4211, 6.4512, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Morning Capture",
            survey_date=date(2026, 8, 9),
        )
        self.other_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Afternoon Capture",
            survey_date=date(2026, 8, 8),
        )

    def create_survey_file(self, **overrides):
        payload = {
            "survey": self.survey,
            "original_filename": "orthomosaic.tif",
            "stored_filename": "survey-1-orthomosaic.tif",
            "file_type": FileType.TWO_D,
            "format": FileFormat.GEOTIFF,
            "mime_type": "image/tiff",
            "size_bytes": 2048,
            "sha256_checksum": "a" * 64,
            "storage_path": "surveys/1/raw/orthomosaic.tif",
            "preview_path": "surveys/1/previews/orthomosaic.jpg",
            "converted_path": "surveys/1/converted/orthomosaic-cog.tif",
            "uploaded_by": self.uploader,
        }
        payload.update(overrides)
        return SurveyFile.objects.create(**payload)

    def create_survey_file_asset(self, **overrides):
        survey_file = overrides.pop("survey_file", None) or self.create_survey_file()
        payload = {
            "survey_file": survey_file,
            "original_filename": "materials.mtl",
            "stored_filename": "materials.mtl",
            "mime_type": "text/plain",
            "size_bytes": 1024,
            "sha256_checksum": "b" * 64,
            "storage_path": f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/assets/materials.mtl",
        }
        payload.update(overrides)
        return SurveyFileAsset.objects.create(**payload)

    def test_survey_file_metadata_paths_uploader_and_default_status(self):
        survey_file = self.create_survey_file()

        self.assertEqual(survey_file.survey, self.survey)
        self.assertEqual(survey_file.original_filename, "orthomosaic.tif")
        self.assertEqual(survey_file.stored_filename, "survey-1-orthomosaic.tif")
        self.assertEqual(survey_file.file_type, FileType.TWO_D)
        self.assertEqual(survey_file.format, FileFormat.GEOTIFF)
        self.assertEqual(survey_file.mime_type, "image/tiff")
        self.assertEqual(survey_file.size_bytes, 2048)
        self.assertEqual(survey_file.sha256_checksum, "a" * 64)
        self.assertEqual(survey_file.storage_path, "surveys/1/raw/orthomosaic.tif")
        self.assertEqual(survey_file.preview_path, "surveys/1/previews/orthomosaic.jpg")
        self.assertEqual(
            survey_file.converted_path,
            "surveys/1/converted/orthomosaic-cog.tif",
        )
        self.assertEqual(survey_file.status, "uploading")
        self.assertEqual(survey_file.uploaded_by, self.uploader)
        self.assertEqual(self.survey.files.get(), survey_file)
        self.assertEqual(self.uploader.files_uploaded.get(), survey_file)

    def test_duplicate_checksum_is_rejected_within_survey_but_allowed_across_surveys(self):
        self.create_survey_file()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_survey_file(
                    stored_filename="survey-1-orthomosaic-copy.tif",
                    storage_path="surveys/1/raw/orthomosaic-copy.tif",
                )

        duplicate_on_other_survey = self.create_survey_file(
            survey=self.other_survey,
            stored_filename="survey-2-orthomosaic.tif",
            storage_path="surveys/2/raw/orthomosaic.tif",
        )

        self.assertEqual(duplicate_on_other_survey.survey, self.other_survey)

    def test_upload_session_defaults_and_one_to_one_file_relationship(self):
        survey_file = self.create_survey_file()
        session = UploadSession.objects.create(
            id="upload-session-1",
            survey=self.survey,
            file=survey_file,
            file_type=FileType.TWO_D,
            total_size_bytes=4096,
        )

        self.assertEqual(session.uploaded_bytes, 0)
        self.assertEqual(session.progress_percent, 0)
        self.assertEqual(session.status, "in_progress")
        self.assertIsNone(session.checksum_expected)
        self.assertEqual(session.file, survey_file)
        self.assertEqual(survey_file.upload_session, session)
        self.assertEqual(self.survey.upload_sessions.get(), session)

    def test_survey_file_asset_persists_metadata_and_relationship(self):
        survey_file = self.create_survey_file()
        asset = self.create_survey_file_asset(survey_file=survey_file)

        self.assertEqual(asset.survey_file, survey_file)
        self.assertEqual(asset.original_filename, "materials.mtl")
        self.assertEqual(asset.stored_filename, "materials.mtl")
        self.assertEqual(asset.mime_type, "text/plain")
        self.assertEqual(asset.size_bytes, 1024)
        self.assertEqual(asset.sha256_checksum, "b" * 64)
        self.assertEqual(
            asset.storage_path,
            f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/assets/materials.mtl",
        )
        self.assertEqual(survey_file.assets.get(), asset)

    def test_survey_file_asset_indexes_and_uniqueness_constraints_are_enforced(self):
        survey_file = self.create_survey_file()
        other_survey_file = self.create_survey_file(
            survey=self.other_survey,
            stored_filename="survey-2-orthomosaic.tif",
            storage_path="surveys/2/raw/orthomosaic.tif",
            sha256_checksum="c" * 64,
        )
        self.create_survey_file_asset(survey_file=survey_file)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_survey_file_asset(
                    survey_file=survey_file,
                    stored_filename="other.mtl",
                    sha256_checksum="b" * 64,
                    storage_path=f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/assets/other.mtl",
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_survey_file_asset(
                    survey_file=survey_file,
                    stored_filename="other.mtl",
                    sha256_checksum="d" * 64,
                )

        duplicate_name_on_other_file = self.create_survey_file_asset(
            survey_file=other_survey_file,
            stored_filename="materials.mtl",
            storage_path=f"surveys/{other_survey_file.survey_id}/files/{other_survey_file.pk}/assets/materials.mtl",
        )
        self.assertEqual(duplicate_name_on_other_file.survey_file, other_survey_file)

        constraints = {
            constraint.name
            for constraint in SurveyFileAsset._meta.constraints
        }
        self.assertIn("survey_file_asset_unique_name_per_file", constraints)
        self.assertIn("survey_file_asset_unique_checksum_per_file", constraints)
        self.assertIn("survey_file_asset_unique_path_per_file", constraints)
        index_fields = {tuple(index.fields) for index in SurveyFileAsset._meta.indexes}
        self.assertIn(("survey_file",), index_fields)
        self.assertIn(("sha256_checksum",), index_fields)

    def test_deleting_survey_cascades_to_survey_files_and_upload_sessions(self):
        survey_file = self.create_survey_file()
        asset = self.create_survey_file_asset(survey_file=survey_file)
        session = UploadSession.objects.create(
            id="upload-session-2",
            survey=self.survey,
            file=survey_file,
            file_type=FileType.TWO_D,
            total_size_bytes=4096,
        )

        self.survey.delete()

        self.assertFalse(SurveyFile.objects.filter(pk=survey_file.pk).exists())
        self.assertFalse(SurveyFileAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(UploadSession.objects.filter(pk=session.pk).exists())

    def test_deleting_survey_file_cascades_to_related_assets(self):
        survey_file = self.create_survey_file()
        asset = self.create_survey_file_asset(survey_file=survey_file)

        survey_file.delete()

        self.assertFalse(SurveyFileAsset.objects.filter(pk=asset.pk).exists())

    def test_deleting_survey_file_sets_linked_upload_session_file_to_null(self):
        survey_file = self.create_survey_file()
        session = UploadSession.objects.create(
            id="upload-session-3",
            survey=self.survey,
            file=survey_file,
            file_type=FileType.TWO_D,
            total_size_bytes=4096,
        )

        survey_file.delete()
        session.refresh_from_db()

        self.assertIsNone(session.file)


class FileValidationTests(TestCase):
    def make_upload(self, name, content, content_type):
        return TrackingUpload(name=name, content=content, content_type=content_type)

    def test_every_allowed_format_family_is_accepted(self):
        cases = [
            ("ortho-geotiff.tif", b"II*\x00\x08\x00\x00\x00\x01\x00\xAF\x87\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00", "image/tiff", FileType.TWO_D, FileFormat.GEOTIFF),
            ("image.tiff", b"II*\x00\x08\x00\x00\x00\x00\x00", "image/tiff", FileType.TWO_D, FileFormat.TIFF),
            ("preview.png", b"\x89PNG\r\n\x1a\nrest", "image/png", FileType.TWO_D, FileFormat.PNG),
            ("photo.jpg", b"\xff\xd8\xff\xe0rest", "image/jpeg", FileType.TWO_D, FileFormat.JPEG),
            ("area.kml", b'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>', "application/vnd.google-earth.kml+xml", FileType.TWO_D, FileFormat.KML),
            ("outline.geojson", b'{"type":"FeatureCollection","features":[]}', "application/geo+json", FileType.TWO_D, FileFormat.GEOJSON),
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

    def test_generic_mime_is_accepted_only_for_valid_3d_files(self):
        cases = [
            ("mesh.obj", b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n", FileFormat.OBJ),
            ("scene.glb", b"glTF\x02\x00\x00\x00rest", FileFormat.GLB),
            ("scene.gltf", b'{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}]}', FileFormat.GLTF),
            ("cloud.las", b"LASF\x00\x00\x00\x00", FileFormat.LAS),
            ("cloud.laz", b"LASF\x00\x00\x00\x00", FileFormat.LAZ),
            ("mesh.ply", b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n0\n", FileFormat.PLY),
            ("shape.stl", b"solid cube\nfacet normal 0 0 0\nouter loop\nendloop\nendfacet\nendsolid cube\n", FileFormat.STL),
        ]
        for name, content, expected_format in cases:
            with self.subTest(name=name):
                result = validate_upload(self.make_upload(name, content, "application/octet-stream"))
                self.assertEqual(result.file_type, FileType.THREE_D)
                self.assertEqual(result.file_format, expected_format)

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("photo.jpg", b"\xff\xd8\xff\xe0rest", "application/octet-stream"))

        with self.assertRaises(FileValidationError):
            validate_upload(self.make_upload("scene.glb", b"not-a-glb", "application/octet-stream"))

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

    def test_storage_filename_sanitization_is_explicit_and_small(self):
        self.assertEqual(
            sanitize_storage_filename("Quarterly Survey ../North Block (Final).GLB"),
            "North-Block-Final.glb",
        )


class PrivateR2StorageAdapterTests(TestCase):
    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_r2_client_uses_fake_overridden_settings_only(self, client_factory):
        client = Mock()
        client_factory.return_value = client

        adapter = PrivateR2StorageAdapter()

        client_factory.assert_called_once_with(
            "s3",
            endpoint_url="https://example.invalid",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
            region_name="auto",
        )
        self.assertEqual(adapter.bucket_name, "test-bucket")

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_managed_multipart_upload_uses_chunk_settings_and_no_public_acl(self, client_factory):
        client = Mock()
        client_factory.return_value = client

        adapter = PrivateR2StorageAdapter()
        upload = TrackingUpload(
            name="North Block Final.glb",
            content=b"glTF\x02\x00\x00\x00rest",
            content_type="model/gltf-binary",
        )
        upload.seek(4)

        storage_key = adapter.upload(
            survey_id=42,
            filename=upload.name,
            file_obj=upload,
            content_type=upload.content_type,
            identifier="fixed123",
        )

        client.upload_fileobj.assert_called_once()

        kwargs = client.upload_fileobj.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(
            kwargs["Key"],
            "surveys/42/staging/fixed123_North-Block-Final.glb",
        )
        self.assertIs(kwargs["Fileobj"]._file_obj, upload)
        self.assertEqual(kwargs["ExtraArgs"]["ContentType"], "model/gltf-binary")
        self.assertNotIn("ACL", kwargs["ExtraArgs"])
        self.assertEqual(
            kwargs["Config"].multipart_threshold,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(
            kwargs["Config"].multipart_chunksize,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )

        self.assertEqual(upload.tell(), 0)
        self.assertEqual(
            storage_key,
            "surveys/42/staging/fixed123_North-Block-Final.glb",
        )
        client.put_object.assert_not_called()

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_upload_to_staging_streams_small_reads_and_returns_sha256_checksum(self, client_factory):
        client = Mock()
        client_factory.return_value = client
        original_bytes = b"glTF\x02\x00\x00\x00payload-for-sha256"
        upload = TrackingUpload(
            name="North Block Final.glb",
            content=original_bytes,
            content_type="model/gltf-binary",
        )
        upload.seek(7)
        read_sizes = []

        def consume_fileobj(*, Fileobj, Bucket, Key, ExtraArgs, Config):
            self.assertEqual(Bucket, "test-bucket")
            self.assertEqual(
                Key,
                "surveys/42/staging/fixed123_North-Block-Final.glb",
            )
            self.assertEqual(ExtraArgs, {"ContentType": "model/gltf-binary"})
            self.assertNotIn("ACL", ExtraArgs)
            self.assertEqual(
                Config.multipart_threshold,
                FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
            )
            self.assertEqual(
                Config.multipart_chunksize,
                FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
            )

            chunks = []
            while True:
                chunk = Fileobj.read(3)
                read_sizes.append(3)
                if not chunk:
                    break
                chunks.append(chunk)

            self.assertEqual(b"".join(chunks), original_bytes)

        client.upload_fileobj.side_effect = consume_fileobj

        adapter = PrivateR2StorageAdapter()
        staged_upload = adapter.upload_to_staging(
            survey_id=42,
            filename=upload.name,
            file_obj=upload,
            content_type=upload.content_type,
            identifier="fixed123",
        )

        client.upload_fileobj.assert_called_once()
        client.put_object.assert_not_called()
        client.copy_object.assert_not_called()
        client.delete_object.assert_not_called()
        self.assertEqual(
            staged_upload.storage_key,
            "surveys/42/staging/fixed123_North-Block-Final.glb",
        )
        self.assertEqual(
            staged_upload.sha256_checksum,
            hashlib.sha256(original_bytes).hexdigest(),
        )
        self.assertEqual(upload.max_requested_read, 3)
        self.assertGreater(len(read_sizes), 1)
        self.assertEqual(upload.tell(), len(original_bytes))

    def test_storage_key_generation_is_server_controlled_and_sanitized(self):
        adapter = PrivateR2StorageAdapter(client=Mock(), bucket_name="private-bucket")

        key = adapter.build_storage_key(
            survey_id=7,
            filename="../Quarterly Survey (North).laz",
            identifier="abc123",
        )

        self.assertEqual(key, "surveys/7/staging/abc123_Quarterly-Survey-North.laz")

    def test_staging_and_canonical_key_generation_follow_documented_private_layout(self):
        adapter = PrivateR2StorageAdapter(client=Mock(), bucket_name="private-bucket")

        staging_key = adapter.build_staging_key(
            survey_id=7,
            filename="../Quarterly Survey (North).laz",
            identifier="abc123",
        )
        canonical_key = adapter.build_canonical_key(
            survey_id=7,
            file_id=99,
            extension=".laz",
        )

        self.assertEqual(
            staging_key,
            "surveys/7/staging/abc123_Quarterly-Survey-North.laz",
        )
        self.assertEqual(canonical_key, "surveys/7/files/99/raw.laz")

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_promotion_uses_server_side_copy_then_deletes_staging_without_public_url(self, client_factory):
        client = Mock()
        client_factory.return_value = client

        adapter = PrivateR2StorageAdapter()
        adapter.promote_object(
            source_key="surveys/7/staging/upload_Quarterly-Survey-North.laz",
            destination_key="surveys/7/files/99/raw.laz",
            content_type="application/vnd.laszip",
        )

        client.copy_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="surveys/7/files/99/raw.laz",
            CopySource={
                "Bucket": "test-bucket",
                "Key": "surveys/7/staging/upload_Quarterly-Survey-North.laz",
            },
            ContentType="application/vnd.laszip",
            MetadataDirective="REPLACE",
        )
        client.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="surveys/7/staging/upload_Quarterly-Survey-North.laz",
        )
        client.put_object.assert_not_called()

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_worker_download_streams_private_object_to_writable_fileobj(self, client_factory):
        client = Mock()
        client_factory.return_value = client
        destination = BytesIO()

        adapter = PrivateR2StorageAdapter()
        adapter.download_to_fileobj(
            storage_key="surveys/7/files/99/raw.laz",
            file_obj=destination,
        )

        client.download_fileobj.assert_called_once()
        kwargs = client.download_fileobj.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(kwargs["Key"], "surveys/7/files/99/raw.laz")
        self.assertIs(kwargs["Fileobj"], destination)
        self.assertEqual(
            kwargs["Config"].multipart_threshold,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(
            kwargs["Config"].multipart_chunksize,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        client.put_object.assert_not_called()
        client.copy_object.assert_not_called()
        client.delete_object.assert_not_called()

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_worker_generated_output_upload_uses_managed_multipart_without_public_acl(self, client_factory):
        client = Mock()
        client_factory.return_value = client
        generated_output = TrackingUpload(
            name="mesh.glb",
            content=b"glTF\x02\x00\x00\x00generated-output",
            content_type="model/gltf-binary",
        )
        generated_output.seek(6)

        adapter = PrivateR2StorageAdapter()
        adapter.upload_generated_fileobj(
            destination_key="surveys/7/files/99/generated/mesh.glb",
            file_obj=generated_output,
            content_type="model/gltf-binary",
        )

        client.upload_fileobj.assert_called_once()
        kwargs = client.upload_fileobj.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(kwargs["Key"], "surveys/7/files/99/generated/mesh.glb")
        self.assertIs(kwargs["Fileobj"], generated_output)
        self.assertEqual(kwargs["ExtraArgs"], {"ContentType": "model/gltf-binary"})
        self.assertNotIn("ACL", kwargs["ExtraArgs"])
        self.assertEqual(
            kwargs["Config"].multipart_threshold,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(
            kwargs["Config"].multipart_chunksize,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(generated_output.tell(), 0)
        client.put_object.assert_not_called()
        client.copy_object.assert_not_called()
        client.delete_object.assert_not_called()


class UploadAdmissionServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            external_id="admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="owner-manager@example.com",
            external_id="manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.other_manager = User.objects.create_user(
            email="other-manager@example.com",
            external_id="manager-2",
            role=UserRole.PROJECT_MANAGER,
        )
        self.assigned_engineer = User.objects.create_user(
            email="assigned-engineer@example.com",
            external_id="engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.unassigned_engineer = User.objects.create_user(
            email="unassigned-engineer@example.com",
            external_id="engineer-2",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer@example.com",
            external_id="viewer-1",
            role=UserRole.VIEWER,
        )
        self.inactive_admin = User.objects.create_user(
            email="inactive-admin@example.com",
            external_id="inactive-admin-1",
            role=UserRole.ADMINISTRATOR,
            is_active=False,
            is_staff=True,
        )
        self.inactive_owner_manager = User.objects.create_user(
            email="inactive-owner-manager@example.com",
            external_id="inactive-manager-1",
            role=UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.inactive_assigned_engineer = User.objects.create_user(
            email="inactive-assigned-engineer@example.com",
            external_id="inactive-engineer-1",
            role=UserRole.SURVEY_ENGINEER,
            is_active=False,
        )
        self.project = Project.objects.create(
            name="Airport Expansion",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.archived_project = Project.objects.create(
            name="Archived Airport Expansion",
            project_manager=self.owner_manager,
            created_by=self.admin,
            status="archived",
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Runway West",
            coordinates=Point(3.4211, 6.4512, srid=4326),
        )
        self.archived_site = Site.objects.create(
            project=self.archived_project,
            name="Runway East",
            coordinates=Point(3.5111, 6.5512, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Week 1 Capture",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.DRAFT,
        )
        self.archived_project_survey = Survey.objects.create(
            project=self.archived_project,
            site=self.archived_site,
            name="Archived Week 1 Capture",
            survey_date=date(2026, 8, 8),
            status=SurveyStatus.DRAFT,
        )
        for member in (self.assigned_engineer, self.viewer, self.inactive_assigned_engineer):
            ProjectMembership.objects.create(
                project=self.project,
                user=member,
                assigned_by=self.owner_manager,
            )

    def make_upload(self, name="site_ortho.tif", content=None, content_type="image/tiff"):
        payload = content or (
            b"II*\x00\x08\x00\x00\x00\x01\x00\xAF\x87\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00"
        )
        return TrackingUpload(name=name, content=payload, content_type=content_type)

    def create_existing_file_with_job(self, **overrides):
        payload = {
            "survey": self.survey,
            "original_filename": "site_ortho.tif",
            "stored_filename": "site_ortho.tif",
            "file_type": FileType.TWO_D,
            "format": FileFormat.GEOTIFF,
            "mime_type": "image/tiff",
            "size_bytes": 2048,
            "sha256_checksum": "a" * 64,
            "storage_path": "surveys/1/files/1/raw.tif",
            "uploaded_by": self.assigned_engineer,
        }
        payload.update(overrides)
        survey_file = SurveyFile.objects.create(**payload)
        processing_job = ProcessingJob.objects.create(file=survey_file, status="queued")
        return survey_file, processing_job

    @override_settings(MAX_SURVEY_TOTAL_SIZE_BYTES=1024)
    def test_upload_authorization_cases(self):
        allowed_users = (self.admin, self.owner_manager, self.assigned_engineer)
        denied_users = (
            self.viewer,
            self.unassigned_engineer,
            self.other_manager,
            self.inactive_admin,
            self.inactive_owner_manager,
            self.inactive_assigned_engineer,
        )

        for actor in allowed_users:
            with self.subTest(actor=actor.email):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Upload Survey {actor.pk}",
                    survey_date=date(2026, 8, 9),
                    status=SurveyStatus.DRAFT,
                )
                storage = FakePrivateStorageAdapter()
                result = admit_uploaded_file(
                    actor=actor,
                    survey=survey,
                    uploaded_file=self.make_upload(),
                    storage=storage,
                )

                self.assertTrue(result.created)
                self.assertEqual(result.survey_file.uploaded_by, actor)

        for actor in denied_users:
            with self.subTest(actor=actor.email):
                storage = FakePrivateStorageAdapter()
                with self.assertRaisesMessage(
                    PermissionDenied,
                    "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files.",
                ):
                    admit_uploaded_file(
                        actor=actor,
                        survey=self.survey,
                        uploaded_file=self.make_upload(),
                        storage=storage,
                    )

                self.assertEqual(storage.uploaded, [])

    def test_archived_project_is_rejected_without_upload(self):
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(
            ValidationError,
            "Uploads are not allowed for archived projects.",
        ):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.archived_project_survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])
        self.assertEqual(SurveyFile.objects.count(), 0)

    @override_settings(MAX_SURVEY_TOTAL_SIZE_BYTES=1024)
    def test_allowed_and_rejected_survey_states_are_enforced(self):
        allowed_states = (
            SurveyStatus.DRAFT,
            SurveyStatus.UPLOADING,
            SurveyStatus.PROCESSING,
            SurveyStatus.FAILED,
            SurveyStatus.READY,
        )
        rejected_states = (
            SurveyStatus.PENDING_APPROVAL,
            SurveyStatus.APPROVED,
            SurveyStatus.REJECTED,
            SurveyStatus.ARCHIVED,
        )

        for state in allowed_states:
            with self.subTest(state=state):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Allowed {state}",
                    survey_date=date(2026, 8, 9),
                    status=state,
                )
                storage = FakePrivateStorageAdapter()
                result = admit_uploaded_file(
                    actor=self.admin,
                    survey=survey,
                    uploaded_file=self.make_upload(),
                    storage=storage,
                )

                self.assertTrue(result.created)

        for state in rejected_states:
            with self.subTest(state=state):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Rejected {state}",
                    survey_date=date(2026, 8, 9),
                    status=state,
                )
                storage = FakePrivateStorageAdapter()
                with self.assertRaisesMessage(
                    ValidationError,
                    "Uploads are not allowed for surveys in the current state.",
                ):
                    admit_uploaded_file(
                        actor=self.admin,
                        survey=survey,
                        uploaded_file=self.make_upload(),
                        storage=storage,
                    )

                self.assertEqual(storage.uploaded, [])

    @patch("apps.files.services.validate_upload", side_effect=FileValidationError("invalid upload"))
    def test_validation_happens_before_r2_upload(self, mocked_validate_upload):
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(FileValidationError, "invalid upload"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        mocked_validate_upload.assert_called_once()
        self.assertEqual(storage.uploaded, [])

    def test_sha256_is_stored_for_new_upload(self):
        storage = FakePrivateStorageAdapter()
        upload = self.make_upload(content=b"\x89PNG\r\n\x1a\npayload", name="preview.png", content_type="image/png")

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=upload,
            storage=storage,
        )

        self.assertEqual(
            result.survey_file.sha256_checksum,
            hashlib.sha256(b"\x89PNG\r\n\x1a\npayload").hexdigest(),
        )

    @override_settings(MAX_SURVEY_TOTAL_SIZE_BYTES=40)
    def test_survey_total_size_limit_is_enforced(self):
        self.create_existing_file_with_job(
            size_bytes=30,
            sha256_checksum="b" * 64,
            storage_path="surveys/1/files/1/raw.tif",
        )
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(
            ValidationError,
            "Survey exceeds the configured total upload size limit.",
        ):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(
                    content=b"II*\x00\x08\x00\x00\x00\x00\x00more-bytes",
                    name="image.tiff",
                ),
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])

    @patch("apps.processing.services.dispatch_processing_job_safely")
    def test_duplicate_checksum_returns_existing_file_and_job_and_cleans_staging(self, mocked_dispatch):
        existing_upload = self.make_upload()
        existing_checksum = hashlib.sha256(existing_upload.getvalue()).hexdigest()
        existing_file, existing_job = self.create_existing_file_with_job(
            sha256_checksum=existing_checksum,
            storage_path="surveys/1/files/55/raw.tif",
        )
        self.survey.status = SurveyStatus.READY
        self.survey.processing_status = "completed"
        self.survey.save(update_fields=["status", "processing_status", "updated_at"])
        storage = FakePrivateStorageAdapter()

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=self.make_upload(),
            storage=storage,
        )

        self.survey.refresh_from_db()
        self.assertFalse(result.created)
        self.assertEqual(result.survey_file.pk, existing_file.pk)
        self.assertEqual(result.processing_job.pk, existing_job.pk)
        self.assertEqual(SurveyFile.objects.count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(self.survey.status, SurveyStatus.READY)
        self.assertEqual(self.survey.processing_status, "completed")
        self.assertEqual(len(storage.uploaded), 1)
        self.assertEqual(storage.deleted, storage.uploaded)
        self.assertEqual(storage.promoted, [])
        mocked_dispatch.assert_not_called()

    @patch("apps.processing.services.dispatch_processing_job_safely")
    def test_successful_new_admission_creates_file_job_audit_and_updates_survey(self, mocked_dispatch):
        storage = FakePrivateStorageAdapter()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = admit_uploaded_file(
                actor=self.assigned_engineer,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.survey.refresh_from_db()
        survey_file = SurveyFile.objects.get()
        processing_job = ProcessingJob.objects.get()
        audit_log = AuditLog.objects.get()

        self.assertTrue(result.created)
        self.assertEqual(result.survey_file.pk, survey_file.pk)
        self.assertEqual(result.processing_job.pk, processing_job.pk)
        self.assertEqual(processing_job.file_id, survey_file.pk)
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(audit_log.action, AuditAction.FILE_UPLOADED)
        self.assertEqual(audit_log.entity_type, "survey_file")
        self.assertEqual(audit_log.entity_id, survey_file.pk)
        self.assertEqual(survey_file.storage_path, f"surveys/{self.survey.pk}/files/{survey_file.pk}/raw.tif")
        self.assertTrue(survey_file.storage_path.endswith("/raw.tif"))
        self.assertEqual(self.survey.status, SurveyStatus.UPLOADING)
        self.assertEqual(self.survey.processing_status, "queued")
        self.assertEqual(len(storage.promoted), 1)
        self.assertEqual(storage.deleted, storage.uploaded)
        self.assertEqual(len(callbacks), 1)
        mocked_dispatch.assert_not_called()

        callbacks[0]()
        mocked_dispatch.assert_called_once_with(processing_job_id=processing_job.pk)

    @patch("apps.processing.services.logger")
    @patch("apps.processing.services.dispatch_processing_job", side_effect=RuntimeError("broker unavailable"))
    def test_broker_failure_leaves_accepted_job_queued_and_logs_safely(self, mocked_dispatch, mocked_logger):
        storage = FakePrivateStorageAdapter()

        with self.captureOnCommitCallbacks(execute=True):
            result = admit_uploaded_file(
                actor=self.assigned_engineer,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        processing_job = ProcessingJob.objects.get(pk=result.processing_job.pk)
        self.assertEqual(processing_job.status, "queued")
        self.assertIsNone(processing_job.celery_task_id)
        mocked_dispatch.assert_called_once_with(processing_job_id=processing_job.pk)
        mocked_logger.warning.assert_called_once()

    @patch("apps.files.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_failure_rolls_back_records_and_cleans_storage(self, mocked_record_audit_event):
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(len(storage.uploaded), 1)
        self.assertGreaterEqual(len(storage.deleted), 2)
        self.assertEqual(storage.objects, {})
        mocked_record_audit_event.assert_called_once()

    def test_promotion_failure_rolls_back_records_and_cleans_storage(self):
        storage = FakePrivateStorageAdapter()

        def fail_promotion(*, source_key, destination_key, content_type):
            storage.objects[destination_key] = {"content": b"copied", "content_type": content_type}
            raise RuntimeError("promotion failed")

        storage.promote_object = fail_promotion

        with self.assertRaisesMessage(RuntimeError, "promotion failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(storage.objects, {})

    def test_obj_assets_are_persisted_and_count_toward_survey_total(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nmtllib materials.mtl\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        asset_upload = TrackingUpload(
            name="materials.mtl",
            content=b"newmtl material\nmap_Kd texture.png\n",
            content_type="text/plain",
        )

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=obj_upload,
            asset_files=[asset_upload],
            storage=storage,
        )

        asset = SurveyFileAsset.objects.get(survey_file=result.survey_file)
        self.assertEqual(asset.original_filename, "materials.mtl")
        self.assertEqual(asset.storage_path, f"surveys/{self.survey.pk}/files/{result.survey_file.pk}/assets/materials.mtl")
        self.assertIn(asset.storage_path, storage.objects)

    def test_invalid_obj_asset_is_rejected_before_storage(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        invalid_asset = TrackingUpload(
            name="payload.gif",
            content=b"GIF89a",
            content_type="image/gif",
        )

        with self.assertRaisesMessage(ValidationError, "Unsupported OBJ asset extension."):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[invalid_asset],
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])
        self.assertEqual(SurveyFileAsset.objects.count(), 0)

    def test_asset_promotion_failure_rolls_back_primary_file_and_assets(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nmtllib materials.mtl\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        asset_upload = TrackingUpload(
            name="materials.mtl",
            content=b"newmtl material\n",
            content_type="text/plain",
        )
        original_promote = storage.promote_object

        def fail_asset_promotion(*, source_key, destination_key, content_type):
            if "/assets/" in destination_key:
                storage.objects[destination_key] = {"content": b"asset", "content_type": content_type}
                raise RuntimeError("asset promotion failed")
            return original_promote(
                source_key=source_key,
                destination_key=destination_key,
                content_type=content_type,
            )

        storage.promote_object = fail_asset_promotion

        with self.assertRaisesMessage(RuntimeError, "asset promotion failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[asset_upload],
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(SurveyFileAsset.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(storage.objects, {})

    def test_later_asset_staging_failure_cleans_up_primary_and_earlier_assets(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nmtllib materials.mtl\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        first_asset = TrackingUpload(
            name="materials.mtl",
            content=b"newmtl material\n",
            content_type="text/plain",
        )
        second_asset = TrackingUpload(
            name="texture.png",
            content=b"\x89PNG\r\n\x1a\ntexture",
            content_type="image/png",
        )
        original_upload_to_staging = storage.upload_to_staging

        def fail_later_asset_staging(*, survey_id, filename, file_obj, content_type, identifier=None):
            if filename == "texture.png":
                raise RuntimeError("later asset staging failed")
            return original_upload_to_staging(
                survey_id=survey_id,
                filename=filename,
                file_obj=file_obj,
                content_type=content_type,
                identifier=identifier,
            )

        storage.upload_to_staging = fail_later_asset_staging

        with self.assertRaisesMessage(RuntimeError, "later asset staging failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[first_asset, second_asset],
                storage=storage,
            )

        self.assertEqual(storage.objects, {})
        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(SurveyFileAsset.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)


class SurveyFileApiTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.public_key_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin-api@example.com",
            external_id="admin-api-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="pm-api@example.com",
            external_id="pm-api-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="engineer-api@example.com",
            external_id="engineer-api-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer-api@example.com",
            external_id="viewer-api-1",
            role=UserRole.VIEWER,
        )
        self.unassigned_engineer = User.objects.create_user(
            email="outsider-api@example.com",
            external_id="outsider-api-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="API Upload Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Upload Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Upload Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.DRAFT,
        )
        ProjectMembership.objects.create(project=self.project, user=self.engineer, assigned_by=self.owner_manager)
        ProjectMembership.objects.create(project=self.project, user=self.viewer, assigned_by=self.owner_manager)
        self.url = f"/api/v1/surveys/{self.survey.pk}/files"

    def auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem,
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
        )

    def make_token(self, user):
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user.external_id,
                "email": user.email,
                "role": user.role,
                "exp": now + timedelta(minutes=15),
            },
            self.private_key_pem,
            algorithm="RS256",
        )

    def authenticate(self, user, *, enforce_csrf_checks=False):
        self.client = self.client_class(enforce_csrf_checks=enforce_csrf_checks)
        self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = self.make_token(user)

    def add_csrf(self, path="/projects"):
        response = self.client.get(path)
        token = response.cookies["csrftoken"].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        return token

    def make_upload(self, name="image.png", content=b"\x89PNG\r\n\x1a\npayload", content_type="image/png"):
        return SimpleUploadedFile(name=name, content=content, content_type=content_type)

    @patch("apps.processing.services.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_requires_authentication_and_csrf(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()
        unauthenticated = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            missing_csrf = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            allowed = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed.status_code, 202)

    @override_settings(RATE_LIMIT_UPLOAD="1/m")
    @patch("apps.processing.services.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_throttle_is_enforced(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            first = self.client.post(self.url, {"file": self.make_upload("first.png")}, format="multipart")
            second = self.client.post(self.url, {"file": self.make_upload("second.png")}, format="multipart")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)

    @patch("apps.processing.services.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_scope_validation_and_duplicate_response(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_denied = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

            self.authenticate(self.unassigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            outsider_denied = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            accepted = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")
            duplicate = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

        self.assertEqual(viewer_denied.status_code, 403)
        self.assertEqual(outsider_denied.status_code, 403)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(SurveyFile.objects.count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_rejects_non_multipart_and_invalid_multipart_contract(self, storage_factory):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            json_payload = self.client.post(self.url, {"file": "bad"}, format="json")
            missing_file = self.client.post(self.url, {}, format="multipart")
            unexpected_field = self.client.post(
                self.url,
                {"file": self.make_upload(), "note": "bad"},
                format="multipart",
            )
            assets_for_png = self.client.post(
                self.url,
                {"file": self.make_upload(), "assets": [self.make_upload("texture.png")]},
                format="multipart",
            )
            text_file_field = self.client.post(
                self.url,
                {"file": "not-a-file"},
                format="multipart",
            )
            text_assets_field = self.client.post(
                self.url,
                {"file": self.make_upload("valid.png"), "assets": "not-a-file"},
                format="multipart",
            )

        self.assertEqual(json_payload.status_code, 415)
        self.assertEqual(missing_file.status_code, 400)
        self.assertEqual(unexpected_field.status_code, 400)
        self.assertEqual(assets_for_png.status_code, 400)
        self.assertEqual(text_file_field.status_code, 400)
        self.assertEqual(text_assets_field.status_code, 400)

    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_invalid_primary_upload_returns_400_and_creates_no_side_effects(self, storage_factory):
        fake_storage = FakePrivateStorageAdapter()
        storage_factory.return_value = fake_storage

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.url,
                {
                    "file": self.make_upload(
                        name="bad.png",
                        content=b"\xff\xd8\xff\xe0rest",
                        content_type="image/png",
                    )
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake_storage.objects, {})
        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(SurveyFileAsset.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_file_list_visibility_and_safe_representation(self):
        survey_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="image.png",
            stored_filename="image.png",
            file_type=FileType.TWO_D,
            format=FileFormat.PNG,
            mime_type="image/png",
            size_bytes=128,
            sha256_checksum="a" * 64,
            storage_path="surveys/1/files/1/raw.png",
            preview_path="surveys/1/files/1/preview.png",
            converted_path="surveys/1/files/1/cog.tif",
            status="ready",
            uploaded_by=self.engineer,
        )
        ProcessingJob.objects.create(file=survey_file, status="completed", progress_percent=100)

        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(self.url)

            self.authenticate(self.unassigned_engineer)
            denied = self.client.get(self.url)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        payload = allowed.json()[0]
        self.assertEqual(payload["status"], "ready")
        self.assertIn("processing_job", payload)
        self.assertNotIn("storage_path", payload)
        self.assertNotIn("preview_path", payload)
        self.assertNotIn("converted_path", payload)
        self.assertNotIn("sha256_checksum", payload)

    def create_survey_file(self, survey=None, **overrides):
        target_survey = survey or self.survey
        payload = {
            "survey": target_survey,
            "original_filename": "image.png",
            "stored_filename": "image.png",
            "file_type": FileType.TWO_D,
            "format": FileFormat.PNG,
            "mime_type": "image/png",
            "size_bytes": 128,
            "sha256_checksum": overrides.pop("sha256_checksum", "d" * 64),
            "storage_path": overrides.pop(
                "storage_path",
                f"surveys/{target_survey.pk}/files/1/raw.png",
            ),
            "status": overrides.pop("status", "ready"),
            "uploaded_by": overrides.pop("uploaded_by", self.engineer),
        }
        payload.update(overrides)
        return SurveyFile.objects.create(**payload)

    @patch("apps.files.services.dispatch_file_download_audit_event")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_download_requires_authentication(self, storage_factory, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/object"
        storage_factory.return_value = storage
        survey_file = self.create_survey_file()
        self.survey.status = SurveyStatus.APPROVED
        self.survey.save(update_fields=["status", "updated_at"])

        response = self.client.get(f"/api/v1/surveys/{self.survey.pk}/files/{survey_file.pk}/download")

        self.assertEqual(response.status_code, 401)
        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_download_visibility_approval_and_redirect_contract(self, storage_factory, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/object?sig=1"
        storage_factory.return_value = storage
        survey_file = self.create_survey_file()
        download_url = f"/api/v1/surveys/{self.survey.pk}/files/{survey_file.pk}/download"

        with self.auth_settings():
            self.authenticate(self.viewer)
            draft_denied = self.client.get(download_url)

            self.survey.status = SurveyStatus.ARCHIVED
            self.survey.save(update_fields=["status", "updated_at"])
            archived_denied = self.client.get(download_url)

            self.survey.status = SurveyStatus.APPROVED
            self.survey.save(update_fields=["status", "updated_at"])
            allowed = self.client.get(download_url)

            self.authenticate(self.unassigned_engineer)
            outsider_denied = self.client.get(download_url)

        self.assertEqual(draft_denied.status_code, 403)
        self.assertEqual(archived_denied.status_code, 403)
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(allowed["Location"], "https://download.example.invalid/object?sig=1")
        self.assertEqual(allowed.content, b"")
        self.assertEqual(outsider_denied.status_code, 403)
        storage.generate_private_download_url.assert_called_once_with(
            storage_key=survey_file.storage_path,
            expires_in=300,
        )
        mocked_dispatch.assert_called_once_with(
            user_id=self.viewer.pk,
            project_id=self.project.pk,
            survey_id=self.survey.pk,
            survey_file_id=survey_file.pk,
        )

    @patch("apps.files.services.dispatch_file_download_audit_event")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_download_returns_404_for_file_survey_mismatch(self, storage_factory, mocked_dispatch):
        storage_factory.return_value = Mock()
        other_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Other Survey",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.APPROVED,
        )
        survey_file = self.create_survey_file(survey=other_survey, sha256_checksum="e" * 64)

        with self.auth_settings():
            self.authenticate(self.viewer)
            response = self.client.get(f"/api/v1/surveys/{self.survey.pk}/files/{survey_file.pk}/download")

        self.assertEqual(response.status_code, 404)
        mocked_dispatch.assert_not_called()
        storage_factory.return_value.generate_private_download_url.assert_not_called()


class SurveyFileDownloadServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="download-admin@example.com",
            external_id="download-admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="download-manager@example.com",
            external_id="download-manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="download-engineer@example.com",
            external_id="download-engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="download-viewer@example.com",
            external_id="download-viewer-1",
            role=UserRole.VIEWER,
        )
        self.outsider = User.objects.create_user(
            email="download-outsider@example.com",
            external_id="download-outsider-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Download Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Download Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.approved_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Approved Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.APPROVED,
        )
        self.archived_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Archived Survey",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.ARCHIVED,
        )
        self.pending_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Pending Survey",
            survey_date=date(2026, 8, 8),
            status=SurveyStatus.PENDING_APPROVAL,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.engineer,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.viewer,
            assigned_by=self.owner_manager,
        )
        self.approved_file = SurveyFile.objects.create(
            survey=self.approved_survey,
            original_filename="approved.tif",
            stored_filename="approved.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="f" * 64,
            storage_path=f"surveys/{self.approved_survey.pk}/files/10/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_allows_normal_project_visibility_roles(self, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/file"

        for actor in (self.admin, self.owner_manager, self.engineer, self.viewer):
            with self.subTest(actor=actor.role):
                result = get_survey_file_download_for_user(
                    actor=actor,
                    survey_id=self.approved_survey.pk,
                    file_id=self.approved_file.pk,
                    storage=storage,
                )
                self.assertEqual(result.survey_file, self.approved_file)
                self.assertEqual(result.download_url, "https://download.example.invalid/file")

        self.assertEqual(mocked_dispatch.call_count, 4)

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_denies_unassigned_user_before_presign(self, mocked_dispatch):
        storage = Mock()

        with self.assertRaisesMessage(PermissionDenied, "You do not have permission to access this survey."):
            get_survey_file_download_for_user(
                actor=self.outsider,
                survey_id=self.approved_survey.pk,
                file_id=self.approved_file.pk,
                storage=storage,
            )

        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_denies_non_approved_states_including_archived(self, mocked_dispatch):
        storage = Mock()
        archived_file = SurveyFile.objects.create(
            survey=self.archived_survey,
            original_filename="archived.tif",
            stored_filename="archived.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="1" * 64,
            storage_path=f"surveys/{self.archived_survey.pk}/files/11/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )
        pending_file = SurveyFile.objects.create(
            survey=self.pending_survey,
            original_filename="pending.tif",
            stored_filename="pending.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="2" * 64,
            storage_path=f"surveys/{self.pending_survey.pk}/files/12/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )

        with self.assertRaisesMessage(PermissionDenied, "Downloads are allowed only for approved surveys."):
            get_survey_file_download_for_user(
                actor=self.viewer,
                survey_id=self.archived_survey.pk,
                file_id=archived_file.pk,
                storage=storage,
            )

        with self.assertRaisesMessage(PermissionDenied, "Downloads are allowed only for approved surveys."):
            get_survey_file_download_for_user(
                actor=self.viewer,
                survey_id=self.pending_survey.pk,
                file_id=pending_file.pk,
                storage=storage,
            )

        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_returns_404_for_file_mismatch(self, mocked_dispatch):
        storage = Mock()
        other_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Mismatch Survey",
            survey_date=date(2026, 8, 7),
            status=SurveyStatus.APPROVED,
        )
        other_file = SurveyFile.objects.create(
            survey=other_survey,
            original_filename="other.tif",
            stored_filename="other.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="3" * 64,
            storage_path=f"surveys/{other_survey.pk}/files/13/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )

        with self.assertRaises(SurveyFile.DoesNotExist):
            get_survey_file_download_for_user(
                actor=self.viewer,
                survey_id=self.approved_survey.pk,
                file_id=other_file.pk,
                storage=storage,
            )

        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_presigns_exact_private_raw_object_and_dispatches_audit(self, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/raw"

        result = get_survey_file_download_for_user(
            actor=self.viewer,
            survey_id=self.approved_survey.pk,
            file_id=self.approved_file.pk,
            storage=storage,
        )

        self.assertEqual(result.download_url, "https://download.example.invalid/raw")
        storage.generate_private_download_url.assert_called_once_with(
            storage_key=self.approved_file.storage_path,
            expires_in=300,
        )
        mocked_dispatch.assert_called_once_with(
            user_id=self.viewer.pk,
            project_id=self.project.pk,
            survey_id=self.approved_survey.pk,
            survey_file_id=self.approved_file.pk,
        )

    @patch("apps.audit.tasks.logger")
    @patch("apps.audit.tasks.record_file_download_audit_event.delay", side_effect=RuntimeError("broker unavailable"))
    def test_download_service_redirect_is_not_blocked_by_audit_dispatch_failure(self, mocked_delay, mocked_logger):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/raw"

        result = get_survey_file_download_for_user(
            actor=self.viewer,
            survey_id=self.approved_survey.pk,
            file_id=self.approved_file.pk,
            storage=storage,
        )

        self.assertEqual(result.download_url, "https://download.example.invalid/raw")
        mocked_delay.assert_called_once_with(
            user_id=self.viewer.pk,
            project_id=self.project.pk,
            survey_id=self.approved_survey.pk,
            survey_file_id=self.approved_file.pk,
        )
        mocked_logger.warning.assert_called_once()
