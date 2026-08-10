from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import UserRole


class UserManagerTests(TestCase):
    @property
    def User(self):
        return get_user_model()

    def test_create_user_sets_unusable_password(self):
        user = self.User.objects.create_user(
            email="engineer@example.com",
            external_id="auth-123",
            role=UserRole.SURVEY_ENGINEER,
        )

        self.assertFalse(user.has_usable_password())

    def test_create_user_requires_email(self):
        with self.assertRaisesMessage(ValueError, "Email is required"):
            self.User.objects.create_user(
                email="",
                external_id="auth-123",
                role=UserRole.VIEWER,
            )

    def test_create_user_requires_external_id(self):
        with self.assertRaisesMessage(ValueError, "External ID is required"):
            self.User.objects.create_user(
                email="viewer@example.com",
                external_id="",
                role=UserRole.VIEWER,
            )

    def test_create_superuser_creates_administrator_staff_user(self):
        user = self.User.objects.create_superuser(
            email="admin@example.com",
            external_id="auth-admin",
        )

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)
        self.assertEqual(user.role, UserRole.ADMINISTRATOR)
        self.assertFalse(hasattr(user, "is_superuser"))


class AuthValidateApiTests(APITestCase):
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
        self.user = get_user_model().objects.create_user(
            email="viewer@example.com",
            external_id="auth-user-1",
            role=UserRole.VIEWER,
        )
        self.url = "/api/v1/auth/validate"

    def auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem,
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
        )

    def escaped_pem_auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem.replace("\n", "\\n"),
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
        )

    def make_token(self, **overrides):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": self.user.external_id,
            "email": self.user.email,
            "role": self.user.role,
            "exp": now + timedelta(minutes=15),
        }
        payload.update(overrides)
        return jwt.encode(payload, self.private_key_pem, algorithm="RS256")

    def get_with_cookie(self, token):
        self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = token
        return self.client.get(self.url)

    def test_valid_cookie_jwt_succeeds_and_returns_documented_payload(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "authenticated": True,
                "user": {
                    "id": self.user.id,
                    "external_id": self.user.external_id,
                    "email": self.user.email,
                    "role": self.user.role,
                },
            },
        )

    def test_valid_cookie_jwt_succeeds_with_escaped_pem_public_key_setting(self):
        with self.escaped_pem_auth_settings():
            response = self.get_with_cookie(self.make_token())

        self.assertEqual(response.status_code, 200)

    def test_valid_jwt_refreshes_changed_cached_email_and_role_values(self):
        self.user.email = "stale@example.com"
        self.user.role = UserRole.SURVEY_ENGINEER
        self.user.save(update_fields=["email", "role", "updated_at"])

        with self.auth_settings():
            response = self.get_with_cookie(
                self.make_token(email="viewer@example.com", role=UserRole.VIEWER)
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "viewer@example.com")
        self.assertEqual(self.user.role, UserRole.VIEWER)

    def test_missing_cookie_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)

    def test_expired_token_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.get_with_cookie(
                self.make_token(exp=datetime.now(timezone.utc) - timedelta(minutes=1))
            )

        self.assertEqual(response.status_code, 401)

    def test_missing_sub_claim_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token(sub=None))

        self.assertEqual(response.status_code, 401)

    def test_blank_sub_claim_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token(sub=""))

        self.assertEqual(response.status_code, 401)

    def test_invalid_email_claim_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token(email="not-an-email"))

        self.assertEqual(response.status_code, 401)

    def test_unsupported_role_claim_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token(role="NOT_A_SUPPORTED_ROLE"))

        self.assertEqual(response.status_code, 401)

    def test_invalid_signature_or_algorithm_is_rejected_with_401(self):
        invalid_signature_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        invalid_signature_token = jwt.encode(
            {
                "sub": self.user.external_id,
                "email": self.user.email,
                "role": self.user.role,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
            },
            invalid_signature_private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8"),
            algorithm="RS256",
        )
        invalid_algorithm_token = jwt.encode(
            {
                "sub": self.user.external_id,
                "email": self.user.email,
                "role": self.user.role,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
            },
            "shared-secret-for-invalid-algorithm-test",
            algorithm="HS256",
        )

        with self.auth_settings():
            for token in (invalid_signature_token, invalid_algorithm_token):
                response = self.get_with_cookie(token)
                self.assertEqual(response.status_code, 401)

    def test_unknown_external_user_is_rejected_with_401(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token(sub="unknown-auth-user"))

        self.assertEqual(response.status_code, 401)

    def test_inactive_local_user_is_rejected_with_401(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        with self.auth_settings():
            response = self.get_with_cookie(self.make_token())

        self.assertEqual(response.status_code, 401)

    def test_validation_endpoint_does_not_issue_tokens_or_sessions_and_keeps_unusable_password(self):
        with self.auth_settings():
            response = self.get_with_cookie(self.make_token())

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
        self.assertNotIn("token", response.json())
        self.assertNotIn("password", response.json()["user"])
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertIsNone(response.wsgi_request.session.session_key)
