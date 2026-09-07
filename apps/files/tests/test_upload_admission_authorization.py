from .support import *


class UploadAdmissionAuthorizationMixin:
    @override_settings(MAX_SURVEY_TOTAL_SIZE_BYTES=1024)
    def test_upload_authorization_cases(self):
        allowed_users = (self.admin, self.owner_manager, self.assigned_engineer)
        denied_users = (
            self.viewer,
            self.unassigned_engineer,
            self.other_manager,
            self.inactive_admin,
            self.inactive_owner_manager,
            self.inactive_assigned_engineer,
        )

        for actor in allowed_users:
            with self.subTest(actor=actor.email):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Upload Survey {actor.pk}",
                    survey_date=date(2026, 8, 9),
                    status=SurveyStatus.DRAFT,
                )
                storage = FakePrivateStorageAdapter()
                result = admit_uploaded_file(
                    actor=actor,
                    survey=survey,
                    uploaded_file=self.make_upload(),
                    storage=storage,
                )

                self.assertTrue(result.created)
                self.assertEqual(result.survey_file.uploaded_by, actor)

        for actor in denied_users:
            with self.subTest(actor=actor.email):
                storage = FakePrivateStorageAdapter()
                with self.assertRaisesMessage(
                    PermissionDenied,
                    "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files.",
                ):
                    admit_uploaded_file(
                        actor=actor,
                        survey=self.survey,
                        uploaded_file=self.make_upload(),
                        storage=storage,
                    )

                self.assertEqual(storage.uploaded, [])

    def test_archived_project_is_rejected_without_upload(self):
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(
            ValidationError,
            "Uploads are not allowed for archived projects.",
        ):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.archived_project_survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])
        self.assertEqual(SurveyFile.objects.count(), 0)

    def test_upload_rechecks_membership_after_storage_staging(self):
        storage = FakePrivateStorageAdapter()
        original_upload = storage.upload_to_staging

        def revoke_before_admission(**kwargs):
            ProjectMembership.objects.filter(
                project=self.project, user=self.assigned_engineer
            ).delete()
            return original_upload(**kwargs)

        storage.upload_to_staging = revoke_before_admission

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators, the owning project manager, and assigned survey engineers can upload survey files.",
        ):
            admit_uploaded_file(
                actor=self.assigned_engineer,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(storage.objects, {})

    @override_settings(MAX_SURVEY_TOTAL_SIZE_BYTES=1024)
    def test_allowed_and_rejected_survey_states_are_enforced(self):
        allowed_states = (
            SurveyStatus.DRAFT,
            SurveyStatus.UPLOADING,
            SurveyStatus.PROCESSING,
            SurveyStatus.FAILED,
            SurveyStatus.READY,
        )
        rejected_states = (
            SurveyStatus.PENDING_APPROVAL,
            SurveyStatus.APPROVED,
            SurveyStatus.REJECTED,
            SurveyStatus.ARCHIVED,
        )

        for state in allowed_states:
            with self.subTest(state=state):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Allowed {state}",
                    survey_date=date(2026, 8, 9),
                    status=state,
                )
                storage = FakePrivateStorageAdapter()
                result = admit_uploaded_file(
                    actor=self.admin,
                    survey=survey,
                    uploaded_file=self.make_upload(),
                    storage=storage,
                )

                self.assertTrue(result.created)

        for state in rejected_states:
            with self.subTest(state=state):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Rejected {state}",
                    survey_date=date(2026, 8, 9),
                    status=state,
                )
                storage = FakePrivateStorageAdapter()
                with self.assertRaisesMessage(
                    ValidationError,
                    "Uploads are not allowed for surveys in the current state.",
                ):
                    admit_uploaded_file(
                        actor=self.admin,
                        survey=survey,
                        uploaded_file=self.make_upload(),
                        storage=storage,
                    )

                self.assertEqual(storage.uploaded, [])
