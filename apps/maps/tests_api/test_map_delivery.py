import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.processing.models import ProcessingJob
from apps.projects.models import Project, ProjectMembership, Site
from apps.surveys.models import Survey, SurveyStatus


class FakePrivateStorageAdapter:
    def __init__(self, json_objects=None):
        self.json_objects = dict(json_objects or {})
        self.presign_calls = []

    def download_to_fileobj(self, *, storage_key, file_obj):
        file_obj.write(self.json_objects[storage_key])

    def generate_private_download_url(self, *, storage_key, expires_in):
        self.presign_calls.append((storage_key, expires_in))
        return f"https://signed.example.invalid/{storage_key.replace('/', '__')}?exp={expires_in}"


class MapDeliveryApiTests(APITestCase):
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
            email="maps-admin@example.com",
            external_id="maps-admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="maps-manager@example.com",
            external_id="maps-manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.viewer = User.objects.create_user(
            email="maps-viewer@example.com",
            external_id="maps-viewer-1",
            role=UserRole.VIEWER,
        )
        self.outsider = User.objects.create_user(
            email="maps-outsider@example.com",
            external_id="maps-outsider-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Maps Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Maps Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Pending Survey Review",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.PENDING_APPROVAL,
            processing_status="completed",
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.viewer,
            assigned_by=self.owner_manager,
        )
        self.raster_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="orthomosaic.tif",
            stored_filename="orthomosaic.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="a" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/11/raw.tif",
            converted_path=f"surveys/{self.survey.pk}/files/11/cog.tif",
            status="ready",
            uploaded_by=self.owner_manager,
        )
        self.png_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="preview.png",
            stored_filename="preview.png",
            file_type=FileType.TWO_D,
            format=FileFormat.PNG,
            mime_type="image/png",
            size_bytes=512,
            sha256_checksum="b" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/12/raw.png",
            status="ready",
            uploaded_by=self.owner_manager,
        )
        self.processing_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="processing.tif",
            stored_filename="processing.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.TIFF,
            mime_type="image/tiff",
            size_bytes=1024,
            sha256_checksum="c" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/13/raw.tif",
            status="processing",
            uploaded_by=self.owner_manager,
        )
        ProcessingJob.objects.create(file=self.raster_file, status="completed", progress_percent=100)
        ProcessingJob.objects.create(file=self.png_file, status="completed", progress_percent=100)
        ProcessingJob.objects.create(file=self.processing_file, status="running", progress_percent=50)
        self.metadata_key = f"surveys/{self.survey.pk}/files/{self.raster_file.pk}/tiles/metadata.json"
        self.tile_metadata = {
            "bounds": [3.0, 6.0, 4.0, 7.0],
            "zoom_range": {"min": 0, "max": 18},
            "tile_matrix_bounds": {"18": {"x_min": 3, "x_max": 4, "y_min": 5, "y_max": 6}},
            "generated_tile_count": 4,
        }

    @patch("apps.maps.services.PrivateR2StorageAdapter")
    def test_published_attempt_supplies_layer_metadata_and_exact_tile(self, storage_factory):
        prefix = f"surveys/{self.survey.pk}/files/{self.raster_file.pk}/attempts/" + "a" * 32 + "/"
        self.raster_file.preview_path = prefix + "preview.png"
        self.raster_file.converted_path = prefix + "cog.tif"
        self.raster_file.save(update_fields=["preview_path", "converted_path"])
        fake_storage = FakePrivateStorageAdapter(json_objects={
            prefix + "tiles/metadata.json": json.dumps(self.tile_metadata).encode("utf-8"),
        })
        storage_factory.return_value = fake_storage
        with self.auth_settings():
            self.authenticate(self.viewer)
            layers = self.client.get(f"/api/v1/surveys/{self.survey.pk}/map-layers")
            tile = self.client.get(f"/api/v1/map-layers/{self.raster_file.pk}/tiles/18/3/5")
        self.assertEqual(layers.status_code, 200)
        self.assertEqual(tile.status_code, 302)
        self.assertEqual(layers.json()[0]["bounds"], self.tile_metadata["bounds"])
        self.assertEqual(fake_storage.presign_calls[-1], (prefix + "tiles/18/3/5.png", 300))

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

    def authenticate(self, user):
        self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = self.make_token(user)

    @patch("apps.maps.services.PrivateR2StorageAdapter")
    def test_map_layers_list_enforces_scope_ready_state_and_non_leaking_payload(self, storage_factory):
        fake_storage = FakePrivateStorageAdapter(
            json_objects={self.metadata_key: json.dumps(self.tile_metadata).encode("utf-8")}
        )
        storage_factory.return_value = fake_storage
        url = f"/api/v1/surveys/{self.survey.pk}/map-layers"

        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(url)

            self.authenticate(self.outsider)
            denied = self.client.get(url)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        payload = allowed.json()
        self.assertEqual([item["id"] for item in payload], [self.raster_file.pk, self.png_file.pk])
        raster_payload = payload[0]
        png_payload = payload[1]
        self.assertEqual(raster_payload["bounds"], self.tile_metadata["bounds"])
        self.assertEqual(raster_payload["zoom_range"], self.tile_metadata["zoom_range"])
        self.assertEqual(
            raster_payload["tile_url_template"],
            f"/api/v1/map-layers/{self.raster_file.pk}/tiles/{{z}}/{{x}}/{{y}}",
        )
        self.assertNotIn("source_url", raster_payload)
        self.assertIn("source_url", png_payload)
        self.assertNotIn("storage_path", json.dumps(payload))
        self.assertNotIn("sha256_checksum", json.dumps(payload))
        self.assertEqual(
            fake_storage.presign_calls,
            [(self.png_file.storage_path, 300)],
        )

    @patch("apps.maps.services.PrivateR2StorageAdapter")
    def test_tile_endpoint_redirects_to_signed_exact_tile_without_leaking_storage_key(self, storage_factory):
        fake_storage = FakePrivateStorageAdapter(
            json_objects={self.metadata_key: json.dumps(self.tile_metadata).encode("utf-8")}
        )
        storage_factory.return_value = fake_storage
        url = f"/api/v1/map-layers/{self.raster_file.pk}/tiles/18/3/5"

        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(url)

            self.authenticate(self.outsider)
            denied = self.client.get(url)

            self.authenticate(self.viewer)
            missing = self.client.get(f"/api/v1/map-layers/{self.raster_file.pk}/tiles/19/3/5")

        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(missing.status_code, 404)
        expected_key = f"surveys/{self.survey.pk}/files/{self.raster_file.pk}/tiles/18/3/5.png"
        self.assertEqual(fake_storage.presign_calls, [(expected_key, 300)])
        self.assertNotIn(expected_key, allowed["Location"])
