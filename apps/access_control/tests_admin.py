from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.projects.models import Project


class AuthenticatedCookieMixin:
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

    def auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem,
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
        )

    def make_token(self, user: User):
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

    def authenticate(self, user: User):
        self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = self.make_token(user)


class UserManagementApiTests(AuthenticatedCookieMixin, APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@example.com",
            external_id="admin-ext-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.viewer = User.objects.create_user(
            email="viewer@example.com",
            external_id="viewer-ext-1",
            role=UserRole.VIEWER,
        )
        self.project_manager = User.objects.create_user(
            email="pm@example.com",
            external_id="pm-ext-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.list_url = "/api/v1/users"

    def test_admin_can_list_create_and_update_users_with_audit_records(self):
        with self.auth_settings():
            self.authenticate(self.admin)

            list_response = self.client.get(self.list_url)
            create_response = self.client.post(
                self.list_url,
                {
                    "email": "engineer@example.com",
                    "external_id": "engineer-ext-1",
                    "role": "SURVEY_ENGINEER",
                    "is_active": True,
                },
                format="json",
            )

            created_user_id = create_response.json()["id"]
            patch_response = self.client.patch(
                f"/api/v1/users/{created_user_id}",
                {"role": "VIEWER", "is_active": False, "email": "viewerized@example.com"},
                format="json",
            )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(create_response.json()["external_id"], "engineer-ext-1")
        self.assertEqual(patch_response.json()["role"], "VIEWER")
        self.assertFalse(patch_response.json()["is_active"])

        audits = list(AuditLog.objects.filter(action=AuditAction.ADMIN_ACTION).order_by("id"))
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0].entity_type, "user")
        self.assertEqual(audits[0].details["operation"], "user_created")
        self.assertEqual(audits[1].details["operation"], "user_updated")
        self.assertEqual(audits[1].details["changes"]["role"], "VIEWER")

    def test_user_management_rejects_non_admin_unknown_fields_and_external_id_patch(self):
        target_user = User.objects.create_user(
            email="target@example.com",
            external_id="target-ext-1",
            role=UserRole.VIEWER,
        )

        with self.auth_settings():
            self.authenticate(self.viewer)
            forbidden = self.client.get(self.list_url)

            self.authenticate(self.admin)
            unknown = self.client.post(
                self.list_url,
                {
                    "email": "bad@example.com",
                    "external_id": "bad-ext-1",
                    "role": "VIEWER",
                    "mystery": "nope",
                },
                format="json",
            )
            immutable = self.client.patch(
                f"/api/v1/users/{target_user.pk}",
                {"external_id": "changed-ext"},
                format="json",
            )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(immutable.status_code, 400)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_role_or_deactivation_change_is_blocked_while_user_owns_projects(self):
        Project.objects.create(
            name="Owned Project",
            project_manager=self.project_manager,
            created_by=self.admin,
        )

        with self.auth_settings():
            self.authenticate(self.admin)
            role_response = self.client.patch(
                f"/api/v1/users/{self.project_manager.pk}",
                {"role": "VIEWER"},
                format="json",
            )
            active_response = self.client.patch(
                f"/api/v1/users/{self.project_manager.pk}",
                {"is_active": False},
                format="json",
            )

        self.assertEqual(role_response.status_code, 400)
        self.assertEqual(active_response.status_code, 400)
        self.assertEqual(AuditLog.objects.count(), 0)


class AdminPageProtectionTests(AuthenticatedCookieMixin, APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin-page@example.com",
            external_id="admin-page-ext-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.viewer = User.objects.create_user(
            email="viewer-page@example.com",
            external_id="viewer-page-ext-1",
            role=UserRole.VIEWER,
        )

    def test_admin_page_redirects_unauthenticated_and_non_admin_users(self):
        with self.auth_settings():
            unauthenticated = self.client.get("/admin")
            self.authenticate(self.viewer)
            viewer_response = self.client.get("/admin")

        self.assertEqual(unauthenticated.status_code, 302)
        self.assertEqual(unauthenticated.headers["Location"], "/login")
        self.assertEqual(viewer_response.status_code, 302)
        self.assertEqual(viewer_response.headers["Location"], "/projects")

    def test_admin_page_renders_for_administrator(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User Management")


class PublicSchemaTests(AuthenticatedCookieMixin, APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="schema-admin@example.com",
            external_id="schema-admin-ext-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )

    def test_schema_endpoint_is_public_and_documents_user_and_health_routes(self):
        with self.auth_settings():
            response = self.client.get("/api/schema?format=json")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("/api/v1/users", schema["paths"])
        self.assertIn("/health", schema["paths"])
        self.assertIn("/ready", schema["paths"])
        self.assertIn("HitechJWTCookieAuth", schema["components"]["securitySchemes"])
