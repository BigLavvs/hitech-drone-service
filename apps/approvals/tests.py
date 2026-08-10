from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.gis.geos import Point
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.approvals.models import Approval, ApprovalHistory
from apps.approvals.services import (
    ApprovalConflictError,
    approve_survey,
    reject_survey,
    submit_survey_for_approval,
)
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.projects.models import Project, Site
from apps.processing.models import ProcessingJob
from apps.projects.models import ProjectMembership
from apps.surveys.models import Survey, SurveyStatus
from apps.surveys.services import archive_survey_after_review


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
