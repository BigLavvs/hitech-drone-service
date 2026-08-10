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


class ModelDeliveryApiTests(APITestCase):
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
            email="models-admin@example.com",
            external_id="models-admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="models-manager@example.com",
            external_id="models-manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.viewer = User.objects.create_user(
            email="models-viewer@example.com",
            external_id="models-viewer-1",
            role=UserRole.VIEWER,
        )
        self.outsider = User.objects.create_user(
            email="models-outsider@example.com",
            external_id="models-outsider-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Models Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Models Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Models Pending Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.PENDING_APPROVAL,
            processing_status="completed",
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.viewer,
            assigned_by=self.owner_manager,
        )
        self.mesh_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="mesh.obj",
            stored_filename="mesh.obj",
            file_type=FileType.THREE_D,
            format=FileFormat.OBJ,
            mime_type="model/obj",
            size_bytes=2048,
            sha256_checksum="a" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/21/raw.obj",
            converted_path=f"surveys/{self.survey.pk}/files/21/model.glb",
            status="ready",
            uploaded_by=self.owner_manager,
        )
        self.point_cloud_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="cloud.laz",
            stored_filename="cloud.laz",
            file_type=FileType.THREE_D,
            format=FileFormat.LAZ,
            mime_type="application/vnd.laszip",
            size_bytes=4096,
            sha256_checksum="b" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/22/raw.laz",
            converted_path=f"surveys/{self.survey.pk}/files/22/potree/metadata.json",
            status="ready",
            uploaded_by=self.owner_manager,
        )
        self.failed_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="failed.glb",
            stored_filename="failed.glb",
            file_type=FileType.THREE_D,
            format=FileFormat.GLB,
            mime_type="model/gltf-binary",
            size_bytes=1024,
            sha256_checksum="c" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/23/raw.glb",
            status="failed",
            uploaded_by=self.owner_manager,
        )
        self.mesh_metadata_key = f"surveys/{self.survey.pk}/files/{self.mesh_file.pk}/model-metadata.json"
        self.point_metadata_key = f"surveys/{self.survey.pk}/files/{self.point_cloud_file.pk}/model-metadata.json"

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

    @patch("apps.models3d.services.PrivateR2StorageAdapter")
    def test_models_list_enforces_scope_ready_state_and_hides_private_paths(self, storage_factory):
        fake_storage = FakePrivateStorageAdapter(
            json_objects={
                self.mesh_metadata_key: json.dumps(
                    {
                        "display_format": "GLB",
                        "vertex_count": 6,
                        "bounding_box": {"min": [0, 0, 0], "max": [1, 1, 1]},
                        "crs": "EPSG:4978",
                    }
                ).encode("utf-8"),
                self.point_metadata_key: json.dumps(
                    {
                        "display_format": "POTREE",
                        "vertex_count": 1250,
                        "bounding_box": {"min": [10, 20, 30], "max": [40, 50, 60]},
                        "crs": "EPSG:32631",
                    }
                ).encode("utf-8"),
            }
        )
        storage_factory.return_value = fake_storage
        url = f"/api/v1/surveys/{self.survey.pk}/models"

        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(url)

            self.authenticate(self.outsider)
            denied = self.client.get(url)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        payload = allowed.json()
        self.assertEqual([item["id"] for item in payload], [self.mesh_file.pk, self.point_cloud_file.pk])
        self.assertEqual(payload[0]["viewer_source_type"], "glb")
        self.assertEqual(payload[1]["viewer_source_type"], "potree")
        self.assertEqual(payload[0]["display_format"], "GLB")
        self.assertEqual(payload[1]["display_format"], "POTREE")
        self.assertNotIn("storage_path", json.dumps(payload))
        self.assertNotIn("sha256_checksum", json.dumps(payload))
        self.assertEqual(
            fake_storage.presign_calls,
            [
                (self.mesh_file.converted_path, 300),
                (self.point_cloud_file.converted_path, 300),
            ],
        )
