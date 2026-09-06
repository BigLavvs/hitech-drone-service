from .support import *


class SurveyFileDownloadServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="download-admin@example.com",
            external_id="download-admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="download-manager@example.com",
            external_id="download-manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.engineer = User.objects.create_user(
            email="download-engineer@example.com",
            external_id="download-engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="download-viewer@example.com",
            external_id="download-viewer-1",
            role=UserRole.VIEWER,
        )
        self.outsider = User.objects.create_user(
            email="download-outsider@example.com",
            external_id="download-outsider-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Download Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Download Site",
            coordinates=Point(3.42, 6.45, srid=4326),
        )
        self.approved_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Approved Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.APPROVED,
        )
        self.archived_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Archived Survey",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.ARCHIVED,
        )
        self.pending_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Pending Survey",
            survey_date=date(2026, 8, 8),
            status=SurveyStatus.PENDING_APPROVAL,
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
        self.approved_file = SurveyFile.objects.create(
            survey=self.approved_survey,
            original_filename="approved.tif",
            stored_filename="approved.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="f" * 64,
            storage_path=f"surveys/{self.approved_survey.pk}/files/10/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_allows_normal_project_visibility_roles(self, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/file"

        for actor in (self.admin, self.owner_manager, self.engineer, self.viewer):
            with self.subTest(actor=actor.role):
                result = get_survey_file_download_for_user(
                    actor=actor,
                    survey_id=self.approved_survey.pk,
                    file_id=self.approved_file.pk,
                    storage=storage,
                )
                self.assertEqual(result.survey_file, self.approved_file)
                self.assertEqual(result.download_url, "https://download.example.invalid/file")

        self.assertEqual(mocked_dispatch.call_count, 4)

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_denies_unassigned_user_before_presign(self, mocked_dispatch):
        storage = Mock()

        with self.assertRaisesMessage(PermissionDenied, "You do not have permission to access this survey."):
            get_survey_file_download_for_user(
                actor=self.outsider,
                survey_id=self.approved_survey.pk,
                file_id=self.approved_file.pk,
                storage=storage,
            )

        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_denies_non_approved_states_including_archived(self, mocked_dispatch):
        storage = Mock()
        archived_file = SurveyFile.objects.create(
            survey=self.archived_survey,
            original_filename="archived.tif",
            stored_filename="archived.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="1" * 64,
            storage_path=f"surveys/{self.archived_survey.pk}/files/11/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )
        pending_file = SurveyFile.objects.create(
            survey=self.pending_survey,
            original_filename="pending.tif",
            stored_filename="pending.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="2" * 64,
            storage_path=f"surveys/{self.pending_survey.pk}/files/12/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )

        with self.assertRaisesMessage(PermissionDenied, "Downloads are allowed only for approved surveys."):
            get_survey_file_download_for_user(
                actor=self.viewer,
                survey_id=self.archived_survey.pk,
                file_id=archived_file.pk,
                storage=storage,
            )

        with self.assertRaisesMessage(PermissionDenied, "Downloads are allowed only for approved surveys."):
            get_survey_file_download_for_user(
                actor=self.viewer,
                survey_id=self.pending_survey.pk,
                file_id=pending_file.pk,
                storage=storage,
            )

        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_returns_404_for_file_mismatch(self, mocked_dispatch):
        storage = Mock()
        other_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Mismatch Survey",
            survey_date=date(2026, 8, 7),
            status=SurveyStatus.APPROVED,
        )
        other_file = SurveyFile.objects.create(
            survey=other_survey,
            original_filename="other.tif",
            stored_filename="other.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=2048,
            sha256_checksum="3" * 64,
            storage_path=f"surveys/{other_survey.pk}/files/13/raw.tif",
            status="ready",
            uploaded_by=self.engineer,
        )

        with self.assertRaises(SurveyFile.DoesNotExist):
            get_survey_file_download_for_user(
                actor=self.viewer,
                survey_id=self.approved_survey.pk,
                file_id=other_file.pk,
                storage=storage,
            )

        storage.generate_private_download_url.assert_not_called()
        mocked_dispatch.assert_not_called()

    @patch("apps.files.services.dispatch_file_download_audit_event")
    def test_download_service_presigns_exact_private_raw_object_and_dispatches_audit(self, mocked_dispatch):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/raw"

        result = get_survey_file_download_for_user(
            actor=self.viewer,
            survey_id=self.approved_survey.pk,
            file_id=self.approved_file.pk,
            storage=storage,
        )

        self.assertEqual(result.download_url, "https://download.example.invalid/raw")
        storage.generate_private_download_url.assert_called_once_with(
            storage_key=self.approved_file.storage_path,
            expires_in=300,
        )
        mocked_dispatch.assert_called_once_with(
            user_id=self.viewer.pk,
            project_id=self.project.pk,
            survey_id=self.approved_survey.pk,
            survey_file_id=self.approved_file.pk,
        )

    @patch("apps.audit.tasks.logger")
    @patch("apps.audit.tasks.record_file_download_audit_event.delay", side_effect=RuntimeError("broker unavailable"))
    def test_download_service_redirect_is_not_blocked_by_audit_dispatch_failure(self, mocked_delay, mocked_logger):
        storage = Mock()
        storage.generate_private_download_url.return_value = "https://download.example.invalid/raw"

        result = get_survey_file_download_for_user(
            actor=self.viewer,
            survey_id=self.approved_survey.pk,
            file_id=self.approved_file.pk,
            storage=storage,
        )

        self.assertEqual(result.download_url, "https://download.example.invalid/raw")
        mocked_delay.assert_called_once_with(
            user_id=self.viewer.pk,
            project_id=self.project.pk,
            survey_id=self.approved_survey.pk,
            survey_file_id=self.approved_file.pk,
        )
        mocked_logger.warning.assert_called_once()
