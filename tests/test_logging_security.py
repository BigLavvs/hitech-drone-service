import json
import logging
from io import StringIO

from django.conf import settings
from django.test import SimpleTestCase

from config.logging import JsonLogFormatter


class JsonLoggingSecurityTests(SimpleTestCase):
    def test_django_and_celery_loggers_use_sanitizing_json_handler(self):
        self.assertEqual(settings.LOGGING["handlers"]["console"]["formatter"], "json")
        self.assertEqual(settings.LOGGING["loggers"]["django"]["handlers"], ["console"])
        self.assertEqual(settings.LOGGING["loggers"]["celery"]["handlers"], ["console"])

    def format_record(self, message, *args, **extra):
        record = logging.getLogger("test.security").makeRecord(
            "test.security",
            logging.ERROR,
            __file__,
            1,
            message,
            args,
            None,
            extra=extra,
        )
        return json.loads(JsonLogFormatter().format(record))

    def test_messages_and_nested_context_redact_secrets(self):
        payload = self.format_record(
            "provider failure password=%s url=%s",
            "synthetic-password",
            "https://storage.invalid/object?X-Amz-Signature=synthetic-signature",
            request={"method": "GET", "headers": {"Authorization": "Bearer synthetic-token"}},
            nested={"safe_event": "upload_failed", "token": "synthetic-token"},
        )

        rendered = json.dumps(payload)
        self.assertNotIn("synthetic-password", rendered)
        self.assertNotIn("synthetic-signature", rendered)
        self.assertNotIn("synthetic-token", rendered)
        self.assertEqual(payload["nested"]["safe_event"], "upload_failed")
        self.assertEqual(payload["request"]["method"], "GET")
        self.assertEqual(payload["request"]["headers"]["Authorization"], "[REDACTED]")

    def test_interpolated_exception_cookie_and_database_url_are_redacted(self):
        payload = self.format_record(
            "conversion failed: %s; Cookie: csrftoken=FIRST_COOKIE; hitech_access_token=SECOND_COOKIE; db=postgresql://demo:SYNTHETIC_DB_PASSWORD@db.invalid/demo",
            ValueError('{"password": "SYNTHETIC_PASSWORD"}'),
        )

        rendered = json.dumps(payload)
        for secret in (
            "SYNTHETIC_PASSWORD",
            "FIRST_COOKIE",
            "SECOND_COOKIE",
            "SYNTHETIC_DB_PASSWORD",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("ValueError", payload["message"])

    def test_configured_django_and_celery_loggers_use_sanitizing_formatter(self):
        configured_handler = logging.getLogger("django").handlers[0]
        self.assertIsInstance(configured_handler.formatter, JsonLogFormatter)
        original_stream = configured_handler.stream
        for logger_name in ("django.request", "celery.app.trace"):
            stream = StringIO()
            configured_handler.setStream(stream)
            try:
                logging.getLogger(logger_name).error(
                    "task failed with %s",
                    ValueError('{"password": "CONFIGURED_PASSWORD"}'),
                    extra={
                        "Cookie": "csrftoken=CONFIGURED_COOKIE; hitech_access_token=CONFIGURED_TOKEN",
                        "database_url": "postgresql://demo:CONFIGURED_DB_PASSWORD@db.invalid/demo",
                    },
                )
                rendered = stream.getvalue()
            finally:
                configured_handler.setStream(original_stream)

            self.assertNotIn("CONFIGURED_PASSWORD", rendered)
            self.assertNotIn("CONFIGURED_COOKIE", rendered)
            self.assertNotIn("CONFIGURED_TOKEN", rendered)
            self.assertNotIn("CONFIGURED_DB_PASSWORD", rendered)
            self.assertTrue(json.loads(rendered)["message"])

    def test_request_objects_and_exception_context_are_not_stringified(self):
        class FakeRequest:
            META = {"HTTP_COOKIE": "synthetic-cookie"}
            COOKIES = {"session": "synthetic-session"}

            def __str__(self):
                return "request cookie=synthetic-cookie"

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("django.request")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            try:
                raise RuntimeError("provider failed token=synthetic-token")
            except RuntimeError:
                logger.exception("database failure signed_url=https://storage.invalid/a?sig=synthetic", extra={"request": FakeRequest()})
        finally:
            logger.removeHandler(handler)

        output = stream.getvalue()
        self.assertNotIn("synthetic-token", output)
        self.assertNotIn("synthetic", output)
        payload = json.loads(output)
        self.assertEqual(payload["exception"], "RuntimeError")
        self.assertEqual(payload["request"]["type"], "FakeRequest")
