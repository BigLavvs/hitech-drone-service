from .support import *


class ProcessingRoutingTests(TestCase):
    def test_retry_constants_match_documented_policy(self):
        self.assertEqual(MAX_AUTOMATIC_RETRIES, 3)
        self.assertEqual(RETRY_DELAYS_MINUTES, (2, 5, 10))

    def test_derived_raster_tile_zoom_is_capped_for_assessment_processing(self):
        derived_zoom = _derive_max_zoom(
            mercator_bounds=(0.0, 0.0, 256.0, 256.0),
            width=65536,
            height=65536,
        )

        self.assertEqual(derived_zoom, ASSESSMENT_MAX_GENERATED_TILE_ZOOM)


class ProcessingJobApiTests(APITestCase):
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
            email="admin-processing@example.com",
            external_id="admin-processing-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="pm-processing@example.com",
            external_id="pm-processing-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="engineer-processing@example.com",
            external_id="engineer-processing-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer-processing@example.com",
            external_id="viewer-processing-1",
            role=UserRole.VIEWER,
        )
        self.other_engineer = User.objects.create_user(
            email="other-processing@example.com",
            external_id="other-processing-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Processing API Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Processing Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Processing Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.FAILED,
            processing_status="failed",
            created_by=self.engineer,
        )
        ProjectMembership.objects.create(project=self.project, user=self.engineer, assigned_by=self.owner_manager)
        ProjectMembership.objects.create(project=self.project, user=self.viewer, assigned_by=self.owner_manager)
        self.survey_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="mesh.obj",
            stored_filename="mesh.obj",
            file_type=FileType.THREE_D,
            format=FileFormat.OBJ,
            mime_type="model/obj",
            size_bytes=128,
            sha256_checksum="a" * 64,
            storage_path="surveys/1/files/1/raw.obj",
            preview_path="surveys/1/files/1/preview.glb",
            converted_path="surveys/1/files/1/model.glb",
            status="failed",
            uploaded_by=self.engineer,
        )
        self.job = ProcessingJob.objects.create(
            file=self.survey_file,
            status="failed",
            progress_percent=99,
            retry_count=0,
            error_message="processing failed",
        )
        self.detail_url = f"/api/v1/processing-jobs/{self.job.pk}"
        self.retry_url = f"{self.detail_url}/retry"

    def auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem,
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
        )

    def make_token(self, user):
        now = datetime.now(dt_timezone.utc)
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

    def add_csrf(self, path="/projects"):
        response = self.client.get(path)
        token = response.cookies["csrftoken"].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        return token

    def test_job_detail_visibility_and_safe_representation(self):
        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(self.detail_url)

            self.authenticate(self.other_engineer)
            denied = self.client.get(self.detail_url)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        payload = allowed.json()
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("storage_path", payload["file"])
        self.assertNotIn("sha256_checksum", payload["file"])
        self.assertNotIn("preview_path", payload["file"])
        self.assertNotIn("converted_path", payload["file"])

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    def test_retry_requires_authentication_csrf_and_role_scope(self, mocked_dispatch):
        unauthenticated = self.client.post(self.retry_url, {}, format="json")

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            missing_csrf = self.client.post(self.retry_url, {}, format="json")

            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_denied = self.client.post(self.retry_url, {}, format="json")

            self.authenticate(self.other_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            outsider_denied = self.client.post(self.retry_url, {}, format="json")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                accepted = self.client.post(self.retry_url, {}, format="json")

        self.job.refresh_from_db()
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(viewer_denied.status_code, 403)
        self.assertEqual(outsider_denied.status_code, 403)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.job.status, "queued")
        self.assertEqual(self.job.retry_count, 0)
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(), 1)
        self.assertEqual(len(callbacks), 1)
        mocked_dispatch.assert_not_called()

        callbacks[0]()
        mocked_dispatch.assert_called_once_with(processing_job_id=self.job.pk)

    @override_settings(RATE_LIMIT_RETRY="1/m")
    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    def test_retry_throttle_and_state_guards(self, _mocked_dispatch):
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            first = self.client.post(self.retry_url, {}, format="json")
            second = self.client.post(self.retry_url, {}, format="json")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)

        cache.clear()
        self.job.refresh_from_db()
        self.job.status = "completed"
        self.job.save(update_fields=["status", "updated_at"])
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            wrong_state = self.client.post(self.retry_url, {}, format="json")

        self.assertEqual(wrong_state.status_code, 400)

    @override_settings(RATE_LIMIT_GENERAL="1/m", RATE_LIMIT_RETRY="10/m")
    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    def test_general_throttle_also_applies_to_retry_posts(self, _mocked_dispatch):
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            first = self.client.post(self.retry_url, {}, format="json")
            second = self.client.post(self.retry_url, {}, format="json")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second)

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    def test_retry_after_permanent_failure_starts_new_automatic_cycle(self, _mocked_dispatch):
        self.job.retry_count = MAX_AUTOMATIC_RETRIES
        self.job.save(update_fields=["retry_count", "updated_at"])

        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(self.retry_url, {}, format="json")

        self.job.refresh_from_db()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.job.retry_count, 0)
        details = AuditLog.objects.get(action=AuditAction.PROCESSING_RETRY).details
        self.assertEqual(details["previous_retry_count"], MAX_AUTOMATIC_RETRIES)
        self.assertFalse(details["automatic"])
