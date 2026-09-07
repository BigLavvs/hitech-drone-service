from .support import *


class ApprovalWorkflowServiceTests(TestCase):
    def setUp(self) -> None:
        self.admin = self.create_user("admin@example.com", "admin-1", UserRole.ADMINISTRATOR)
        self.owner_manager = self.create_user("pm@example.com", "pm-1", UserRole.PROJECT_MANAGER)
        self.other_manager = self.create_user("other-pm@example.com", "pm-2", UserRole.PROJECT_MANAGER)
        self.engineer = self.create_user("engineer@example.com", "se-1", UserRole.SURVEY_ENGINEER)
        self.other_engineer = self.create_user("other-engineer@example.com", "se-2", UserRole.SURVEY_ENGINEER)
        self.viewer = self.create_user("viewer@example.com", "viewer-1", UserRole.VIEWER)
        self.project = Project.objects.create(
            name="Project Alpha",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.other_project = Project.objects.create(
            name="Project Beta",
            project_manager=self.other_manager,
            created_by=self.admin,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Site A",
            coordinates=Point(3.3792, 6.5244, srid=4326),
        )
        self.other_site = Site.objects.create(
            project=self.other_project,
            name="Site B",
            coordinates=Point(7.3792, 9.5244, srid=4326),
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
        self.ready_survey = self.create_ready_survey(
            name="Ready Survey",
            created_by=self.engineer,
        )
        self.pending_survey = self.create_ready_survey(
            name="Pending Survey",
            created_by=self.engineer,
            status=SurveyStatus.PENDING_APPROVAL,
        )
        self.pending_approval = Approval.objects.create(
            survey=self.pending_survey,
            submitted_by=self.engineer,
            submitted_at=timezone.now(),
        )
        ApprovalHistory.objects.create(
            approval=self.pending_approval,
            action="submitted",
            actor=self.engineer,
        )

    def create_user(self, email: str, external_id: str, role: str, **extra_fields) -> User:
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def create_ready_survey(
        self,
        *,
        name: str,
        created_by: User,
        status: str = SurveyStatus.READY,
        processing_status: str = "completed",
        project: Project | None = None,
        site: Site | None = None,
    ) -> Survey:
        survey = Survey.objects.create(
            project=project or self.project,
            site=site or self.site,
            name=name,
            survey_date=date(2026, 8, 10),
            status=status,
            processing_status=processing_status,
            created_by=created_by,
        )
        survey_file = SurveyFile.objects.create(
            survey=survey,
            original_filename=f"{name}.tif",
            stored_filename=f"{name}.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=1024,
            sha256_checksum=f"{survey.pk:064d}",
            storage_path=f"surveys/{survey.pk}/files/1/raw.tif",
            status="ready",
            uploaded_by=created_by,
        )
        ProcessingJob.objects.create(
            file=survey_file,
            status="completed",
            progress_percent=100,
        )
        return survey

    def test_submit_requires_assigned_active_survey_engineer(self) -> None:
        blocked_actors = (self.admin, self.owner_manager, self.other_engineer, self.viewer)

        for actor in blocked_actors:
            with self.assertRaises(PermissionDenied):
                submit_survey_for_approval(actor=actor, survey=self.ready_survey)

        self.assertEqual(Approval.objects.filter(survey=self.ready_survey).count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_submit_rejects_non_ready_surveys_without_writes(self) -> None:
        scenarios = []

        wrong_status = self.create_ready_survey(
            name="Draft Survey",
            created_by=self.engineer,
            status=SurveyStatus.DRAFT,
        )
        scenarios.append(wrong_status)

        wrong_processing = self.create_ready_survey(
            name="Running Survey",
            created_by=self.engineer,
            processing_status="processing",
        )
        scenarios.append(wrong_processing)

        no_files = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="No Files Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.READY,
            processing_status="completed",
            created_by=self.engineer,
        )
        scenarios.append(no_files)

        file_not_ready = self.create_ready_survey(name="File Not Ready", created_by=self.engineer)
        file_not_ready.files.update(status="processing")
        scenarios.append(file_not_ready)

        job_not_ready = self.create_ready_survey(name="Job Not Ready", created_by=self.engineer)
        ProcessingJob.objects.filter(file__survey=job_not_ready).update(status="processing")
        scenarios.append(job_not_ready)

        for survey in scenarios:
            with self.assertRaises(ValidationError):
                submit_survey_for_approval(actor=self.engineer, survey=survey)

        self.assertEqual(Approval.objects.filter(survey__in=scenarios).count(), 0)
        self.assertEqual(ApprovalHistory.objects.exclude(approval=self.pending_approval).count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_submit_writes_state_approval_history_and_audit(self) -> None:
        submit_survey_for_approval(actor=self.engineer, survey=self.ready_survey)

        self.ready_survey.refresh_from_db()
        approval = Approval.objects.get(survey=self.ready_survey)
        history = ApprovalHistory.objects.get(approval=approval)
        audit = AuditLog.objects.get(survey=self.ready_survey)

        self.assertEqual(self.ready_survey.status, SurveyStatus.PENDING_APPROVAL)
        self.assertEqual(approval.submitted_by, self.engineer)
        self.assertEqual(history.action, "submitted")
        self.assertEqual(history.actor, self.engineer)
        self.assertEqual(audit.action, AuditAction.SURVEY_SUBMITTED)

    @patch("apps.approvals.services.record_audit_event", side_effect=RuntimeError("audit failed"))
    def test_submit_rolls_back_when_audit_write_fails(self, mocked_record_audit_event) -> None:
        with self.assertRaisesMessage(RuntimeError, "audit failed"):
            submit_survey_for_approval(actor=self.engineer, survey=self.ready_survey)

        mocked_record_audit_event.assert_called_once()
        self.ready_survey.refresh_from_db()
        self.assertEqual(self.ready_survey.status, SurveyStatus.READY)
        self.assertFalse(Approval.objects.filter(survey=self.ready_survey).exists())
        self.assertEqual(ApprovalHistory.objects.exclude(approval=self.pending_approval).count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    @patch("apps.approvals.services.ApprovalHistory.objects.create", side_effect=RuntimeError("history failed"))
    def test_submit_rolls_back_when_history_write_fails(self, mocked_history_create) -> None:
        with self.assertRaisesMessage(RuntimeError, "history failed"):
            submit_survey_for_approval(actor=self.engineer, survey=self.ready_survey)

        mocked_history_create.assert_called_once()
        self.ready_survey.refresh_from_db()
        self.assertEqual(self.ready_survey.status, SurveyStatus.READY)
        self.assertFalse(Approval.objects.filter(survey=self.ready_survey).exists())
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_approve_requires_owner_scope_and_blocks_self_approval(self) -> None:
        other_project_survey = self.create_ready_survey(
            name="Other Project Pending",
            created_by=self.other_engineer,
            status=SurveyStatus.PENDING_APPROVAL,
            project=self.other_project,
            site=self.other_site,
        )
        other_project_approval = Approval.objects.create(
            survey=other_project_survey,
            submitted_by=self.other_engineer,
            submitted_at=timezone.now(),
        )
        ApprovalHistory.objects.create(
            approval=other_project_approval,
            action="submitted",
            actor=self.other_engineer,
        )
        self.pending_survey.created_by = self.owner_manager
        self.pending_survey.save(update_fields=["created_by"])

        with self.assertRaises(PermissionDenied):
            approve_survey(actor=self.viewer, survey=self.pending_survey)
        with self.assertRaises(PermissionDenied):
            approve_survey(actor=self.engineer, survey=self.pending_survey)
        with self.assertRaises(PermissionDenied):
            approve_survey(actor=self.owner_manager, survey=self.pending_survey)
        with self.assertRaises(PermissionDenied):
            approve_survey(actor=self.owner_manager, survey=other_project_survey)

        approve_survey(actor=self.admin, survey=other_project_survey)
        other_project_survey.refresh_from_db()
        self.assertEqual(other_project_survey.status, SurveyStatus.APPROVED)

    def test_approve_and_reject_require_pending_state(self) -> None:
        with self.assertRaisesMessage(ApprovalConflictError, "Survey must be pending approval."):
            approve_survey(actor=self.owner_manager, survey=self.ready_survey)
        with self.assertRaisesMessage(ApprovalConflictError, "Survey must be pending approval."):
            reject_survey(actor=self.owner_manager, survey=self.ready_survey, reason="Rejected")

    def test_approve_revalidates_readiness_and_writes_expected_records(self) -> None:
        self.pending_survey.processing_status = "failed"
        self.pending_survey.save(update_fields=["processing_status"])

        with self.assertRaises(ValidationError):
            approve_survey(actor=self.owner_manager, survey=self.pending_survey)

        self.pending_survey.processing_status = "completed"
        self.pending_survey.save(update_fields=["processing_status"])
        approve_survey(actor=self.owner_manager, survey=self.pending_survey)

        self.pending_survey.refresh_from_db()
        self.pending_approval.refresh_from_db()
        history_actions = list(
            ApprovalHistory.objects.filter(approval=self.pending_approval)
            .order_by("id")
            .values_list("action", flat=True)
        )
        audit = AuditLog.objects.get(survey=self.pending_survey)

        self.assertEqual(self.pending_survey.status, SurveyStatus.APPROVED)
        self.assertEqual(self.pending_survey.approved_by, self.owner_manager)
        self.assertEqual(self.pending_approval.approved_by, self.owner_manager)
        self.assertEqual(history_actions, ["submitted", "approved"])
        self.assertEqual(audit.action, AuditAction.APPROVAL_APPROVED)

    def test_reject_writes_reason_history_and_audit(self) -> None:
        reject_survey(actor=self.owner_manager, survey=self.pending_survey, reason="Missing overlap.")

        self.pending_survey.refresh_from_db()
        self.pending_approval.refresh_from_db()
        rejection_history = ApprovalHistory.objects.filter(approval=self.pending_approval).latest("id")
        audit = AuditLog.objects.get(survey=self.pending_survey)

        self.assertEqual(self.pending_survey.status, SurveyStatus.REJECTED)
        self.assertEqual(self.pending_approval.rejection_reason, "Missing overlap.")
        self.assertEqual(rejection_history.action, "rejected")
        self.assertEqual(rejection_history.reason, "Missing overlap.")
        self.assertEqual(audit.action, AuditAction.APPROVAL_REJECTED)

    def test_approval_rechecks_current_project_owner_after_initial_authorization(self) -> None:
        original_lock = __import__("apps.approvals.services", fromlist=["lock_survey_for_workflow"]).lock_survey_for_workflow

        def transfer_before_lock(*, survey_id):
            Project.objects.filter(pk=self.project.pk).update(project_manager=self.other_manager)
            return original_lock(survey_id=survey_id)

        with patch("apps.approvals.services.lock_survey_for_workflow", side_effect=transfer_before_lock):
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only an active administrator or the owning project manager can review this survey.",
            ):
                approve_survey(actor=self.owner_manager, survey=self.pending_survey)

        self.pending_survey.refresh_from_db()
        self.assertEqual(self.pending_survey.status, SurveyStatus.PENDING_APPROVAL)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_archive_requires_reviewed_state_and_preserves_record(self) -> None:
        with self.assertRaises(ValidationError):
            archive_survey_after_review(actor=self.owner_manager, survey=self.pending_survey)

        reject_survey(actor=self.owner_manager, survey=self.pending_survey, reason="Missing overlap.")
        archived = archive_survey_after_review(actor=self.owner_manager, survey=self.pending_survey)

        archived.refresh_from_db()
        self.assertEqual(archived.status, SurveyStatus.ARCHIVED)
        self.assertTrue(Survey.objects.filter(pk=archived.pk).exists())
        self.assertEqual(
            list(
                ApprovalHistory.objects.filter(approval=self.pending_approval)
                .order_by("id")
                .values_list("action", flat=True)
            ),
            ["submitted", "rejected", "archived"],
        )
        self.assertEqual(
            list(AuditLog.objects.filter(survey=self.pending_survey).order_by("id").values_list("action", flat=True)),
            [AuditAction.APPROVAL_REJECTED, AuditAction.SURVEY_ARCHIVED],
        )
