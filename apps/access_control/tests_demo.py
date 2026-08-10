from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import jwt
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.access_control.demo_access import DEMO_USER_SPECS
from apps.access_control.models import UserRole
from apps.projects.models import ProjectMembership


class DemoAssessmentCommandsTests(TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.private_key_path = Path(self.tempdir.name) / "private_key.pem"
        self.public_key_path = Path(self.tempdir.name) / "public_key.pem"

    def tearDown(self):
        self.tempdir.cleanup()

    @override_settings(DEBUG=True, ENABLE_DEMO_AUTH=False)
    def test_demo_commands_are_blocked_without_explicit_flag(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo_assessment")

        with self.assertRaises(CommandError):
            call_command("init_demo_auth_keys")

    @override_settings(
        DEBUG=True,
        ENABLE_DEMO_AUTH=True,
        DEMO_AUTH_PRIVATE_KEY_PATH="C:/tmp/demo-private.pem",
        DEMO_AUTH_PUBLIC_KEY_PATH="C:/tmp/demo-public.pem",
    )
    def test_seed_command_creates_documented_users_roles_and_relationships(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")

            admin = self._user_for("administrator")
            manager = self._user_for("project_manager")
            engineer = self._user_for("survey_engineer")
            viewer = self._user_for("viewer")

            project = manager.projects_owned.get(name="Assessment Demo Project")
            site = project.sites.get(name="Assessment Demo Site")
            survey = site.surveys.get(name="Assessment Demo Survey")

            self.assertEqual(admin.role, UserRole.ADMINISTRATOR)
            self.assertEqual(manager.role, UserRole.PROJECT_MANAGER)
            self.assertEqual(engineer.role, UserRole.SURVEY_ENGINEER)
            self.assertEqual(viewer.role, UserRole.VIEWER)
            self.assertEqual(project.created_by, admin)
            self.assertEqual(project.project_manager, manager)
            self.assertTrue(ProjectMembership.objects.filter(project=project, user=engineer).exists())
            self.assertTrue(ProjectMembership.objects.filter(project=project, user=viewer).exists())
            self.assertEqual(survey.created_by, engineer)
            self.assertEqual(survey.status, "DRAFT")
            self.assertEqual(survey.processing_status, "pending")
            self.assertTrue(survey.measurements.filter(name="Assessment demo boundary").exists())

            call_command("seed_demo_assessment")
            self.assertEqual(ProjectMembership.objects.filter(project=project, user=engineer).count(), 1)
            self.assertEqual(ProjectMembership.objects.filter(project=project, user=viewer).count(), 1)

    @override_settings(
        DEBUG=True,
        ENABLE_DEMO_AUTH=True,
        DEMO_AUTH_TOKEN_TTL_SECONDS=900,
    )
    def test_issue_demo_token_signs_expected_claims(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")

            output = []
            call_command("issue_demo_token", "--role", "viewer", stdout=self._collector(output))
            token = "".join(output).strip()
            claims = jwt.decode(
                token,
                key=self.public_key_path.read_text(encoding="utf-8"),
                algorithms=["RS256"],
            )
            spec = DEMO_USER_SPECS["viewer"]
            self.assertEqual(claims["sub"], spec.external_id)
            self.assertEqual(claims["email"], spec.email)
            self.assertEqual(claims["role"], spec.role)

    @override_settings(
        DEBUG=True,
        ENABLE_DEMO_AUTH=True,
        DEMO_AUTH_TOKEN_TTL_SECONDS=900,
    )
    def test_issue_demo_token_uses_env_private_key_with_escaped_newlines(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")
            env_private_key = (
                self.private_key_path.read_text(encoding="utf-8").replace("\n", "\\n")
            )

        with override_settings(
            DEMO_AUTH_PRIVATE_KEY=env_private_key,
            DEMO_AUTH_PRIVATE_KEY_PATH=str(Path(self.tempdir.name) / "missing.pem"),
        ):
            output = []
            call_command("issue_demo_token", "--role", "viewer", stdout=self._collector(output))
            token = "".join(output).strip()

        claims = jwt.decode(
            token,
            key=self.public_key_path.read_text(encoding="utf-8"),
            algorithms=["RS256"],
        )
        self.assertEqual(claims["sub"], DEMO_USER_SPECS["viewer"].external_id)

    @override_settings(
        DEBUG=True,
        ENABLE_DEMO_AUTH=True,
        DEMO_AUTH_TOKEN_TTL_SECONDS=900,
    )
    def test_env_private_key_takes_precedence_over_local_key_path(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")
            env_private_key = self.private_key_path.read_text(encoding="utf-8")

        with override_settings(
            DEMO_AUTH_PRIVATE_KEY=env_private_key,
            DEMO_AUTH_PRIVATE_KEY_PATH=str(Path(self.tempdir.name) / "does-not-exist.pem"),
        ):
            output = []
            call_command("issue_demo_token", "--role", "administrator", stdout=self._collector(output))
            token = "".join(output).strip()

        claims = jwt.decode(
            token,
            key=self.public_key_path.read_text(encoding="utf-8"),
            algorithms=["RS256"],
        )
        self.assertEqual(claims["sub"], DEMO_USER_SPECS["administrator"].external_id)

    def _user_for(self, key):
        spec = DEMO_USER_SPECS[key]
        return get_user_model().objects.get(external_id=spec.external_id)

    def _collector(self, buffer):
        class Collector:
            def write(self, value):
                buffer.append(value)

        return Collector()


class DemoSessionApiTests(TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.private_key_path = Path(self.tempdir.name) / "private_key.pem"
        self.public_key_path = Path(self.tempdir.name) / "public_key.pem"
        self.url = "/api/v1/demo-auth/session"
        self.client = APIClient(enforce_csrf_checks=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def _issue_csrf_cookie(self):
        self.client.get("/login")
        return self.client.cookies["csrftoken"].value

    @override_settings(ENABLE_DEMO_AUTH=False)
    def test_demo_session_endpoint_is_disabled_when_demo_auth_is_off(self):
        response = self.client.post(self.url, {"role": "viewer"}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("hitech_access_token", response.cookies)

    @override_settings(
        ENABLE_DEMO_AUTH=True,
        DEBUG=False,
        DEMO_AUTH_TOKEN_TTL_SECONDS=900,
        HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
    )
    def test_valid_role_sets_secure_cookie_in_deployed_mode_and_returns_redirect_target(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")
            csrf_token = self._issue_csrf_cookie()

            response = self.client.post(
                self.url,
                {"role": "viewer"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"redirect_to": "/projects"})
        self.assertIn("hitech_access_token", response.cookies)
        cookie = response.cookies["hitech_access_token"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/")
        self.assertNotIn("token", response.content.decode("utf-8"))

    @override_settings(
        ENABLE_DEMO_AUTH=True,
        DEBUG=True,
        DEMO_AUTH_TOKEN_TTL_SECONDS=900,
        HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
    )
    def test_valid_role_keeps_cookie_usable_without_secure_flag_in_local_debug_mode(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")
            csrf_token = self._issue_csrf_cookie()

            response = self.client.post(
                self.url,
                {"role": "viewer"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies["hitech_access_token"]["secure"])

    @override_settings(
        ENABLE_DEMO_AUTH=True,
        HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
    )
    def test_invalid_role_or_missing_seeded_user_is_rejected_safely(self):
        with override_settings(
            DEMO_AUTH_PRIVATE_KEY_PATH=str(self.private_key_path),
            DEMO_AUTH_PUBLIC_KEY_PATH=str(self.public_key_path),
        ):
            call_command("init_demo_auth_keys")
            call_command("seed_demo_assessment")
            csrf_token = self._issue_csrf_cookie()

            invalid_role_response = self.client.post(
                self.url,
                {"role": "not-a-role"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.assertEqual(invalid_role_response.status_code, 400)
            self.assertNotIn("hitech_access_token", invalid_role_response.cookies)

            get_user_model().objects.filter(
                external_id=DEMO_USER_SPECS["viewer"].external_id
            ).delete()

            missing_user_response = self.client.post(
                self.url,
                {"role": "viewer"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )

        self.assertEqual(missing_user_response.status_code, 400)
        self.assertEqual(
            missing_user_response.json(),
            {"role": ["The selected demo role is unavailable in this environment."]},
        )
        self.assertNotIn("hitech_access_token", missing_user_response.cookies)

    @override_settings(ENABLE_DEMO_AUTH=True)
    def test_demo_session_endpoint_enforces_csrf(self):
        response = self.client.post(self.url, {"role": "viewer"}, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("hitech_access_token", response.cookies)


class LoginPageDemoAccessTests(SimpleTestCase):
    @override_settings(
        ENABLE_DEMO_AUTH=True,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        },
    )
    def test_login_page_shows_assessment_demo_access_when_enabled(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assessment demo access")
        self.assertContains(response, "Access assessment demo")

    @override_settings(
        ENABLE_DEMO_AUTH=False,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        },
    )
    def test_login_page_hides_assessment_demo_access_when_disabled(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Assessment demo access")
        self.assertNotContains(response, "Access assessment demo")
