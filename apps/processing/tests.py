import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileFormat, FileType, SurveyFile, SurveyFileAsset
from apps.processing.models import ProcessingJob
from apps.processing.services import (
    ASSESSMENT_MAX_GENERATED_TILE_ZOOM,
    MAX_AUTOMATIC_RETRIES,
    POTREE_METADATA_FILENAME,
    RETRY_DELAYS_MINUTES,
    _derive_max_zoom,
    _generate_xyz_tile_pyramid,
    ProcessingError,
    create_queued_processing_job,
    dispatch_processing_job,
    execute_processing_task,
    manual_retry_processing_job,
)
from apps.processing.tasks import _run_processing_task
from apps.projects.models import Project, ProjectMembership, Site
from apps.surveys.models import Survey, SurveyStatus


class FakePrivateStorageAdapter:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.download_calls = []
        self.upload_calls = []

    def download_to_fileobj(self, *, storage_key, file_obj):
        self.download_calls.append(storage_key)
        payload = self.objects[storage_key]
        for index in range(0, len(payload), 3):
            file_obj.write(payload[index : index + 3])

    def upload_generated_fileobj(self, *, destination_key, file_obj, content_type):
        self.upload_calls.append((destination_key, content_type))
        file_obj.seek(0)
        self.objects[destination_key] = file_obj.read()


class FakeMaskedArray:
    def __init__(self, bands):
        self._bands = bands

    def filled(self, _value):
        return self._bands


class ProcessingServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            external_id="admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.engineer = User.objects.create_user(
            email="engineer@example.com",
            external_id="engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.other_engineer = User.objects.create_user(
            email="other@example.com",
            external_id="engineer-2",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer@example.com",
            external_id="viewer-1",
            role=UserRole.VIEWER,
        )
        self.project = Project.objects.create(
            name="Harbor Mapping",
            project_manager=self.admin,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Pier Alpha",
            coordinates=Point(3.4211, 6.4512, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Initial Capture",
            survey_date=date(2026, 8, 9),
            created_by=self.engineer,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.engineer,
            assigned_by=self.admin,
        )

    def create_file_and_job(self, *, file_format=FileFormat.GEOTIFF, content=None, **overrides):
        raw_bytes = content or b"II*\x00\x08\x00\x00\x00\x01\x00\xAF\x87\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00"
        extension = {
            FileFormat.GEOTIFF: "tif",
            FileFormat.TIFF: "tiff",
            FileFormat.PNG: "png",
            FileFormat.JPEG: "jpg",
            FileFormat.KML: "kml",
            FileFormat.GEOJSON: "geojson",
            FileFormat.OBJ: "obj",
            FileFormat.GLB: "glb",
            FileFormat.GLTF: "gltf",
            FileFormat.LAS: "las",
            FileFormat.LAZ: "laz",
            FileFormat.PLY: "ply",
            FileFormat.STL: "stl",
        }[file_format]
        payload = {
            "survey": self.survey,
            "original_filename": f"source.{extension}",
            "stored_filename": f"source.{extension}",
            "file_type": FileType.TWO_D if file_format in {
                FileFormat.GEOTIFF,
                FileFormat.TIFF,
                FileFormat.PNG,
                FileFormat.JPEG,
                FileFormat.KML,
                FileFormat.GEOJSON,
            } else FileType.THREE_D,
            "format": file_format,
            "mime_type": {
                FileFormat.GEOTIFF: "image/tiff",
                FileFormat.TIFF: "image/tiff",
                FileFormat.PNG: "image/png",
                FileFormat.JPEG: "image/jpeg",
                FileFormat.KML: "application/vnd.google-earth.kml+xml",
                FileFormat.GEOJSON: "application/geo+json",
                FileFormat.OBJ: "model/obj",
                FileFormat.GLB: "model/gltf-binary",
                FileFormat.GLTF: "model/gltf+json",
                FileFormat.LAS: "application/vnd.las",
                FileFormat.LAZ: "application/vnd.laszip",
                FileFormat.PLY: "application/ply",
                FileFormat.STL: "model/stl",
            }[file_format],
            "size_bytes": len(raw_bytes),
            "sha256_checksum": __import__("hashlib").sha256(raw_bytes).hexdigest(),
            "storage_path": f"raw-{extension}",
            "uploaded_by": self.engineer,
            "status": "uploading",
        }
        payload.update(overrides)
        survey_file = SurveyFile.objects.create(**payload)
        processing_job = create_queued_processing_job(survey_file=survey_file)
        return survey_file, processing_job, raw_bytes

    @staticmethod
    def existing_local_path():
        return str(Path(__file__).resolve())

    def test_dispatch_selects_task_and_persists_celery_task_id(self):
        survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.GLB)

        with patch("apps.processing.tasks.process_3d_file.apply_async") as mocked_apply_async:
            mocked_apply_async.return_value = SimpleNamespace(id="task-3d-1")
            result = dispatch_processing_job(processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertTrue(result.dispatched)
        self.assertEqual(result.celery_task_id, "task-3d-1")
        self.assertEqual(processing_job.celery_task_id, "task-3d-1")
        mocked_apply_async.assert_called_once_with(args=[processing_job.pk])
        self.assertEqual(survey_file.file_type, FileType.THREE_D)

    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_task_is_idempotent_for_running_or_completed_jobs(self, storage_factory):
        storage_factory.return_value = Mock()
        survey_file, processing_job, _raw = self.create_file_and_job()

        for status in ("running", "completed"):
            with self.subTest(status=status):
                processing_job.status = status
                processing_job.save(update_fields=["status", "updated_at"])
                result = execute_processing_task(processing_job_id=processing_job.pk)
                self.assertEqual(result["status"], "ignored")

        self.assertEqual(
            AuditLog.objects.filter(
                action__in=[AuditAction.PROCESSING_STARTED, AuditAction.PROCESSING_COMPLETED]
            ).count(),
            0,
        )

    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_successful_processing_updates_states_and_audits(self, storage_factory):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage
        preview_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00\x3a\x7e\x9b\x55"
            b"\x00\x00\x00\x0cIDAT\x08\x99\x63\x60\x00\x00\x00\x02\x00\x01\xf4\x71\x64\xa6"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        class FakeDataset:
            count = 1
            height = 8
            width = 8

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, **kwargs):
                import numpy

                return FakeMaskedArray(numpy.array([[[0.0, 5.0], [10.0, 15.0]]], dtype="float32"))

        class FakePreviewWriter:
            def __init__(self, output_path):
                self.output_path = Path(output_path)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _data):
                self.output_path.write_bytes(preview_bytes)

        def fake_rasterio_open(path, mode="r", **kwargs):
            if mode == "w":
                return FakePreviewWriter(path)
            return FakeDataset()

        def fake_rasterio_copy(src, dst, driver):
            Path(dst).write_bytes(b"cog-bytes")

        with patch("rasterio.open", side_effect=fake_rasterio_open), patch(
            "rasterio.shutil.copy", side_effect=fake_rasterio_copy
        ):
            result = execute_processing_task(processing_job_id=processing_job.pk)

        preview_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/preview.png"
        converted_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/cog.tif"
        processing_job.refresh_from_db()
        survey_file.refresh_from_db()
        self.survey.refresh_from_db()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(processing_job.status, "completed")
        self.assertEqual(processing_job.progress_percent, 100)
        self.assertEqual(survey_file.status, "ready")
        self.assertEqual(survey_file.preview_path, preview_key)
        self.assertEqual(survey_file.converted_path, converted_key)
        self.assertEqual(self.survey.status, SurveyStatus.READY)
        self.assertEqual(self.survey.processing_status, "completed")
        self.assertTrue(fake_storage.objects[preview_key].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(fake_storage.objects[converted_key], b"cog-bytes")
        self.assertEqual(
            list(AuditLog.objects.values_list("action", flat=True)),
            [AuditAction.PROCESSING_STARTED, AuditAction.PROCESSING_COMPLETED],
        )

    @patch("apps.processing.services._generate_xyz_tile_pyramid")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_raster_processing_uploads_tile_metadata_sidecar_at_deterministic_key(
        self,
        storage_factory,
        mocked_tile_generation,
    ):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage
        mocked_tile_generation.return_value = {
            "bounds": [3.0, 6.0, 4.0, 7.0],
            "zoom_range": {"min": 0, "max": 18},
            "tile_matrix_bounds": {"18": {"x_min": 1, "x_max": 2, "y_min": 3, "y_max": 4}},
            "generated_tile_count": 8,
        }

        class FakeDataset:
            count = 1
            height = 8
            width = 8

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, **kwargs):
                import numpy

                return FakeMaskedArray(numpy.array([[[0.0, 5.0], [10.0, 15.0]]], dtype="float32"))

        class FakePreviewWriter:
            def __init__(self, output_path):
                self.output_path = Path(output_path)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _data):
                self.output_path.write_bytes(b"\x89PNG\r\n\x1a\npng")

        def fake_rasterio_open(path, mode="r", **kwargs):
            if mode == "w":
                return FakePreviewWriter(path)
            return FakeDataset()

        def fake_rasterio_copy(src, dst, driver):
            Path(dst).write_bytes(b"cog-bytes")

        with patch("rasterio.open", side_effect=fake_rasterio_open), patch(
            "rasterio.shutil.copy",
            side_effect=fake_rasterio_copy,
        ):
            execute_processing_task(processing_job_id=processing_job.pk)

        metadata_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/tiles/metadata.json"
        self.assertIn(metadata_key, fake_storage.objects)
        self.assertEqual(
            json.loads(fake_storage.objects[metadata_key].decode("utf-8"))["zoom_range"],
            {"min": 0, "max": 18},
        )
        mocked_tile_generation.assert_called_once()

    def test_generate_xyz_tile_pyramid_creates_real_tiles_and_metadata_without_boundless_reads(self):
        import numpy
        import rasterio
        from rasterio.transform import from_bounds

        survey_file, _processing_job, _raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter()

        with tempfile.TemporaryDirectory(prefix="tile-gen-raw-") as raw_dir, tempfile.TemporaryDirectory(
            prefix="tile-gen-out-"
        ) as output_dir:
            raw_path = Path(raw_dir) / "sample.tif"
            transform = from_bounds(3.0, 6.0, 3.02, 6.02, 32, 32)
            with rasterio.open(
                raw_path,
                "w",
                driver="GTiff",
                width=32,
                height=32,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=transform,
            ) as dataset:
                dataset.write(numpy.full((1, 32, 32), 180, dtype="uint8"))

            metadata = _generate_xyz_tile_pyramid(
                survey_file=survey_file,
                local_raw_path=raw_path,
                temp_dir_path=Path(output_dir),
                storage=fake_storage,
            )

        self.assertGreater(metadata["generated_tile_count"], 0)
        self.assertIn("bounds", metadata)
        self.assertIn("zoom_range", metadata)
        tile_keys = [
            destination_key
            for destination_key, _content_type in fake_storage.upload_calls
            if destination_key.endswith(".png")
        ]
        self.assertGreaterEqual(len(tile_keys), 1)
        self.assertTrue(
            any(
                payload.startswith(b"\x89PNG\r\n\x1a\n")
                for key, payload in fake_storage.objects.items()
                if key.endswith(".png")
            )
        )
        self.assertTrue(all("tiles/" in tile_key for tile_key in tile_keys))

    @patch("apps.processing.services._process_raster_file")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_worker_revalidation_uses_persisted_safe_filename(self, storage_factory, mocked_process_raster):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(
            original_filename="Quarterly Survey Final.tif",
        )
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        mocked_process_raster.return_value = SimpleNamespace(preview_path="preview", converted_path="cog")

        with patch("apps.processing.services.validate_upload", wraps=__import__("apps.files.validation", fromlist=["validate_upload"]).validate_upload) as mocked_validate:
            execute_processing_task(processing_job_id=processing_job.pk)

        validation_file = mocked_validate.call_args.args[0]
        self.assertEqual(validation_file.name, survey_file.original_filename)
        self.assertNotEqual(validation_file.name, survey_file.storage_path)

    @patch("apps.processing.services.validate_upload")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_survey_remains_processing_until_all_files_are_ready(self, storage_factory, mocked_validate):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        self.create_file_and_job(
            file_format=FileFormat.PNG,
            content=b"\x89PNG\r\n\x1a\nrest",
            status="processing",
            storage_path="raw-png",
        )
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})

        with patch(
            "apps.processing.services._process_raster_file",
            return_value=SimpleNamespace(preview_path="preview", converted_path="cog"),
        ):
            execute_processing_task(processing_job_id=processing_job.pk)

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, SurveyStatus.PROCESSING)
        self.assertEqual(self.survey.processing_status, "processing")
        mocked_validate.assert_called_once()

    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_browser_ready_formats_skip_redundant_conversion(self, storage_factory):
        for file_format, payload in (
            (FileFormat.PNG, b"\x89PNG\r\n\x1a\nrest"),
            (FileFormat.JPEG, b"\xff\xd8\xff\xe0rest"),
            (FileFormat.KML, b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>'),
            (FileFormat.GEOJSON, b'{"type":"FeatureCollection","features":[]}'),
        ):
            with self.subTest(file_format=file_format):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Survey {file_format}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename=f"source.{file_format.lower()}",
                    stored_filename=f"source-{file_format}.{file_format.lower()}",
                    file_type=FileType.TWO_D if file_format in {FileFormat.PNG, FileFormat.JPEG, FileFormat.KML, FileFormat.GEOJSON} else FileType.THREE_D,
                    format=file_format,
                    mime_type={
                        FileFormat.PNG: "image/png",
                        FileFormat.JPEG: "image/jpeg",
                        FileFormat.KML: "application/vnd.google-earth.kml+xml",
                        FileFormat.GEOJSON: "application/geo+json",
                        FileFormat.GLB: "model/gltf-binary",
                        FileFormat.GLTF: "model/gltf+json",
                    }[file_format],
                    size_bytes=len(payload),
                    sha256_checksum=__import__("hashlib").sha256(payload).hexdigest(),
                    storage_path=f"browser-ready-{file_format}",
                    uploaded_by=self.engineer,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: payload})

                execute_processing_task(processing_job_id=processing_job.pk)

                survey_file.refresh_from_db()
                self.assertIsNone(survey_file.preview_path)
                self.assertIsNone(survey_file.converted_path)

    @patch("apps.processing.services._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_mesh_formats_keep_full_conversion_and_store_reduced_preview(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        for file_format, raw_bytes, mime_type in (
            (FileFormat.OBJ, b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n", "model/obj"),
            (FileFormat.PLY, b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n", "application/ply"),
            (FileFormat.STL, b"solid mesh\nfacet normal 0 0 1\nendfacet\nendsolid mesh\n", "model/stl"),
        ):
            with self.subTest(file_format=file_format):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Mesh Survey {file_format}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename=f"mesh.{file_format.lower()}",
                    stored_filename=f"mesh.{file_format.lower()}",
                    file_type=FileType.THREE_D,
                    format=file_format,
                    mime_type=mime_type,
                    size_bytes=len(raw_bytes),
                    sha256_checksum=__import__("hashlib").sha256(raw_bytes).hexdigest(),
                    storage_path=f"mesh-{file_format.lower()}",
                    uploaded_by=self.engineer,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
                storage_factory.return_value = fake_storage

                class FakeMesh:
                    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

                    def export(self, output_path, file_type):
                        Path(output_path).write_bytes(f"{file_type}-full".encode("utf-8"))

                def fake_preview_export(*, mesh, destination_path):
                    destination_path.write_bytes(b"preview-glb")

                mocked_trimesh_load.return_value = FakeMesh()
                mocked_export_preview.side_effect = fake_preview_export

                execute_processing_task(processing_job_id=processing_job.pk)

                survey_file.refresh_from_db()
                self.assertEqual(
                    survey_file.preview_path,
                    f"surveys/{survey.pk}/files/{survey_file.pk}/preview.glb",
                )
                self.assertEqual(
                    survey_file.converted_path,
                    f"surveys/{survey.pk}/files/{survey_file.pk}/model.glb",
                )
                self.assertEqual(fake_storage.objects[survey_file.preview_path], b"preview-glb")
                self.assertEqual(fake_storage.objects[survey_file.converted_path], b"glb-full")

    @patch("apps.processing.services._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_model_processing_uploads_private_metadata_sidecar(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        raw_bytes = b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n"
        survey_file, processing_job, _raw = self.create_file_and_job(
            file_format=FileFormat.OBJ,
            content=raw_bytes,
        )
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage

        class FakeBounds:
            def tolist(self):
                return [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]]

        class FakeVertices:
            def __len__(self):
                return 6

        class FakeMesh:
            faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]
            vertices = FakeVertices()
            bounds = FakeBounds()
            metadata = {"crs": "EPSG:4978"}

            def export(self, output_path, file_type):
                Path(output_path).write_bytes(b"glb-output")

            def copy(self):
                return self

        mocked_trimesh_load.return_value = FakeMesh()
        mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
            b"preview-glb"
        )

        execute_processing_task(processing_job_id=processing_job.pk)

        metadata_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/model-metadata.json"
        self.assertIn(metadata_key, fake_storage.objects)
        payload = json.loads(fake_storage.objects[metadata_key].decode("utf-8"))
        self.assertEqual(payload["display_format"], "GLB")
        self.assertEqual(payload["vertex_count"], 6)
        self.assertEqual(payload["bounding_box"]["min"], [0.0, 1.0, 2.0])
        self.assertEqual(payload["crs"], "EPSG:4978")

    @patch("apps.processing.services._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_glb_and_gltf_keep_raw_source_and_store_preview_proxy(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        for file_format, payload, mime_type in (
            (FileFormat.GLB, b"glTF\x02\x00\x00\x00rest", "model/gltf-binary"),
            (FileFormat.GLTF, b'{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}]}', "model/gltf+json"),
        ):
            with self.subTest(file_format=file_format):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Browser Ready Model {file_format}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename=f"scene.{file_format.lower()}",
                    stored_filename=f"scene.{file_format.lower()}",
                    file_type=FileType.THREE_D,
                    format=file_format,
                    mime_type=mime_type,
                    size_bytes=len(payload),
                    sha256_checksum=__import__("hashlib").sha256(payload).hexdigest(),
                    storage_path=f"browser-ready-{file_format.lower()}",
                    uploaded_by=self.engineer,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: payload})
                storage_factory.return_value = fake_storage

                class FakeMesh:
                    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

                mocked_trimesh_load.return_value = FakeMesh()
                mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
                    b"proxy-glb"
                )

                execute_processing_task(processing_job_id=processing_job.pk)

                survey_file.refresh_from_db()
                self.assertEqual(
                    survey_file.preview_path,
                    f"surveys/{survey.pk}/files/{survey_file.pk}/preview.glb",
                )
                self.assertIsNone(survey_file.converted_path)
                self.assertEqual(fake_storage.objects[survey_file.preview_path], b"proxy-glb")

    @patch("apps.processing.services._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_external_gltf_assets_are_staged_and_converted_to_glb(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        gltf_payload = (
            b'{"asset":{"version":"2.0"},"buffers":[{"uri":"scene.bin"}],'
            b'"images":[{"uri":"textures/albedo.jpeg"}]}'
        )
        survey_file, processing_job, _ = self.create_file_and_job(
            file_format=FileFormat.GLTF,
            content=gltf_payload,
        )
        binary_payload = b"external-buffer"
        texture_payload = b"\xff\xd8\xfftexture"
        binary_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/assets/scene.bin"
        texture_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/assets/albedo.jpeg"
        SurveyFileAsset.objects.create(
            survey_file=survey_file,
            original_filename="scene.bin",
            stored_filename="scene.bin",
            mime_type="application/octet-stream",
            size_bytes=len(binary_payload),
            sha256_checksum=__import__("hashlib").sha256(binary_payload).hexdigest(),
            storage_path=binary_key,
        )
        SurveyFileAsset.objects.create(
            survey_file=survey_file,
            original_filename="albedo.jpeg",
            stored_filename="albedo.jpeg",
            mime_type="image/jpeg",
            size_bytes=len(texture_payload),
            sha256_checksum=__import__("hashlib").sha256(texture_payload).hexdigest(),
            storage_path=texture_key,
        )
        fake_storage = FakePrivateStorageAdapter(
            objects={
                survey_file.storage_path: gltf_payload,
                binary_key: binary_payload,
                texture_key: texture_payload,
            }
        )
        storage_factory.return_value = fake_storage

        class FakeMesh:
            faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

            def export(self, output_path, file_type):
                Path(output_path).write_bytes(f"{file_type}-full".encode("utf-8"))

        mocked_trimesh_load.return_value = FakeMesh()
        mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
            b"preview-glb"
        )

        execute_processing_task(processing_job_id=processing_job.pk)

        survey_file.refresh_from_db()
        self.assertEqual(
            survey_file.converted_path,
            f"surveys/{self.survey.pk}/files/{survey_file.pk}/model.glb",
        )
        self.assertEqual(fake_storage.objects[survey_file.converted_path], b"glb-full")
        self.assertEqual(fake_storage.download_calls[:3], [survey_file.storage_path, binary_key, texture_key])

    @patch("apps.processing.services.subprocess.run")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_missing_potree_converter_fails_safely_without_invocation(self, storage_factory, mocked_run):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(file_format=FileFormat.LAZ, content=b"LASF\x00\x00\x00\x00")
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})

        fake_task = Mock()
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            with override_settings(POTREE_CONVERTER_PATH="missing-potree"):
                _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.retry_count, 1)
        mocked_run.assert_not_called()
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(),
            1,
        )

    @patch("apps.processing.services.validate_upload")
    @patch("apps.processing.services.subprocess.run")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_potree_processing_uploads_recursive_output_and_sets_metadata_path(
        self,
        storage_factory,
        mocked_run,
        mocked_validate,
    ):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(
            file_format=FileFormat.LAZ,
            content=b"LASF\x00\x00\x00\x00",
        )
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage

        def fake_run(args, check, capture_output):
            output_dir = Path(args[-1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / POTREE_METADATA_FILENAME).write_text('{"version":"2.0"}', encoding="utf-8")
            hierarchy_dir = output_dir / "hierarchy"
            hierarchy_dir.mkdir()
            (hierarchy_dir / "0.bin").write_bytes(b"hierarchy")
            return SimpleNamespace(returncode=0)

        mocked_run.side_effect = fake_run

        with override_settings(POTREE_CONVERTER_PATH=self.existing_local_path()):
            result = execute_processing_task(processing_job_id=processing_job.pk)

        survey_file.refresh_from_db()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            survey_file.preview_path,
            f"surveys/{self.survey.pk}/files/{survey_file.pk}/potree/{POTREE_METADATA_FILENAME}",
        )
        self.assertEqual(
            survey_file.converted_path,
            f"surveys/{self.survey.pk}/files/{survey_file.pk}/potree/{POTREE_METADATA_FILENAME}",
        )
        uploaded_keys = [key for key, _content_type in fake_storage.upload_calls]
        self.assertIn(
            f"surveys/{self.survey.pk}/files/{survey_file.pk}/potree/{POTREE_METADATA_FILENAME}",
            uploaded_keys,
        )
        self.assertIn(
            f"surveys/{self.survey.pk}/files/{survey_file.pk}/potree/hierarchy/0.bin",
            uploaded_keys,
        )
        mocked_validate.assert_called_once()

    @patch("apps.processing.services.validate_upload")
    @patch("apps.processing.services.subprocess.run")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_potree_processing_requires_metadata_output(
        self,
        storage_factory,
        mocked_run,
        mocked_validate,
    ):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(
            file_format=FileFormat.LAS,
            content=b"LASF\x00\x00\x00\x00",
        )
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage

        def fake_run(args, check, capture_output):
            output_dir = Path(args[-1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "octree.bin").write_bytes(b"missing-metadata")
            return SimpleNamespace(returncode=0)

        mocked_run.side_effect = fake_run
        fake_task = Mock()
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            with override_settings(POTREE_CONVERTER_PATH=self.existing_local_path()):
                _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.retry_count, 1)
        self.assertEqual(fake_storage.upload_calls, [])
        mocked_validate.assert_called_once()

    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_checksum_mismatch_schedules_retry(self, storage_factory):
        survey_file, processing_job, _raw_bytes = self.create_file_and_job()
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: b"corrupt"})
        fake_task = Mock()
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(processing_job.retry_count, 1)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(),
            1,
        )
        fake_task.retry.assert_called_once()

    @patch("apps.processing.tasks.execute_processing_task", side_effect=ProcessingError("File processing failed."))
    def test_automatic_retry_schedule_uses_assessment_backoff(self, _mocked_execute):
        for starting_retry_count, expected_countdown in enumerate((120, 300, 600)):
            with self.subTest(starting_retry_count=starting_retry_count):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Retry Survey {starting_retry_count}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename="source.tif",
                    stored_filename="source.tif",
                    file_type=FileType.TWO_D,
                    format=FileFormat.GEOTIFF,
                    mime_type="image/tiff",
                    size_bytes=1,
                    sha256_checksum="a" * 64,
                    storage_path=f"retry-{starting_retry_count}",
                    uploaded_by=self.engineer,
                )
                processing_job = ProcessingJob.objects.create(
                    file=survey_file,
                    status="queued",
                    retry_count=starting_retry_count,
                )
                fake_task = Mock()
                fake_task.retry.side_effect = RuntimeError("retry scheduled")

                with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
                    _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

                processing_job.refresh_from_db()
                self.assertEqual(processing_job.retry_count, starting_retry_count + 1)
                self.assertEqual(fake_task.retry.call_args.kwargs["countdown"], expected_countdown)

    @patch("apps.processing.tasks.execute_processing_task", side_effect=ProcessingError("File processing failed."))
    def test_fourth_failed_attempt_marks_job_file_and_survey_failed(self, _mocked_execute):
        survey_file, processing_job, _raw_bytes = self.create_file_and_job()
        processing_job.retry_count = MAX_AUTOMATIC_RETRIES
        processing_job.save(update_fields=["retry_count", "updated_at"])
        fake_task = Mock()

        result = _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        survey_file.refresh_from_db()
        self.survey.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(processing_job.status, "failed")
        self.assertEqual(survey_file.status, "failed")
        self.assertEqual(self.survey.status, SurveyStatus.FAILED)
        self.assertEqual(self.survey.processing_status, "failed")
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_FAILED).count(),
            1,
        )
        fake_task.retry.assert_not_called()

    @patch("apps.processing.services.dispatch_processing_job_safely")
    @patch("apps.processing.services._process_raster_file")
    @patch("apps.processing.services.validate_upload")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_manual_retry_enforces_permission_state_and_limit_rules(self, storage_factory, mocked_validate, mocked_process_raster, mocked_dispatch):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        mocked_process_raster.return_value = SimpleNamespace(preview_path="preview", converted_path="cog")

        processing_job.status = "failed"
        processing_job.save(update_fields=["status", "updated_at"])

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only eligible active users can retry failed processing jobs.",
        ):
            manual_retry_processing_job(actor=self.viewer, processing_job_id=processing_job.pk)

        processing_job.status = "completed"
        processing_job.save(update_fields=["status", "updated_at"])
        with self.assertRaisesMessage(
            ValidationError,
            "Only permanently failed processing jobs can be retried manually.",
        ):
            manual_retry_processing_job(actor=self.admin, processing_job_id=processing_job.pk)

        processing_job.status = "failed"
        processing_job.retry_count = MAX_AUTOMATIC_RETRIES
        processing_job.save(update_fields=["status", "retry_count", "updated_at"])
        with self.assertRaisesMessage(
            ValidationError,
            "The processing job has reached the retry limit.",
        ):
            manual_retry_processing_job(actor=self.admin, processing_job_id=processing_job.pk)

        processing_job.retry_count = 0
        processing_job.status = "failed"
        processing_job.progress_percent = 55
        processing_job.error_message = "Old error"
        processing_job.save(update_fields=["retry_count", "status", "progress_percent", "error_message", "updated_at"])

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            retried_job = manual_retry_processing_job(actor=self.engineer, processing_job_id=processing_job.pk)

        retried_job.refresh_from_db()
        self.assertEqual(retried_job.status, "queued")
        self.assertEqual(retried_job.retry_count, 1)
        self.assertEqual(retried_job.progress_percent, 0)
        self.assertIsNone(retried_job.error_message)
        self.assertEqual(len(callbacks), 1)
        mocked_dispatch.assert_not_called()
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(),
            1,
        )

    @patch("trimesh.load")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_obj_conversion_downloads_related_assets_and_uploads_glb(self, storage_factory, mocked_trimesh_load):
        raw_bytes = b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n"
        survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.OBJ, content=raw_bytes)
        asset_bytes = b"newmtl material\n"
        SurveyFileAsset.objects.create(
            survey_file=survey_file,
            original_filename="materials.mtl",
            stored_filename="materials.mtl",
            mime_type="text/plain",
            size_bytes=len(asset_bytes),
            sha256_checksum=__import__("hashlib").sha256(asset_bytes).hexdigest(),
            storage_path="obj-asset",
        )
        fake_storage = FakePrivateStorageAdapter(
            objects={survey_file.storage_path: raw_bytes, "obj-asset": asset_bytes}
        )
        storage_factory.return_value = fake_storage

        exported_paths = []

        class FakeMesh:
            faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

            def export(self, output_path, file_type):
                exported_paths.append((output_path, file_type))
                Path(output_path).write_bytes(b"glb-output")

            def copy(self):
                return self

        mocked_trimesh_load.return_value = FakeMesh()

        with patch("apps.processing.services._export_reduced_glb_preview") as mocked_export_preview:
            mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
                b"preview-glb"
            )
            execute_processing_task(processing_job_id=processing_job.pk)

        self.assertEqual(fake_storage.download_calls, [survey_file.storage_path, "obj-asset"])
        self.assertEqual(len(fake_storage.upload_calls), 2)
        self.assertEqual(exported_paths[0][1], "glb")

    @patch("rasterio.shutil.copy")
    @patch("rasterio.open")
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_raster_processing_streams_private_io_and_cleans_temp_directory(self, storage_factory, mocked_rasterio_open, mocked_rasterio_copy):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage
        observed_temp_dir = {}

        class FakeDataset:
            count = 1
            height = 4
            width = 4

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, **kwargs):
                observed_temp_dir["read_called"] = True
                import numpy

                return FakeMaskedArray(numpy.array([[[0.0, 5.0], [10.0, 15.0]]], dtype="float32"))

        class FakePreviewWriter:
            def __init__(self, output_path):
                self.output_path = Path(output_path)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _data):
                self.output_path.write_bytes(
                    b"\x89PNG\r\n\x1a\n"
                    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00\x3a\x7e\x9b\x55"
                    b"\x00\x00\x00\x0cIDAT\x08\x99\x63\x60\x00\x00\x00\x02\x00\x01\xf4\x71\x64\xa6"
                    b"\x00\x00\x00\x00IEND\xaeB`\x82"
                )

        def fake_rasterio_open(path, mode="r", **kwargs):
            if mode == "w":
                return FakePreviewWriter(path)
            return FakeDataset()

        mocked_rasterio_open.side_effect = fake_rasterio_open

        def record_copy(src, dst, driver):
            observed_temp_dir["path"] = Path(dst).parent
            Path(dst).write_bytes(b"cog")

        mocked_rasterio_copy.side_effect = record_copy

        execute_processing_task(processing_job_id=processing_job.pk)

        self.assertEqual(fake_storage.download_calls, [survey_file.storage_path])
        self.assertEqual(len(fake_storage.upload_calls), 2)
        self.assertTrue(observed_temp_dir["read_called"])
        preview_uploads = [key for key, _content_type in fake_storage.upload_calls if key.endswith("preview.png")]
        self.assertEqual(len(preview_uploads), 1)
        self.assertTrue(fake_storage.objects[preview_uploads[0]].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertFalse(observed_temp_dir["path"].exists())

    @patch("apps.processing.services._process_raster_file", side_effect=ProcessingError("conversion failed"))
    @patch("apps.processing.services.PrivateR2StorageAdapter")
    def test_temp_directory_is_cleaned_after_processing_failure(self, storage_factory, _mocked_process_raster):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        fake_task = Mock()
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.retry_count, 1)


