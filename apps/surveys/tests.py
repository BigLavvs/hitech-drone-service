from datetime import date
from unittest.mock import patch

from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.access_control.models import User, UserRole
from apps.approvals.models import Approval
from apps.audit.models import AuditAction, AuditLog
from apps.projects.models import ProjectMembership
from apps.projects.models import Project, Site
from apps.surveys.models import Survey, SurveyStatus
from apps.surveys.services import create_survey, update_survey


class SurveyModelTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            email="creator@example.com",
            external_id="creator-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.approver = User.objects.create_user(
            email="approver@example.com",
            external_id="approver-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.project = Project.objects.create(name="Lekki Deep Scan")
        self.site = Site.objects.create(
            project=self.project,
            name="Zone A",
            coordinates=Point(3.3903, 6.4474, srid=4326),
        )

    def test_documented_defaults_are_applied(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Baseline Capture",
            survey_date=date(2026, 8, 9),
        )

        self.assertEqual(survey.status, SurveyStatus.DRAFT)
        self.assertEqual(survey.processing_status, "pending")
        self.assertEqual(survey.coordinate_reference_system, "EPSG:4326")

    def test_project_site_creator_and_approver_relationships(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Progress Capture",
            survey_date=date(2026, 8, 8),
            created_by=self.creator,
            approved_by=self.approver,
        )

        self.assertEqual(survey.project, self.project)
        self.assertEqual(survey.site, self.site)
        self.assertEqual(survey.created_by, self.creator)
        self.assertEqual(survey.approved_by, self.approver)
        self.assertEqual(self.project.surveys.get(), survey)
        self.assertEqual(self.site.surveys.get(), survey)
        self.assertEqual(self.creator.surveys_created.get(), survey)
        self.assertEqual(self.approver.surveys_approved.get(), survey)

    def test_deleting_site_cascades_to_surveys(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Weekly Capture",
            survey_date=date(2026, 8, 7),
        )

        self.site.delete()

        self.assertFalse(Survey.objects.filter(pk=survey.pk).exists())

    def test_deleting_creator_or_approver_sets_corresponding_fields_to_null(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Approval Capture",
            survey_date=date(2026, 8, 6),
            created_by=self.creator,
            approved_by=self.approver,
        )

        self.creator.delete()
        survey.refresh_from_db()
        self.assertIsNone(survey.created_by)
        self.assertEqual(survey.approved_by, self.approver)

        self.approver.delete()
        survey.refresh_from_db()
        self.assertIsNone(survey.approved_by)

    def test_every_documented_status_value_is_accepted(self):
        accepted_statuses = {choice for choice, _ in SurveyStatus.choices}

        self.assertEqual(
            accepted_statuses,
            {
                SurveyStatus.DRAFT,
                SurveyStatus.UPLOADING,
                SurveyStatus.PROCESSING,
                SurveyStatus.READY,
                SurveyStatus.FAILED,
                SurveyStatus.PENDING_APPROVAL,
                SurveyStatus.APPROVED,
                SurveyStatus.REJECTED,
                SurveyStatus.ARCHIVED,
            },
        )

        for index, status in enumerate(SurveyStatus.values, start=1):
            survey = Survey.objects.create(
                project=self.project,
                site=self.site,
                name=f"Survey {index}",
                survey_date=date(2026, 8, index),
                status=status,
            )
            self.assertEqual(survey.status, status)


