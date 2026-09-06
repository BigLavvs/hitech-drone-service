from .support import *


class ApprovalWorkflowApiTests(APITestCase):
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
        self.admin = self.create_user("admin@example.com", "admin-1", UserRole.ADMINISTRATOR)
        self.owner_manager = self.create_user("pm@example.com", "pm-1", UserRole.PROJECT_MANAGER)
        self.other_manager = self.create_user("other-pm@example.com", "pm-2", UserRole.PROJECT_MANAGER)
        self.engineer = self.create_user("engineer@example.com", "se-1", UserRole.SURVEY_ENGINEER)
        self.other_engineer = self.create_user("other-engineer@example.com", "se-2", UserRole.SURVEY_ENGINEER)
        self.viewer = self.create_user("viewer@example.com", "viewer-1", UserRole.VIEWER)
        self.unassigned_viewer = self.create_user(
            "viewer-2@example.com",
            "viewer-2",
            UserRole.VIEWER,
        )
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
            submitted_at=datetime(2026, 8, 10, 9, 0, tzinfo=dt_timezone.utc),
            submitted_by=self.engineer,
        )
        submitted_history = ApprovalHistory.objects.create(
            approval=self.pending_approval,
            action="submitted",
            actor=self.engineer,
        )
        approved_history = ApprovalHistory.objects.create(
            approval=self.pending_approval,
            action="approved",
            actor=self.owner_manager,
        )
        ApprovalHistory.objects.filter(pk=submitted_history.pk).update(
            timestamp=datetime(2026, 8, 10, 9, 0, tzinfo=dt_timezone.utc)
        )
        ApprovalHistory.objects.filter(pk=approved_history.pk).update(
            timestamp=datetime(2026, 8, 10, 11, 0, tzinfo=dt_timezone.utc)
        )
        self.pending_approval.approved_at = datetime(2026, 8, 10, 11, 0, tzinfo=dt_timezone.utc)
        self.pending_approval.approved_by = self.owner_manager
        self.pending_approval.save(update_fields=["approved_at", "approved_by", "updated_at"])
        self.pending_survey.status = SurveyStatus.APPROVED
        self.pending_survey.approved_by = self.owner_manager
        self.pending_survey.save(update_fields=["status", "approved_by", "updated_at"])

        self.other_project_pending = self.create_ready_survey(
            name="Other Pending",
            created_by=self.other_engineer,
            status=SurveyStatus.PENDING_APPROVAL,
            project=self.other_project,
            site=self.other_site,
        )
        self.other_project_approval = Approval.objects.create(
            survey=self.other_project_pending,
            submitted_at=datetime(2026, 8, 10, 8, 0, tzinfo=dt_timezone.utc),
            submitted_by=self.other_engineer,
        )
        ApprovalHistory.objects.create(
            approval=self.other_project_approval,
            action="submitted",
            actor=self.other_engineer,
        )

    def auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem,
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
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
            size_bytes=2048,
            sha256_checksum=f"{survey.pk + 1000:064d}",
            storage_path=f"surveys/{survey.pk}/files/1/raw.tif",
            status="ready",
            uploaded_by=created_by,
        )
        ProcessingJob.objects.create(file=survey_file, status="completed", progress_percent=100)
        return survey

    def make_token(self, user: User) -> str:
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

    def authenticate(self, user: User, *, enforce_csrf_checks: bool = False) -> None:
        self.client = self.client_class(enforce_csrf_checks=enforce_csrf_checks)
        self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = self.make_token(user)

    def add_csrf(self, path: str = "/projects") -> str:
        response = self.client.get(path)
        token = response.cookies["csrftoken"].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        return token

    def url(self, survey: Survey, action: str) -> str:
        return f"/api/v1/surveys/{survey.pk}/{action}"

    def test_authentication_and_csrf_are_enforced(self) -> None:
        submit_url = self.url(self.ready_survey, "submit")

        unauthenticated = self.client.post(submit_url, {}, format="json")

        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            missing_csrf = self.client.post(submit_url, {}, format="json")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            allowed = self.client.post(submit_url, {}, format="json")

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_submit_endpoint_enforces_scope_strict_payload_and_empty_success(self) -> None:
        with self.auth_settings():
            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            good = self.client.post(self.url(self.ready_survey, "submit"), {}, format="json")

            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            pm_denied = self.client.post(self.url(self.ready_survey, "submit"), {}, format="json")

            self.authenticate(self.engineer, enforce_csrf_checks=True)
            self.add_csrf()
            bad_payload = self.client.post(
                self.url(self.create_ready_survey(name="Another Ready", created_by=self.engineer), "submit"),
                {"unexpected": True},
                format="json",
            )

        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.content, b"")
        self.assertEqual(pm_denied.status_code, 403)
        self.assertEqual(bad_payload.status_code, 400)

    def test_approve_and_reject_scope_conflict_revalidation_and_reason_validation(self) -> None:
        to_approve = self.create_ready_survey(
            name="To Approve",
            created_by=self.engineer,
            status=SurveyStatus.PENDING_APPROVAL,
        )
        to_approve_approval = Approval.objects.create(
            survey=to_approve,
            submitted_at=datetime(2026, 8, 10, 10, 0, tzinfo=dt_timezone.utc),
            submitted_by=self.engineer,
        )
        ApprovalHistory.objects.create(
            approval=to_approve_approval,
            action="submitted",
            actor=self.engineer,
        )
        to_approve.processing_status = "failed"
        to_approve.save(update_fields=["processing_status"])

        with self.auth_settings():
            self.authenticate(self.viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_denied = self.client.post(self.url(to_approve, "approve"), {}, format="json")

            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            conflict = self.client.post(self.url(self.ready_survey, "approve"), {}, format="json")
            not_ready = self.client.post(self.url(to_approve, "approve"), {}, format="json")

            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            cross_project = self.client.post(self.url(self.other_project_pending, "approve"), {}, format="json")

            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            missing_reason = self.client.post(self.url(to_approve, "reject"), {}, format="json")
            blank_reason = self.client.post(
                self.url(to_approve, "reject"),
                {"reason": "   "},
                format="json",
            )
            unexpected_reason = self.client.post(
                self.url(to_approve, "reject"),
                {"reason": "Missing overlap.", "unexpected": True},
                format="json",
            )

        self.assertEqual(viewer_denied.status_code, 403)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(not_ready.status_code, 400)
        self.assertEqual(cross_project.status_code, 200)
        self.assertEqual(missing_reason.status_code, 400)
        self.assertEqual(blank_reason.status_code, 400)
        self.assertEqual(unexpected_reason.status_code, 400)

    def test_self_review_denial_and_archive_rules(self) -> None:
        self.pending_survey.created_by = self.owner_manager
        self.pending_survey.status = SurveyStatus.PENDING_APPROVAL
        self.pending_survey.save(update_fields=["created_by", "status"])
        self.pending_approval.approved_at = None
        self.pending_approval.approved_by = None
        self.pending_approval.save(update_fields=["approved_at", "approved_by", "updated_at"])

        rejected = self.create_ready_survey(
            name="Rejected Survey",
            created_by=self.engineer,
            status=SurveyStatus.PENDING_APPROVAL,
        )
        rejected_approval = Approval.objects.create(
            survey=rejected,
            submitted_at=datetime(2026, 8, 10, 7, 0, tzinfo=dt_timezone.utc),
            submitted_by=self.engineer,
            rejection_reason="Needs correction.",
        )
        ApprovalHistory.objects.create(
            approval=rejected_approval,
            action="submitted",
            actor=self.engineer,
        )
        rejected.status = SurveyStatus.REJECTED
        rejected.save(update_fields=["status"])
        ApprovalHistory.objects.create(
            approval=rejected_approval,
            action="rejected",
            actor=self.owner_manager,
            reason="Needs correction.",
        )

        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            self_approve = self.client.post(self.url(self.pending_survey, "approve"), {}, format="json")
            self_reject = self.client.post(
                self.url(self.pending_survey, "reject"),
                {"reason": "No."},
                format="json",
            )
            invalid_archive = self.client.post(self.url(self.ready_survey, "archive"), {}, format="json")
            valid_archive = self.client.post(self.url(rejected, "archive"), {}, format="json")

        self.assertEqual(self_approve.status_code, 403)
        self.assertEqual(self_reject.status_code, 403)
        self.assertEqual(invalid_archive.status_code, 400)
        self.assertEqual(valid_archive.status_code, 200)
        self.assertEqual(valid_archive.json()["status"], SurveyStatus.ARCHIVED)
        self.assertTrue(Survey.objects.filter(pk=rejected.pk).exists())

    def test_approval_read_enforces_visibility_and_representation(self) -> None:
        draft_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Draft Survey",
            survey_date=date(2026, 8, 10),
            status=SurveyStatus.READY,
            processing_status="completed",
            created_by=self.engineer,
        )

        with self.auth_settings():
            self.authenticate(self.admin)
            admin_read = self.client.get(self.url(self.pending_survey, "approvals"))

            self.authenticate(self.owner_manager)
            owner_read = self.client.get(self.url(self.pending_survey, "approvals"))

            self.authenticate(self.viewer)
            viewer_read = self.client.get(self.url(self.pending_survey, "approvals"))

            self.authenticate(self.unassigned_viewer)
            forbidden_read = self.client.get(self.url(self.pending_survey, "approvals"))

            self.authenticate(self.engineer)
            pre_submission = self.client.get(self.url(draft_survey, "approvals"))

        self.assertEqual(admin_read.status_code, 200)
        self.assertEqual(owner_read.status_code, 200)
        self.assertEqual(viewer_read.status_code, 200)
        self.assertEqual(forbidden_read.status_code, 403)
        self.assertEqual(pre_submission.status_code, 404)

        body = admin_read.json()
        self.assertEqual(
            list(body.keys()),
            [
                "survey_id",
                "current_status",
                "submitted_at",
                "submitted_by",
                "approved_at",
                "approved_by",
                "rejection_reason",
                "history",
            ],
        )
        self.assertEqual(body["current_status"], SurveyStatus.APPROVED)
        self.assertEqual([entry["action"] for entry in body["history"]], ["submitted", "approved"])
        self.assertEqual(body["history"][0]["actor_id"], self.engineer.pk)
        self.assertEqual(body["history"][1]["actor_id"], self.owner_manager.pk)
