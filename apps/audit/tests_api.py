from datetime import date, datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import record_audit_event
from apps.projects.models import Project, ProjectMembership, Site
from apps.surveys.models import Survey


class AuditLogApiTests(APITestCase):
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
            email="audit-admin@example.com",
            external_id="audit-admin-1",
            role=UserRole.ADMINISTRATOR,
        )
        self.owner_manager = User.objects.create_user(
            email="audit-manager@example.com",
            external_id="audit-manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.other_manager = User.objects.create_user(
            email="audit-other-manager@example.com",
            external_id="audit-manager-2",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="audit-engineer@example.com",
            external_id="audit-engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="audit-viewer@example.com",
            external_id="audit-viewer-1",
            role=UserRole.VIEWER,
        )
        self.outsider = User.objects.create_user(
            email="audit-outsider@example.com",
            external_id="audit-outsider-1",
            role=UserRole.VIEWER,
        )

        self.project = Project.objects.create(
            name="Audit Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.other_project = Project.objects.create(
            name="Other Audit Project",
            project_manager=self.other_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Audit Site",
            coordinates=Point(3.3792, 6.5244, srid=4326),
        )
        self.other_site = Site.objects.create(
            project=self.other_project,
            name="Other Audit Site",
            coordinates=Point(7.3792, 9.5244, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Audit Survey",
            survey_date=date(2026, 8, 10),
            created_by=self.engineer,
        )
        self.other_survey = Survey.objects.create(
            project=self.other_project,
            site=self.other_site,
            name="Other Audit Survey",
            survey_date=date(2026, 8, 10),
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

        self.project_log = self.create_audit(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=self.survey.pk,
            user=self.owner_manager,
            project=self.project,
            survey=self.survey,
            details={"status": "created"},
            timestamp=datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc),
        )
        self.measurement_log = self.create_audit(
            action=AuditAction.MEASUREMENT_CREATED,
            entity_type="measurement",
            entity_id=77,
            user=self.viewer,
            project=self.project,
            survey=self.survey,
            details={"name": "Measured boundary"},
            timestamp=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )
        self.other_project_log = self.create_audit(
            action=AuditAction.FILE_DOWNLOADED,
            entity_type="survey_file",
            entity_id=88,
            user=self.other_manager,
            project=self.other_project,
            survey=self.other_survey,
            details={"operation": "download"},
            timestamp=datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc),
        )
        self.sensitive_log = self.create_audit(
            action=AuditAction.FILE_UPLOADED,
            entity_type="survey_file",
            entity_id=99,
            user=self.owner_manager,
            project=self.project,
            survey=self.survey,
            details={
                "storage_path": "surveys/1/files/99/raw.tif",
                "sha256_checksum": "a" * 64,
                "presigned_url": "https://signed.example.invalid/file?X-Amz-Signature=secret",
                "objectKey": "surveys/1/files/99/raw.tif",
                "r2Key": "surveys/1/files/99/cog.tif",
                "storagePath": "surveys/1/files/99/preview.png",
                "signedUrl": "https://signed.example.invalid/file?X-Amz-Credential=secret",
                "download-url": "https://signed.example.invalid/file?X-Amz-Signature=secret",
                "authorization": "Bearer sensitive-access-token",
                "unexpected_field": "surveys/1/files/99/private-sidecar.json",
                "nested": {
                    "token": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
                    "download-url": "https://signed.example.invalid/file?X-Amz-Signature=secret",
                    "safe_label": "kept",
                },
            },
            timestamp=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
        )
        self.global_admin_log = self.create_audit(
            action=AuditAction.ADMIN_ACTION,
            entity_type="system",
            entity_id=1,
            user=self.admin,
            details={"operation": "configuration check"},
            timestamp=datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
        )

        self.list_url = "/api/v1/audit-logs"

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

    def create_audit(self, *, timestamp, **kwargs):
        audit_log = record_audit_event(**kwargs)
        AuditLog.objects.filter(pk=audit_log.pk).update(timestamp=timestamp)
        audit_log.refresh_from_db()
        return audit_log

    def test_audit_list_scope_matches_project_visibility_rules(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            admin_response = self.client.get(self.list_url)

            self.authenticate(self.owner_manager)
            manager_response = self.client.get(self.list_url)

            self.authenticate(self.engineer)
            engineer_response = self.client.get(self.list_url)

            self.authenticate(self.viewer)
            viewer_response = self.client.get(self.list_url)

            self.authenticate(self.outsider)
            outsider_response = self.client.get(self.list_url)

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(manager_response.status_code, 200)
        self.assertEqual(engineer_response.status_code, 200)
        self.assertEqual(viewer_response.status_code, 200)
        self.assertEqual(outsider_response.status_code, 200)
        self.assertEqual(admin_response.json()["count"], 5)
        self.assertEqual(manager_response.json()["count"], 3)
        self.assertEqual(engineer_response.json()["count"], 3)
        self.assertEqual(viewer_response.json()["count"], 3)
        self.assertEqual(outsider_response.json()["count"], 1)

    def test_audit_list_filters_pagination_and_newest_first_ordering(self):
        for index in range(25):
            self.create_audit(
                action=AuditAction.MEASUREMENT_CREATED,
                entity_type="measurement",
                entity_id=200 + index,
                user=self.engineer,
                project=self.project,
                survey=self.survey,
                details={"sequence": index},
                timestamp=datetime(2026, 8, 10, 0, index % 60, tzinfo=timezone.utc),
            )

        with self.auth_settings():
            self.authenticate(self.admin)
            filtered = self.client.get(
                f"{self.list_url}?project_id={self.project.pk}&survey_id={self.survey.pk}"
                "&action=MEASUREMENT_CREATED&from_date=2026-08-03&to_date=2026-08-10"
            )
            paged = self.client.get(f"{self.list_url}?limit=5&offset=10")
            invalid_action = self.client.get(f"{self.list_url}?action=measurement_created")
            invalid_unknown = self.client.get(f"{self.list_url}?user_id=1")

        self.assertEqual(filtered.status_code, 200)
        filtered_results = filtered.json()["results"]
        self.assertTrue(filtered_results)
        self.assertEqual(filtered_results[0]["action"], AuditAction.MEASUREMENT_CREATED)
        self.assertTrue(all(item["project_id"] == self.project.pk for item in filtered_results))
        self.assertTrue(all(item["survey_id"] == self.survey.pk for item in filtered_results))
        timestamps = [item["timestamp"] for item in filtered_results[:5]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        self.assertEqual(paged.status_code, 200)
        self.assertEqual(len(paged.json()["results"]), 5)
        self.assertEqual(paged.json()["count"], 30)
        self.assertEqual(invalid_action.status_code, 400)
        self.assertEqual(invalid_unknown.status_code, 400)

    def test_audit_detail_enforces_same_project_scope(self):
        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(f"{self.list_url}/{self.project_log.pk}")
            forbidden = self.client.get(f"{self.list_url}/{self.other_project_log.pk}")

            self.authenticate(self.outsider)
            outsider_forbidden = self.client.get(f"{self.list_url}/{self.project_log.pk}")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(outsider_forbidden.status_code, 403)

    def test_audit_serialization_hides_sensitive_storage_and_secret_details(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            response = self.client.get(f"{self.list_url}/{self.sensitive_log.pk}")

        self.assertEqual(response.status_code, 200)
        details = response.json()["details"]
        self.assertEqual(details["nested"]["safe_label"], "kept")
        self.assertNotIn("storage_path", details)
        self.assertNotIn("sha256_checksum", details)
        self.assertNotIn("presigned_url", details)
        self.assertNotIn("objectKey", details)
        self.assertNotIn("r2Key", details)
        self.assertNotIn("storagePath", details)
        self.assertNotIn("signedUrl", details)
        self.assertNotIn("download-url", details)
        self.assertNotIn("authorization", details)
        self.assertNotIn("unexpected_field", details)
        self.assertNotIn("token", details.get("nested", {}))
        self.assertNotIn("download-url", details.get("nested", {}))
        self.sensitive_log.refresh_from_db()
        self.assertIn("storage_path", self.sensitive_log.details)
        self.assertIn("sha256_checksum", self.sensitive_log.details)
        self.assertIn("objectKey", self.sensitive_log.details)
        self.assertIn("unexpected_field", self.sensitive_log.details)

    def test_audit_detail_representation_includes_timeline_fields(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager)
            response = self.client.get(f"{self.list_url}/{self.measurement_log.pk}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            list(body.keys()),
            [
                "id",
                "action",
                "entity_type",
                "entity_id",
                "project_id",
                "survey_id",
                "user_id",
                "details",
                "timestamp",
            ],
        )
        self.assertEqual(body["project_id"], self.project.pk)
        self.assertEqual(body["survey_id"], self.survey.pk)
        self.assertEqual(body["user_id"], self.viewer.pk)
