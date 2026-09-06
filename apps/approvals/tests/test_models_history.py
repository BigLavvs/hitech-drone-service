from .support import *


class ApprovalModelTests(TestCase):
    def setUp(self) -> None:
        self.project_manager = self.create_user("pm@example.com", "pm-1", UserRole.PROJECT_MANAGER)
        self.survey_engineer = self.create_user(
            "engineer@example.com",
            "se-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Project Alpha",
            project_manager=self.project_manager,
            created_by=self.project_manager,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Site A",
            coordinates=Point(3.3792, 6.5244),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Survey A",
            survey_date=timezone.localdate(),
            created_by=self.survey_engineer,
        )

    def create_user(self, email: str, external_id: str, role: str) -> User:
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
        )

    def test_only_one_approval_may_exist_for_a_survey(self) -> None:
        Approval.objects.create(survey=self.survey)

        with self.assertRaises(IntegrityError):
            Approval.objects.create(survey=self.survey)

    def test_submission_approval_and_rejection_metadata_persist(self) -> None:
        submitted_at = timezone.now()
        approved_at = submitted_at + timedelta(hours=2)
        approval = Approval.objects.create(
            survey=self.survey,
            submitted_at=submitted_at,
            submitted_by=self.survey_engineer,
            approved_at=approved_at,
            approved_by=self.project_manager,
            rejection_reason="Cloud cover blocked required visibility.",
        )

        stored = Approval.objects.get(pk=approval.pk)

        self.assertEqual(stored.submitted_at, submitted_at)
        self.assertEqual(stored.submitted_by, self.survey_engineer)
        self.assertEqual(stored.approved_at, approved_at)
        self.assertEqual(stored.approved_by, self.project_manager)
        self.assertEqual(
            stored.rejection_reason,
            "Cloud cover blocked required visibility.",
        )

    def test_approval_history_records_link_through_related_name(self) -> None:
        approval = Approval.objects.create(survey=self.survey)
        history_entry = ApprovalHistory.objects.create(
            approval=approval,
            action="submitted",
            actor=self.survey_engineer,
            reason="Ready for manager review.",
        )

        self.assertQuerySetEqual(
            approval.history.order_by("id"),
            [history_entry],
            transform=lambda item: item,
        )

    def test_existing_approval_history_record_cannot_be_saved_after_modification(self) -> None:
        approval = Approval.objects.create(survey=self.survey)
        history_entry = ApprovalHistory.objects.create(
            approval=approval,
            action="submitted",
            actor=self.survey_engineer,
        )

        history_entry.reason = "Changed later."

        with self.assertRaisesMessage(
            ValidationError,
            "Approval history is append-only and cannot be updated.",
        ):
            history_entry.save()

    def test_existing_approval_history_record_cannot_be_deleted_directly(self) -> None:
        approval = Approval.objects.create(survey=self.survey)
        history_entry = ApprovalHistory.objects.create(
            approval=approval,
            action="submitted",
            actor=self.survey_engineer,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Approval history is append-only and cannot be deleted.",
        ):
            history_entry.delete()

    def test_deleting_approval_cascades_to_history(self) -> None:
        approval = Approval.objects.create(survey=self.survey)
        ApprovalHistory.objects.create(
            approval=approval,
            action="submitted",
            actor=self.survey_engineer,
        )

        approval.delete()

        self.assertFalse(ApprovalHistory.objects.exists())

    def test_deleting_users_sets_matching_approval_relations_to_null(self) -> None:
        approver = self.create_user("approver@example.com", "pm-2", UserRole.PROJECT_MANAGER)
        history_actor = self.create_user("actor@example.com", "se-2", UserRole.SURVEY_ENGINEER)
        approval = Approval.objects.create(
            survey=self.survey,
            submitted_by=self.survey_engineer,
            approved_by=approver,
        )
        history_entry = ApprovalHistory.objects.create(
            approval=approval,
            action="approved",
            actor=history_actor,
        )

        self.survey_engineer.delete()
        approver.delete()
        history_actor.delete()

        approval.refresh_from_db()
        history_entry.refresh_from_db()

        self.assertIsNone(approval.submitted_by)
        self.assertIsNone(approval.approved_by)
        self.assertIsNone(history_entry.actor)