class ProcessingRoutingTests(TestCase):
    def test_retry_constants_match_documented_policy(self):
        self.assertEqual(MAX_AUTOMATIC_RETRIES, 3)
        self.assertEqual(RETRY_DELAYS_MINUTES, (2, 5, 10))

    def test_derived_raster_tile_zoom_is_capped_for_assessment_processing(self):
        derived_zoom = _derive_max_zoom(
            mercator_bounds=(0.0, 0.0, 256.0, 256.0),
            width=65536,
            height=65536,
        )

        self.assertEqual(derived_zoom, ASSESSMENT_MAX_GENERATED_TILE_ZOOM)


class ProcessingJobApiTests(APITestCase):
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
            email="admin-processing@example.com",
            external_id="admin-processing-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="pm-processing@example.com",
            external_id="pm-processing-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="engineer-processing@example.com",
            external_id="engineer-processing-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer-processing@example.com",
            external_id="viewer-processing-1",
            role=UserRole.VIEWER,
        )
        self.other_engineer = User.objects.create_user(
            email="other-processing@example.com",
            external_id="other-processing-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Processing API Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Processing Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Processing Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.FAILED,
            processing_status="failed",
            created_by=self.engineer,
        )
        ProjectMembership.objects.create(project=self.project, user=self.engineer, assigned_by=self.owner_manager)
        ProjectMembership.objects.create(project=self.project, user=self.viewer, assigned_by=self.owner_manager)
        self.survey_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="mesh.obj",
            stored_filename="mesh.obj",
            file_type=FileType.THREE_D,
            format=FileFormat.OBJ,
            mime_type="model/obj",
            size_bytes=128,
            sha256_checksum="a" * 64,
            storage_path="surveys/1/files/1/raw.obj",
            preview_path="surveys/1/files/1/preview.glb",
            converted_path="surveys/1/files/1/model.glb",
            status="failed",
            uploaded_by=self.engineer,
        )
        self.job = ProcessingJob.objects.create(
            file=self.survey_file,
            status="failed",
            progress_percent=99,
            retry_count=0,
            error_message="processing failed",
        )
        self.detail_url = f"/api/v1/processing-jobs/{self.job.pk}"
        self.retry_url = f"{self.detail_url}/retry"

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

    def test_job_detail_visibility_and_safe_representation(self):
        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(self.detail_url)

            self.authenticate(self.other_engineer)
            denied = self.client.get(self.detail_url)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        payload = allowed.json()
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("storage_path", payload["file"])
        self.assertNotIn("sha256_checksum", payload["file"])
        self.assertNotIn("preview_path", payload["file"])
        self.assertNotIn("converted_path", payload["file"])

    @patch("apps.processing.services.dispatch_processing_job_safely")
    def test_retry_requires_authentication_csrf_and_role_scope(self, mocked_dispatch):
        unauthenticated = self.client.post(self.retry_url, {}, format="json")

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            missing_csrf = self.client.post(self.retry_url, {}, format="json")

            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_denied = self.client.post(self.retry_url, {}, format="json")

            self.authenticate(self.other_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            outsider_denied = self.client.post(self.retry_url, {}, format="json")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                accepted = self.client.post(self.retry_url, {}, format="json")

        self.job.refresh_from_db()
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(viewer_denied.status_code, 403)
        self.assertEqual(outsider_denied.status_code, 403)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.job.status, "queued")
        self.assertEqual(self.job.retry_count, 1)
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(), 1)
        self.assertEqual(len(callbacks), 1)
        mocked_dispatch.assert_not_called()

        callbacks[0]()
        mocked_dispatch.assert_called_once_with(processing_job_id=self.job.pk)

    @override_settings(RATE_LIMIT_RETRY="1/m")
    @patch("apps.processing.services.dispatch_processing_job_safely")
    def test_retry_throttle_and_state_guards(self, _mocked_dispatch):
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            first = self.client.post(self.retry_url, {}, format="json")
            second = self.client.post(self.retry_url, {}, format="json")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)

        cache.clear()
        self.job.refresh_from_db()
        self.job.status = "completed"
        self.job.save(update_fields=["status", "updated_at"])
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            wrong_state = self.client.post(self.retry_url, {}, format="json")

        self.assertEqual(wrong_state.status_code, 400)

    @patch("apps.processing.services.dispatch_processing_job_safely")
    def test_retry_limit_guard_returns_validation_error(self, _mocked_dispatch):
        self.job.retry_count = MAX_AUTOMATIC_RETRIES
        self.job.save(update_fields=["retry_count", "updated_at"])

        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(self.retry_url, {}, format="json")

        self.assertEqual(response.status_code, 400)
