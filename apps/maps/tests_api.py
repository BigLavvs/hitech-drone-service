import json
from decimal import Decimal
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
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.maps.models import Measurement
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


class MeasurementApiTests(APITestCase):
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
            email="measure-admin@example.com",
            external_id="measure-admin-1",
            role=UserRole.ADMINISTRATOR,
        )
        self.owner_manager = User.objects.create_user(
            email="measure-manager@example.com",
            external_id="measure-manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.other_manager = User.objects.create_user(
            email="measure-other-manager@example.com",
            external_id="measure-manager-2",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="measure-engineer@example.com",
            external_id="measure-engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="measure-viewer@example.com",
            external_id="measure-viewer-1",
            role=UserRole.VIEWER,
        )
        self.outsider = User.objects.create_user(
            email="measure-outsider@example.com",
            external_id="measure-outsider-1",
            role=UserRole.SURVEY_ENGINEER,
        )

        self.project = Project.objects.create(
            name="Measurement Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.other_project = Project.objects.create(
            name="Other Measurement Project",
            project_manager=self.other_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Measurement Site",
            coordinates=Point(3.3792, 6.5244, srid=4326),
        )
        self.other_site = Site.objects.create(
            project=self.other_project,
            name="Other Site",
            coordinates=Point(7.3792, 9.5244, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Measurement Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.READY,
            processing_status="completed",
            created_by=self.engineer,
        )
        self.other_survey = Survey.objects.create(
            project=self.other_project,
            site=self.other_site,
            name="Other Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.READY,
            processing_status="completed",
            created_by=self.other_manager,
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
        ProjectMembership.objects.create(
            project=self.other_project,
            user=self.outsider,
            assigned_by=self.other_manager,
        )

        self.list_url = f"/api/v1/surveys/{self.survey.pk}/measurements"

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

    def add_csrf(self, path="/surveys/1"):
        response = self.client.get(path)
        token = response.cookies["csrftoken"].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        return token

    def test_measurement_read_and_create_permissions_follow_project_scope(self):
        Measurement.objects.create(
            survey=self.survey,
            type="DISTANCE",
            name="Existing measurement",
            coordinates=[[3.3792, 6.5244], [3.3801, 6.5250]],
            calculated_value=Decimal("100.00000000"),
            unit="m",
            created_by=self.engineer,
        )

        payload = {
            "type": "DISTANCE",
            "name": "Boundary check",
            "coordinates": [[3.3792, 6.5244], [3.3801, 6.5250]],
        }

        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            admin_list = self.client.get(self.list_url)
            admin_create = self.client.post(self.list_url, payload, format="json")

            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            pm_list = self.client.get(self.list_url)
            pm_create = self.client.post(self.list_url, payload, format="json")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            engineer_list = self.client.get(self.list_url)
            engineer_create = self.client.post(self.list_url, payload, format="json")

            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_list = self.client.get(self.list_url)
            viewer_create = self.client.post(self.list_url, payload, format="json")

            self.authenticate(self.outsider, enforce_csrf_checks=True)
            self.add_csrf()
            outsider_list = self.client.get(self.list_url)
            outsider_create = self.client.post(self.list_url, payload, format="json")

        self.assertEqual(admin_list.status_code, 200)
        self.assertEqual(pm_list.status_code, 200)
        self.assertEqual(engineer_list.status_code, 200)
        self.assertEqual(viewer_list.status_code, 200)
        self.assertEqual(outsider_list.status_code, 403)
        self.assertEqual(admin_create.status_code, 201)
        self.assertEqual(pm_create.status_code, 201)
        self.assertEqual(engineer_create.status_code, 201)
        self.assertEqual(viewer_create.status_code, 201)
        self.assertEqual(outsider_create.status_code, 403)
        self.assertEqual(Measurement.objects.filter(survey=self.survey).count(), 5)

    def test_measurement_coordinate_and_type_validation_is_strict(self):
        with self.auth_settings():
            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            invalid_type = self.client.post(
                self.list_url,
                {"type": "distance", "name": "Bad", "coordinates": [[3.3, 6.5], [3.4, 6.6]]},
                format="json",
            )
            too_short_distance = self.client.post(
                self.list_url,
                {"type": "DISTANCE", "name": "Bad", "coordinates": [[3.3, 6.5]]},
                format="json",
            )
            too_short_area = self.client.post(
                self.list_url,
                {"type": "AREA", "name": "Bad", "coordinates": [[3.3, 6.5], [3.4, 6.6]]},
                format="json",
            )
            malformed_coordinates = self.client.post(
                self.list_url,
                {"type": "DISTANCE", "name": "Bad", "coordinates": [{"lng": 3.3, "lat": 6.5}]},
                format="json",
            )
            invalid_longitude = self.client.post(
                self.list_url,
                {"type": "DISTANCE", "name": "Bad", "coordinates": [[181, 6.5], [3.4, 6.6]]},
                format="json",
            )
            non_numeric = self.client.post(
                self.list_url,
                {"type": "DISTANCE", "name": "Bad", "coordinates": [["3.3", 6.5], [3.4, 6.6]]},
                format="json",
            )
            non_finite = self.client.post(
                self.list_url,
                '{"type":"DISTANCE","name":"Bad","coordinates":[[NaN,6.5],[3.4,6.6]]}',
                content_type="application/json",
            )

        for response in (
            invalid_type,
            too_short_distance,
            too_short_area,
            malformed_coordinates,
            invalid_longitude,
            non_numeric,
            non_finite,
        ):
            self.assertEqual(response.status_code, 400)

    def test_measurement_values_are_calculated_server_side_and_client_overrides_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            rejected_override = self.client.post(
                self.list_url,
                {
                    "type": "DISTANCE",
                    "name": "Override attempt",
                    "coordinates": [[0, 0], [0, 1]],
                    "calculated_value": "12.34",
                    "unit": "km",
                    "survey_id": self.other_survey.pk,
                },
                format="json",
            )
            distance_response = self.client.post(
                self.list_url,
                {
                    "type": "DISTANCE",
                    "name": "Meridian segment",
                    "coordinates": [[0, 0], [0, 1]],
                },
                format="json",
            )
            area_response = self.client.post(
                self.list_url,
                {
                    "type": "AREA",
                    "name": "One degree square",
                    "coordinates": [[0, 0], [1, 0], [1, 1], [0, 1]],
                },
                format="json",
            )

        self.assertEqual(rejected_override.status_code, 400)
        self.assertEqual(distance_response.status_code, 201)
        self.assertEqual(area_response.status_code, 201)
        self.assertEqual(distance_response.json()["unit"], "m")
        self.assertEqual(area_response.json()["unit"], "m²")
        self.assertAlmostEqual(
            float(distance_response.json()["calculated_value"]),
            111195.08,
            places=1,
        )
        self.assertGreater(float(area_response.json()["calculated_value"]), 1_000_000)

    def test_measurement_create_and_delete_write_expected_audit_records(self):
        with self.auth_settings():
            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            create_response = self.client.post(
                self.list_url,
                {
                    "type": "DISTANCE",
                    "name": "Viewer measurement",
                    "coordinates": [[3.3792, 6.5244], [3.3801, 6.5250]],
                },
                format="json",
            )

            measurement_id = create_response.json()["id"]
            detail_url = f"{self.list_url}/{measurement_id}"

            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            delete_response = self.client.delete(detail_url)

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Measurement.objects.filter(pk=measurement_id).exists())
        audit_logs = list(AuditLog.objects.order_by("id"))
        self.assertEqual([log.action for log in audit_logs], [
            AuditAction.MEASUREMENT_CREATED,
            AuditAction.MEASUREMENT_DELETED,
        ])
        self.assertEqual(audit_logs[0].entity_type, "measurement")
        self.assertEqual(audit_logs[0].entity_id, measurement_id)
        self.assertEqual(audit_logs[0].project, self.project)
        self.assertEqual(audit_logs[0].survey, self.survey)
        self.assertEqual(audit_logs[0].user, self.viewer)
        self.assertEqual(audit_logs[1].user, self.owner_manager)

    def test_measurement_delete_is_restricted_to_admin_and_owning_project_manager(self):
        measurement = Measurement.objects.create(
            survey=self.survey,
            type="DISTANCE",
            name="Protected measurement",
            coordinates=[[3.3792, 6.5244], [3.3801, 6.5250]],
            calculated_value=Decimal("100.00000000"),
            unit="m",
            created_by=self.engineer,
        )
        detail_url = f"{self.list_url}/{measurement.pk}"

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            engineer_response = self.client.delete(detail_url)

            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_response = self.client.delete(detail_url)

            self.authenticate(self.other_manager, enforce_csrf_checks=True)
            self.add_csrf()
            other_manager_response = self.client.delete(detail_url)

            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            admin_response = self.client.delete(detail_url)

        self.assertEqual(engineer_response.status_code, 403)
        self.assertEqual(viewer_response.status_code, 403)
        self.assertEqual(other_manager_response.status_code, 403)
        self.assertEqual(admin_response.status_code, 204)

    def test_measurement_detail_enforces_parent_survey_scope(self):
        measurement = Measurement.objects.create(
            survey=self.survey,
            type="DISTANCE",
            name="Scoped measurement",
            coordinates=[[3.3792, 6.5244], [3.3801, 6.5250]],
            calculated_value=Decimal("100.00000000"),
            unit="m",
            created_by=self.engineer,
        )

        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(f"{self.list_url}/{measurement.pk}")
            forbidden_parent = self.client.get(
                f"/api/v1/surveys/{self.other_survey.pk}/measurements/{measurement.pk}"
            )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(forbidden_parent.status_code, 403)