class SurveyServiceTests(TestCase):
    def setUp(self):
        self.admin = self.create_user("admin@example.com", "admin-1", UserRole.ADMINISTRATOR)
        self.owner_manager = self.create_user(
            "owner-manager@example.com",
            "manager-1",
            UserRole.PROJECT_MANAGER,
        )
        self.other_manager = self.create_user(
            "other-manager@example.com",
            "manager-2",
            UserRole.PROJECT_MANAGER,
        )
        self.inactive_owner_manager = self.create_user(
            "inactive-owner-manager@example.com",
            "manager-3",
            UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.creator_engineer = self.create_user(
            "creator-engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.other_engineer = self.create_user(
            "other-engineer@example.com",
            "engineer-2",
            UserRole.SURVEY_ENGINEER,
        )
        self.inactive_assigned_engineer = self.create_user(
            "inactive-assigned-engineer@example.com",
            "engineer-4",
            UserRole.SURVEY_ENGINEER,
            is_active=False,
        )
        self.viewer = self.create_user("viewer@example.com", "viewer-1", UserRole.VIEWER)
        self.inactive_admin = self.create_user(
            "inactive-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
            is_active=False,
        )
        self.active_project = Project.objects.create(
            name="Active Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
            status="active",
        )
        self.archived_project = Project.objects.create(
            name="Archived Project",
            project_manager=self.owner_manager,
            created_by=self.admin,
            status="archived",
        )
        self.site = Site.objects.create(
            project=self.active_project,
            name="Active Site",
            coordinates=Point(3.3903, 6.4474, srid=4326),
        )
        self.archived_site = Site.objects.create(
            project=self.archived_project,
            name="Archived Site",
            coordinates=Point(3.4903, 6.5474, srid=4326),
        )
        self.other_project = Project.objects.create(
            name="Other Project",
            project_manager=self.other_manager,
            created_by=self.admin,
            status="active",
        )
        self.other_site = Site.objects.create(
            project=self.other_project,
            name="Other Site",
            coordinates=Point(3.5903, 6.6474, srid=4326),
        )
        ProjectMembership.objects.create(
            project=self.active_project,
            user=self.creator_engineer,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.active_project,
            user=self.other_engineer,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.active_project,
            user=self.viewer,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.active_project,
            user=self.inactive_assigned_engineer,
            assigned_by=self.owner_manager,
        )
        self.survey = Survey.objects.create(
            project=self.active_project,
            site=self.site,
            name="Existing Survey",
            survey_date=date(2026, 8, 8),
            drone_model="DJI Matrice 300 RTK",
            pilot="Jane Pilot",
            coordinate_reference_system="EPSG:32633",
            notes="Initial notes",
            created_by=self.creator_engineer,
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_authorized_roles_can_create_survey(self):
        for actor in (self.admin, self.owner_manager, self.creator_engineer):
            survey = create_survey(
                actor=actor,
                project=self.active_project,
                site=self.site,
                name=f"Survey {actor.pk}",
                survey_date=date(2026, 8, 9),
            )

            self.assertEqual(survey.created_by, actor)
            self.assertEqual(survey.project, self.active_project)
            self.assertEqual(survey.site, self.site)

        self.assertEqual(Survey.objects.filter(project=self.active_project).count(), 4)

    def test_unauthorized_roles_cannot_create_survey(self):
        blocked_users = (
            self.viewer,
            self.inactive_admin,
            self.other_manager,
        )
        unassigned_engineer = self.create_user(
            "unassigned-engineer@example.com",
            "engineer-3",
            UserRole.SURVEY_ENGINEER,
        )

        for blocked_user in (*blocked_users, unassigned_engineer):
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators, the owning project manager, and assigned survey engineers can create surveys.",
            ):
                create_survey(
                    actor=blocked_user,
                    project=self.active_project,
                    site=self.site,
                    name=f"Blocked {blocked_user.pk}",
                    survey_date=date(2026, 8, 9),
                )

        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_create_survey_rejects_project_site_mismatch_without_audit(self):
        with self.assertRaisesMessage(ValidationError, "Site must belong to the supplied project."):
            create_survey(
                actor=self.admin,
                project=self.active_project,
                site=self.other_site,
                name="Mismatch Survey",
                survey_date=date(2026, 8, 9),
            )

        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_create_survey_rejects_archived_project_without_audit(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Only active projects can have surveys created.",
        ):
            create_survey(
                actor=self.admin,
                project=self.archived_project,
                site=self.archived_site,
                name="Archived Survey",
                survey_date=date(2026, 8, 9),
            )

        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_inactive_owning_project_manager_cannot_create_survey(self):
        self.active_project.project_manager = self.inactive_owner_manager
        self.active_project.save(update_fields=["project_manager"])

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators, the owning project manager, and assigned survey engineers can create surveys.",
        ):
            create_survey(
                actor=self.inactive_owner_manager,
                project=self.active_project,
                site=self.site,
                name="Inactive Manager Survey",
                survey_date=date(2026, 8, 9),
            )

        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_inactive_assigned_survey_engineer_cannot_create_survey(self):
        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators, the owning project manager, and assigned survey engineers can create surveys.",
        ):
            create_survey(
                actor=self.inactive_assigned_engineer,
                project=self.active_project,
                site=self.site,
                name="Inactive Engineer Survey",
                survey_date=date(2026, 8, 9),
            )

        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_create_survey_sets_created_by_and_documented_defaults(self):
        survey = create_survey(
            actor=self.creator_engineer,
            project=self.active_project,
            site=self.site,
            name="Default Survey",
            survey_date=date(2026, 8, 9),
        )

        self.assertEqual(survey.created_by, self.creator_engineer)
        self.assertEqual(survey.status, SurveyStatus.DRAFT)
        self.assertEqual(survey.processing_status, "pending")
        self.assertEqual(survey.coordinate_reference_system, "EPSG:4326")
        self.assertFalse(Approval.objects.filter(survey=survey).exists())

    def test_create_survey_accepts_allowed_metadata_and_writes_audit(self):
        survey = create_survey(
            actor=self.admin,
            project=self.active_project,
            site=self.site,
            name="Metadata Survey",
            survey_date=date(2026, 8, 9),
            drone_model="DJI Mavic 3 Enterprise",
            pilot="John Doe",
            coordinate_reference_system="EPSG:32633",
            notes="Captured after rainfall.",
        )

        audit_log = AuditLog.objects.get()
        self.assertEqual(survey.drone_model, "DJI Mavic 3 Enterprise")
        self.assertEqual(survey.pilot, "John Doe")
        self.assertEqual(survey.coordinate_reference_system, "EPSG:32633")
        self.assertEqual(survey.notes, "Captured after rainfall.")
        self.assertEqual(audit_log.action, AuditAction.SURVEY_CREATED)
        self.assertEqual(audit_log.entity_type, "survey")
        self.assertEqual(audit_log.entity_id, survey.pk)
        self.assertEqual(audit_log.user, self.admin)
        self.assertEqual(audit_log.project, survey.project)
        self.assertEqual(audit_log.survey, survey)

    @patch("apps.surveys.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_create_survey_rolls_back_when_audit_write_fails(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            create_survey(
                actor=self.admin,
                project=self.active_project,
                site=self.site,
                name="Rollback Create",
                survey_date=date(2026, 8, 9),
            )

        mocked_record_audit_event.assert_called_once()
        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_authorized_roles_can_update_survey_metadata(self):
        updated_by_admin = update_survey(
            actor=self.admin,
            survey=self.survey,
            name="Admin Updated Survey",
            survey_date=date(2026, 8, 9),
        )
        self.survey.refresh_from_db()
        self.assertEqual(updated_by_admin.pk, self.survey.pk)
        self.assertEqual(self.survey.name, "Admin Updated Survey")
        self.assertEqual(self.survey.survey_date, date(2026, 8, 9))

        updated_by_manager = update_survey(
            actor=self.owner_manager,
            survey=self.survey,
            drone_model="DJI Inspire 3",
        )
        self.survey.refresh_from_db()
        self.assertEqual(updated_by_manager.pk, self.survey.pk)
        self.assertEqual(self.survey.drone_model, "DJI Inspire 3")

        updated_by_creator = update_survey(
            actor=self.creator_engineer,
            survey=self.survey,
            pilot="Updated Pilot",
            coordinate_reference_system="EPSG:3857",
            notes="Updated notes",
        )
        self.survey.refresh_from_db()
        self.assertEqual(updated_by_creator.pk, self.survey.pk)
        self.assertEqual(self.survey.pilot, "Updated Pilot")
        self.assertEqual(self.survey.coordinate_reference_system, "EPSG:3857")
        self.assertEqual(self.survey.notes, "Updated notes")

    def test_unauthorized_roles_cannot_update_survey_metadata(self):
        blocked_users = (
            self.other_manager,
            self.other_engineer,
            self.viewer,
            self.inactive_admin,
        )

        for blocked_user in blocked_users:
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators, the owning project manager, and the assigned creator survey engineer can update survey metadata.",
            ):
                update_survey(
                    actor=blocked_user,
                    survey=self.survey,
                    name=f"Blocked Update {blocked_user.pk}",
                )

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.name, "Existing Survey")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_inactive_owning_project_manager_cannot_update_survey_metadata(self):
        self.active_project.project_manager = self.inactive_owner_manager
        self.active_project.save(update_fields=["project_manager"])

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators, the owning project manager, and the assigned creator survey engineer can update survey metadata.",
        ):
            update_survey(
                actor=self.inactive_owner_manager,
                survey=self.survey,
                name="Inactive Manager Update",
            )

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.name, "Existing Survey")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_inactive_creator_survey_engineer_cannot_update_survey_metadata(self):
        self.survey.created_by = self.inactive_assigned_engineer
        self.survey.save(update_fields=["created_by"])

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators, the owning project manager, and the assigned creator survey engineer can update survey metadata.",
        ):
            update_survey(
                actor=self.inactive_assigned_engineer,
                survey=self.survey,
                name="Inactive Engineer Update",
            )

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.name, "Existing Survey")
        self.assertEqual(self.survey.created_by, self.inactive_assigned_engineer)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_creator_engineer_losing_membership_cannot_update_survey(self):
        ProjectMembership.objects.filter(
            project=self.active_project,
            user=self.creator_engineer,
        ).delete()

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators, the owning project manager, and the assigned creator survey engineer can update survey metadata.",
        ):
            update_survey(
                actor=self.creator_engineer,
                survey=self.survey,
                name="Lost Membership Update",
            )

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.name, "Existing Survey")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_update_survey_rejects_archived_project_without_audit(self):
        archived_survey = Survey.objects.create(
            project=self.archived_project,
            site=self.archived_site,
            name="Archived Existing Survey",
            survey_date=date(2026, 8, 7),
            created_by=self.creator_engineer,
        )
        ProjectMembership.objects.create(
            project=self.archived_project,
            user=self.creator_engineer,
            assigned_by=self.owner_manager,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Only active projects can have surveys updated.",
        ):
            update_survey(
                actor=self.admin,
                survey=archived_survey,
                name="Archived Update",
            )

        archived_survey.refresh_from_db()
        self.assertEqual(archived_survey.name, "Archived Existing Survey")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_update_survey_only_changes_allowed_metadata_fields_and_writes_audit(self):
        original_project_id = self.survey.project_id
        original_site_id = self.survey.site_id
        original_status = self.survey.status
        original_processing_status = self.survey.processing_status
        original_created_by_id = self.survey.created_by_id
        original_approved_by_id = self.survey.approved_by_id

        update_survey(
            actor=self.owner_manager,
            survey=self.survey,
            name="Allowed Update",
            survey_date=date(2026, 8, 9),
            drone_model=None,
            pilot=None,
            coordinate_reference_system="EPSG:32631",
            notes=None,
        )

        self.survey.refresh_from_db()
        audit_log = AuditLog.objects.get()
        self.assertEqual(self.survey.name, "Allowed Update")
        self.assertEqual(self.survey.survey_date, date(2026, 8, 9))
        self.assertIsNone(self.survey.drone_model)
        self.assertIsNone(self.survey.pilot)
        self.assertEqual(self.survey.coordinate_reference_system, "EPSG:32631")
        self.assertIsNone(self.survey.notes)
        self.assertEqual(self.survey.project_id, original_project_id)
        self.assertEqual(self.survey.site_id, original_site_id)
        self.assertEqual(self.survey.status, original_status)
        self.assertEqual(self.survey.processing_status, original_processing_status)
        self.assertEqual(self.survey.created_by_id, original_created_by_id)
        self.assertEqual(self.survey.approved_by_id, original_approved_by_id)
        self.assertEqual(audit_log.action, AuditAction.SURVEY_UPDATED)
        self.assertEqual(audit_log.entity_type, "survey")
        self.assertEqual(audit_log.entity_id, self.survey.pk)
        self.assertEqual(audit_log.user, self.owner_manager)
        self.assertEqual(audit_log.project, self.active_project)
        self.assertEqual(audit_log.survey, self.survey)

    def test_noop_update_returns_existing_survey_without_write_or_audit(self):
        original_updated_at = self.survey.updated_at

        returned_survey = update_survey(actor=self.admin, survey=self.survey)

        self.survey.refresh_from_db()
        self.assertEqual(returned_survey.pk, self.survey.pk)
        self.assertEqual(self.survey.updated_at, original_updated_at)
        self.assertEqual(AuditLog.objects.count(), 0)

    @patch("apps.surveys.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_update_survey_rolls_back_when_audit_write_fails(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            update_survey(
                actor=self.admin,
                survey=self.survey,
                name="Rollback Update",
            )

        mocked_record_audit_event.assert_called_once()
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.name, "Existing Survey")
        self.assertEqual(AuditLog.objects.count(), 0)
