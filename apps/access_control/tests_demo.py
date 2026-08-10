from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import jwt
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

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

    def _user_for(self, key):
        spec = DEMO_USER_SPECS[key]
        return get_user_model().objects.get(external_id=spec.external_id)

    def _collector(self, buffer):
        class Collector:
            def write(self, value):
                buffer.append(value)

        return Collector()
