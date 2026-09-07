from .support import *


class SurveyFileApiTests(APITestCase):
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
            email="admin-api@example.com",
            external_id="admin-api-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="pm-api@example.com",
            external_id="pm-api-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="engineer-api@example.com",
            external_id="engineer-api-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer-api@example.com",
            external_id="viewer-api-1",
            role=UserRole.VIEWER,
        )
        self.unassigned_engineer = User.objects.create_user(
            email="outsider-api@example.com",
            external_id="outsider-api-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="API Upload Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Upload Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Upload Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.DRAFT,
        )
        ProjectMembership.objects.create(project=self.project, user=self.engineer, assigned_by=self.owner_manager)
        ProjectMembership.objects.create(project=self.project, user=self.viewer, assigned_by=self.owner_manager)
        self.url = f"/api/v1/surveys/{self.survey.pk}/files"

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

    def add_csrf(self, path="/projects"):
        response = self.client.get(path)
        token = response.cookies["csrftoken"].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        return token

    def make_upload(self, name="image.png", content=None, content_type="image/png"):
        if content is None:
            content = valid_png_bytes()
        return SimpleUploadedFile(name=name, content=content, content_type=content_type)

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_requires_authentication_and_csrf(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()
        unauthenticated = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            missing_csrf = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            allowed = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed.status_code, 202)

    @override_settings(RATE_LIMIT_UPLOAD="1/m")
    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_throttle_is_enforced(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            first = self.client.post(self.url, {"file": self.make_upload("first.png")}, format="multipart")
            second = self.client.post(self.url, {"file": self.make_upload("second.png")}, format="multipart")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)

    @override_settings(RATE_LIMIT_GENERAL="1/m", RATE_LIMIT_UPLOAD="10/m")
    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_general_throttle_also_applies_to_upload_posts(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            first = self.client.post(self.url, {"file": self.make_upload("general-first.png")}, format="multipart")
            second = self.client.post(self.url, {"file": self.make_upload("general-second.png")}, format="multipart")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second)

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_scope_validation_and_duplicate_response(self, storage_factory, _mocked_dispatch):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_denied = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

            self.authenticate(self.unassigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            outsider_denied = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            accepted = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")
            duplicate = self.client.post(self.url, {"file": self.make_upload()}, format="multipart")

        self.assertEqual(viewer_denied.status_code, 403)
        self.assertEqual(outsider_denied.status_code, 403)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(SurveyFile.objects.count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_upload_rejects_non_multipart_and_invalid_multipart_contract(self, storage_factory):
        storage_factory.return_value = FakePrivateStorageAdapter()

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            json_payload = self.client.post(self.url, {"file": "bad"}, format="json")
            missing_file = self.client.post(self.url, {}, format="multipart")
            unexpected_field = self.client.post(
                self.url,
                {"file": self.make_upload(), "note": "bad"},
                format="multipart",
            )
            assets_for_png = self.client.post(
                self.url,
                {"file": self.make_upload(), "assets": [self.make_upload("texture.png")]},
                format="multipart",
            )
            text_file_field = self.client.post(
                self.url,
                {"file": "not-a-file"},
                format="multipart",
            )
            text_assets_field = self.client.post(
                self.url,
                {"file": self.make_upload("valid.png"), "assets": "not-a-file"},
                format="multipart",
            )

        self.assertEqual(json_payload.status_code, 415)
        self.assertEqual(missing_file.status_code, 400)
        self.assertEqual(unexpected_field.status_code, 400)
        self.assertEqual(assets_for_png.status_code, 400)
        self.assertEqual(text_file_field.status_code, 400)
        self.assertEqual(text_assets_field.status_code, 400)

    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_invalid_primary_upload_returns_400_and_creates_no_side_effects(self, storage_factory):
        fake_storage = FakePrivateStorageAdapter()
        storage_factory.return_value = fake_storage

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.url,
                {
                    "file": self.make_upload(
                        name="bad.png",
                        content=b"\xff\xd8\xff\xe0rest",
                        content_type="image/png",
                    )
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake_storage.objects, {})
        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(SurveyFileAsset.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_file_list_visibility_and_safe_representation(self):
        survey_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="image.png",
            stored_filename="image.png",
            file_type=FileType.TWO_D,
            format=FileFormat.PNG,
            mime_type="image/png",
            size_bytes=128,
            sha256_checksum="a" * 64,
            storage_path="surveys/1/files/1/raw.png",
            preview_path="surveys/1/files/1/preview.png",
            converted_path="surveys/1/files/1/cog.tif",
            status="ready",
            uploaded_by=self.engineer,
        )
        ProcessingJob.objects.create(file=survey_file, status="completed", progress_percent=100)

        with self.auth_settings():
            self.authenticate(self.viewer)
            allowed = self.client.get(self.url)

            self.authenticate(self.unassigned_engineer)
            denied = self.client.get(self.url)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        payload = allowed.json()[0]
        self.assertEqual(payload["status"], "ready")
        self.assertIn("processing_job", payload)
        self.assertNotIn("storage_path", payload)
        self.assertNotIn("preview_path", payload)
        self.assertNotIn("converted_path", payload)
        self.assertNotIn("sha256_checksum", payload)

    def create_survey_file(self, survey=None, **overrides):
        target_survey = survey or self.survey
        payload = {
            "survey": target_survey,
            "original_filename": "image.png",
            "stored_filename": "image.png",
            "file_type": FileType.TWO_D,
            "format": FileFormat.PNG,
            "mime_type": "image/png",
            "size_bytes": 128,
            "sha256_checksum": overrides.pop("sha256_checksum", "d" * 64),
            "storage_path": overrides.pop(
                "storage_path",
                f"surveys/{target_survey.pk}/files/1/raw.png",
            ),
            "status": overrides.pop("status", "ready"),
            "uploaded_by": overrides.pop("uploaded_by", self.engineer),
        }
        payload.update(overrides)
        return SurveyFile.objects.create(**payload)

    @patch("apps.files.services.dispatch_file_download_audit_event")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_download_requires_authentication(self, storage_factory, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/object"
        storage_factory.return_value = storage
        survey_file = self.create_survey_file()
        self.survey.status = SurveyStatus.APPROVED
        self.survey.save(update_fields=["status", "updated_at"])

        response = self.client.get(f"/api/v1/surveys/{self.survey.pk}/files/{survey_file.pk}/download")

        self.assertEqual(response.status_code, 401)
        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_download_visibility_approval_and_redirect_contract(self, storage_factory, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/object?sig=1"
        storage_factory.return_value = storage
        survey_file = self.create_survey_file()
        download_url = f"/api/v1/surveys/{self.survey.pk}/files/{survey_file.pk}/download"

        with self.auth_settings():
            self.authenticate(self.viewer)
            draft_denied = self.client.get(download_url)

            self.survey.status = SurveyStatus.ARCHIVED
            self.survey.save(update_fields=["status", "updated_at"])
            archived_denied = self.client.get(download_url)

            self.survey.status = SurveyStatus.APPROVED
            self.survey.save(update_fields=["status", "updated_at"])
            allowed = self.client.get(download_url)

            self.authenticate(self.unassigned_engineer)
            outsider_denied = self.client.get(download_url)

        self.assertEqual(draft_denied.status_code, 403)
        self.assertEqual(archived_denied.status_code, 403)
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(allowed["Location"], "https://download.example.invalid/object?sig=1")
        self.assertEqual(allowed.content, b"")
        self.assertEqual(outsider_denied.status_code, 403)
        storage.generate_private_download_url.assert_called_once_with(
            storage_key=survey_file.storage_path,
            expires_in=300,
        )
        mocked_dispatch.assert_called_once_with(
            user_id=self.viewer.pk,
            project_id=self.project.pk,
            survey_id=self.survey.pk,
            survey_file_id=survey_file.pk,
        )

    @patch("apps.files.services.dispatch_file_download_audit_event")
    @patch("apps.files.services.PrivateR2StorageAdapter")
    def test_download_returns_404_for_file_survey_mismatch(self, storage_factory, mocked_dispatch):
        storage_factory.return_value = Mock()
        other_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Other Survey",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.APPROVED,
        )
        survey_file = self.create_survey_file(survey=other_survey, sha256_checksum="e" * 64)

        with self.auth_settings():
            self.authenticate(self.viewer)
            response = self.client.get(f"/api/v1/surveys/{self.survey.pk}/files/{survey_file.pk}/download")

        self.assertEqual(response.status_code, 404)
        mocked_dispatch.assert_not_called()
        storage_factory.return_value.generate_private_download_url.assert_not_called()
