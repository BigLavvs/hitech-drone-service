from datetime import datetime, timedelta, timezone

import jwt
from config import settings as project_settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase, APIClient

from apps.access_control.models import UserRole


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "security-tests",
    }
}
class SecurityConfigurationTests(SimpleTestCase):
    def test_security_settings_are_derived_from_debug_mode(self):
        self.assertEqual(project_settings.CSRF_COOKIE_SECURE, not project_settings.DEBUG)
        self.assertEqual(project_settings.SESSION_COOKIE_SECURE, not project_settings.DEBUG)
        self.assertEqual(project_settings.SECURE_SSL_REDIRECT, not project_settings.DEBUG)
        self.assertFalse(settings.CSRF_COOKIE_HTTPONLY)

    def test_production_cache_uses_namespaced_shared_redis(self):
        cache_config = project_settings.CACHES["default"]

        self.assertEqual(
            cache_config["BACKEND"],
            "django.core.cache.backends.redis.RedisCache",
        )
        self.assertTrue(cache_config["LOCATION"].startswith("redis://"))
        self.assertTrue(cache_config["KEY_PREFIX"].startswith("hitech-drone-mapping:"))

    def test_security_middleware_redirects_and_accepts_forwarded_https(self):
        with override_settings(DEBUG=False, SECURE_SSL_REDIRECT=True):
            redirected = self.client.get("/")
            forwarded = self.client.get("/", HTTP_X_FORWARDED_PROTO="https")

        self.assertEqual(redirected.status_code, 301)
        self.assertEqual(forwarded.status_code, 200)

    @override_settings(
        SECURE_SSL_REDIRECT=False,
        R2_ENDPOINT_URL="https://account-id.r2.cloudflarestorage.com",
        R2_PUBLIC_URL="",
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    def test_csp_header_contains_nonce_and_current_allowed_resource_hosts(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        csp = response["Content-Security-Policy"]
        body = response.content.decode("utf-8")

        self.assertIn("script-src 'self' https://cdn.jsdelivr.net 'nonce-", csp)
        self.assertIn("script-src-elem 'self' https://cdn.jsdelivr.net 'nonce-", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("script-src", 1)[1].split(";", 1)[0])
        response_nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
        style_policy = next(
            directive for directive in csp.split("; ") if directive.startswith("style-src-elem ")
        )
        self.assertIn(f"'nonce-{response_nonce}'", style_policy)
        self.assertNotIn("'unsafe-inline'", style_policy)
        self.assertIn("https://account-id.r2.cloudflarestorage.com", csp)
        self.assertIn('type="importmap" nonce="', body)
        self.assertIn("https://cdn.jsdelivr.net/npm/three@0.167.1/build/three.module.js", body)

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_api_documentation_inline_blocks_receive_the_response_nonce(self):
        swagger = self.client.get("/docs")
        redoc = self.client.get("/docs/redoc")

        self.assertEqual(swagger.status_code, 200)
        self.assertEqual(redoc.status_code, 200)
        for response in (swagger, redoc):
            body = response.content.decode("utf-8")
            nonce = response["Content-Security-Policy"].split("'nonce-", 1)[1].split("'", 1)[0]
            self.assertIn(f'nonce="{nonce}"', body)
        self.assertIn("Redoc.init", redoc.content.decode("utf-8"))
        self.assertIn("SwaggerUIBundle", swagger.content.decode("utf-8"))


class RateLimitSecurityTests(APITestCase):
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
        cache.clear()
        self.user = get_user_model().objects.create_user(
            email="viewer-security@example.com",
            external_id="viewer-security",
            role=UserRole.VIEWER,
        )

    def make_token(self):
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": self.user.external_id,
                "email": self.user.email,
                "role": self.user.role,
                "exp": now + timedelta(minutes=15),
            },
            self.private_key_pem,
            algorithm="RS256",
        )

    @override_settings(
        CACHES=TEST_CACHES,
        RATE_LIMIT_GENERAL="1/m",
        HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
    )
    def test_general_rate_limit_applies_to_authenticated_api_endpoints(self):
        with override_settings(HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem):
            self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = self.make_token()
            first = self.client.get("/api/v1/auth/validate")
            second = self.client.get("/api/v1/auth/validate")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    @override_settings(
        CACHES=TEST_CACHES,
        ENABLE_DEMO_AUTH=True,
        RATE_LIMIT_LOGIN="1/m",
        SECURE_SSL_REDIRECT=False,
    )
    def test_demo_session_route_uses_login_specific_rate_limit(self):
        client = APIClient(enforce_csrf_checks=True)
        csrf_response = client.get("/login")
        csrf_token = csrf_response.cookies["csrftoken"].value

        first = client.post(
            "/api/v1/demo-auth/session",
            {"role": "not-a-role"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        second = client.post(
            "/api/v1/demo-auth/session",
            {"role": "not-a-role"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 429)

    @override_settings(
        CACHES=TEST_CACHES,
        RATE_LIMIT_GENERAL="1/m",
        SECURE_SSL_REDIRECT=False,
    )
    def test_public_health_and_docs_routes_are_not_throttled(self):
        for path in ("/health", "/api/schema", "/docs", "/docs/redoc"):
            self.client.get(path)
            second = self.client.get(path)
            self.assertNotEqual(second.status_code, 429)
