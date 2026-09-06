from .support import *
from .test_processing_dispatch import ProcessingDispatchLifecycleMixin
from .test_processing_models import ProcessingModelProcessingMixin
from .test_processing_point_clouds import ProcessingPointCloudMixin
from .test_processing_raster_tiles import ProcessingRasterTilesMixin
from .test_processing_retry import ProcessingRetryMixin
from .test_lease_safety import ProcessingLeaseSafetyMixin


class ProcessingServiceTests(ProcessingDispatchLifecycleMixin, ProcessingRasterTilesMixin, ProcessingModelProcessingMixin, ProcessingPointCloudMixin, ProcessingRetryMixin, ProcessingLeaseSafetyMixin, TestCase):
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

    def published_root(self, survey_file):
        survey_file.refresh_from_db()
        path = survey_file.preview_path or survey_file.converted_path
        root = f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/attempts/"
        self.assertIsNotNone(path)
        self.assertTrue(path.startswith(root))
        attempt = path[len(root):].split("/", 1)[0]
        self.assertRegex(attempt, r"^[0-9a-f]{32}$")
        return f"{root}{attempt}/"
