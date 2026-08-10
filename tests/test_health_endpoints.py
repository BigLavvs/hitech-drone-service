from unittest.mock import patch

from django.test import SimpleTestCase


class HealthEndpointTests(SimpleTestCase):
    def test_health_is_lightweight_and_public(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("config.health._check_celery_worker", return_value=True)
    @patch("config.health._check_r2", return_value=True)
    @patch("config.health._check_redis", return_value=True)
    @patch("config.health._check_database", return_value=True)
    def test_ready_returns_200_when_all_dependencies_are_ready(
        self,
        _mock_db,
        _mock_redis,
        _mock_r2,
        _mock_worker,
    ):
        response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "components": {
                    "database": "ok",
                    "redis": "ok",
                    "r2": "ok",
                    "celery_worker": "ok",
                },
            },
        )

    @patch("config.health._check_celery_worker", return_value=False)
    @patch("config.health._check_r2", return_value=True)
    @patch("config.health._check_redis", return_value=True)
    @patch("config.health._check_database", return_value=True)
    def test_ready_returns_503_when_any_dependency_is_unavailable(
        self,
        _mock_db,
        _mock_redis,
        _mock_r2,
        _mock_worker,
    ):
        response = self.client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertEqual(response.json()["components"]["celery_worker"], "unavailable")
