from unittest.mock import patch

from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.projects.models import Project, ProjectMembership, Site
from apps.projects.services import (
    add_project_member,
    archive_project,
    create_site,
    create_project,
    delete_site,
    remove_project_member,
    update_site,
    update_project,
    user_can_manage_project,
    user_can_view_project,
)


class ProjectModelsTestCase(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            email="manager@example.com",
            external_id="manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.creator = User.objects.create_user(
            email="creator@example.com",
            external_id="creator-1",
            role=UserRole.ADMINISTRATOR,
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            external_id="member-1",
            role=UserRole.SURVEY_ENGINEER,
        )

    def test_project_defaults_and_user_relationships(self):
        project = Project.objects.create(
            name="Lekki Site Survey",
            project_manager=self.manager,
            created_by=self.creator,
        )

        self.assertEqual(project.status, "active")
        self.assertEqual(project.project_manager, self.manager)
        self.assertEqual(project.created_by, self.creator)
        self.assertEqual(self.manager.projects_owned.get(), project)
        self.assertEqual(self.creator.projects_created.get(), project)

    def test_project_membership_duplicate_project_user_pair_is_rejected(self):
        project = Project.objects.create(name="Eko Atlantic Mapping")
        ProjectMembership.objects.create(project=project, user=self.member, assigned_by=self.manager)

        with self.assertRaises(IntegrityError):
            ProjectMembership.objects.create(project=project, user=self.member)

    def test_deleting_project_cascades_to_memberships_and_sites(self):
        project = Project.objects.create(name="Abuja Corridor Scan")
        membership = ProjectMembership.objects.create(
            project=project,
            user=self.member,
            assigned_by=self.manager,
        )
        site = Site.objects.create(
            project=project,
            name="Block A",
            coordinates=Point(3.4723, 6.4281, srid=4326),
        )

        project.delete()

        self.assertFalse(ProjectMembership.objects.filter(pk=membership.pk).exists())
        self.assertFalse(Site.objects.filter(pk=site.pk).exists())

    def test_site_coordinates_persist_as_point_with_srid_4326(self):
        project = Project.objects.create(name="Port Harcourt Survey")
        site = Site.objects.create(
            project=project,
            name="Jetty",
            coordinates=Point(7.0134, 4.8156, srid=4326),
        )

        site.refresh_from_db()

        self.assertIsInstance(site.coordinates, Point)
        self.assertEqual(site.coordinates.srid, 4326)
        self.assertAlmostEqual(site.coordinates.x, 7.0134)
        self.assertAlmostEqual(site.coordinates.y, 4.8156)


class ProjectAccessPolicyTestCase(TestCase):
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
        self.survey_engineer_assigned = self.create_user(
            "assigned-engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.survey_engineer_unassigned = self.create_user(
            "unassigned-engineer@example.com",
            "engineer-2",
            UserRole.SURVEY_ENGINEER,
        )
        self.viewer_assigned = self.create_user(
            "assigned-viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.viewer_unassigned = self.create_user(
            "unassigned-viewer@example.com",
            "viewer-2",
            UserRole.VIEWER,
        )
        self.inactive_admin = self.create_user(
            "inactive-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
            is_active=False,
        )

        self.project = Project.objects.create(
            name="Victoria Island Survey",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer_assigned,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.viewer_assigned,
            assigned_by=self.owner_manager,
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_administrator_can_view_and_manage_any_project(self):
        self.assertTrue(user_can_view_project(self.admin, self.project))
        self.assertTrue(user_can_manage_project(self.admin, self.project))

    def test_project_manager_can_view_and_manage_only_owned_project(self):
        self.assertTrue(user_can_view_project(self.owner_manager, self.project))
        self.assertTrue(user_can_manage_project(self.owner_manager, self.project))
        self.assertFalse(user_can_view_project(self.other_manager, self.project))
        self.assertFalse(user_can_manage_project(self.other_manager, self.project))

    def test_assigned_and_unassigned_survey_engineer_access(self):
        self.assertTrue(user_can_view_project(self.survey_engineer_assigned, self.project))
        self.assertFalse(user_can_view_project(self.survey_engineer_unassigned, self.project))
        self.assertFalse(user_can_manage_project(self.survey_engineer_assigned, self.project))
        self.assertFalse(user_can_manage_project(self.survey_engineer_unassigned, self.project))

    def test_assigned_and_unassigned_viewer_access(self):
        self.assertTrue(user_can_view_project(self.viewer_assigned, self.project))
        self.assertFalse(user_can_view_project(self.viewer_unassigned, self.project))
        self.assertFalse(user_can_manage_project(self.viewer_assigned, self.project))
        self.assertFalse(user_can_manage_project(self.viewer_unassigned, self.project))

    def test_inactive_user_cannot_view_or_manage_project(self):
        self.assertFalse(user_can_view_project(self.inactive_admin, self.project))
        self.assertFalse(user_can_manage_project(self.inactive_admin, self.project))


class CreateProjectServiceTests(TestCase):
    def setUp(self):
        self.admin = self.create_user("admin@example.com", "admin-1", UserRole.ADMINISTRATOR)
        self.project_manager = self.create_user(
            "manager@example.com",
            "manager-1",
            UserRole.PROJECT_MANAGER,
        )
        self.other_project_manager = self.create_user(
            "other-manager@example.com",
            "manager-2",
            UserRole.PROJECT_MANAGER,
        )
        self.inactive_project_manager = self.create_user(
            "inactive-manager@example.com",
            "manager-3",
            UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.survey_engineer = self.create_user(
            "engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.viewer = self.create_user(
            "viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.inactive_admin = self.create_user(
            "inactive-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
            is_active=False,
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_administrator_creates_project_with_assigned_active_project_manager(self):
        project = create_project(
            actor=self.admin,
            name="Lekki Phase 1 Road Expansion",
            description="Dualisation of the expressway",
            location="Lekki, Lagos",
            project_manager=self.project_manager,
        )

        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(project.project_manager, self.project_manager)
        self.assertEqual(project.created_by, self.admin)
        self.assertEqual(project.name, "Lekki Phase 1 Road Expansion")
        self.assertEqual(project.description, "Dualisation of the expressway")
        self.assertEqual(project.location, "Lekki, Lagos")

    def test_project_manager_creates_project_and_becomes_manager(self):
        project = create_project(
            actor=self.project_manager,
            name="Victoria Island Towers",
            project_manager=self.other_project_manager,
        )

        self.assertEqual(project.project_manager, self.project_manager)
        self.assertEqual(project.created_by, self.project_manager)

    def test_project_created_audit_record_is_written_correctly(self):
        project = create_project(
            actor=self.admin,
            name="Audit Trail Project",
            project_manager=self.project_manager,
        )

        audit_log = AuditLog.objects.get()

        self.assertEqual(audit_log.action, AuditAction.PROJECT_CREATED)
        self.assertEqual(audit_log.entity_type, "project")
        self.assertEqual(audit_log.entity_id, project.pk)
        self.assertEqual(audit_log.user, self.admin)
        self.assertEqual(audit_log.project, project)
        self.assertIsNone(audit_log.survey)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_project_creation_rolls_back_if_audit_write_fails(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            create_project(
                actor=self.admin,
                name="Atomicity Project",
                project_manager=self.project_manager,
            )

        mocked_record_audit_event.assert_called_once()
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_administrator_creation_without_project_manager_is_rejected(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Administrators must assign a project manager when creating a project.",
        ):
            create_project(actor=self.admin, name="No Manager Project")

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_assigning_non_project_manager_is_rejected(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Assigned project manager must have the PROJECT_MANAGER role.",
        ):
            create_project(
                actor=self.admin,
                name="Invalid Manager Project",
                project_manager=self.survey_engineer,
            )

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_assigning_inactive_project_manager_is_rejected(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Project manager must be active.",
        ):
            create_project(
                actor=self.admin,
                name="Inactive Manager Project",
                project_manager=self.inactive_project_manager,
            )

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_survey_engineer_viewer_and_inactive_users_cannot_create_projects(self):
        blocked_users = (
            self.survey_engineer,
            self.viewer,
            self.inactive_admin,
        )

        for blocked_user in blocked_users:
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and project managers can create projects.",
            ):
                create_project(
                    actor=blocked_user,
                    name=f"Blocked Project {blocked_user.pk}",
                    project_manager=self.project_manager,
                )

        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)


class UpdateProjectServiceTests(TestCase):
    def setUp(self):
        self.admin = self.create_user("admin@example.com", "admin-1", UserRole.ADMINISTRATOR)
        self.owner_manager = self.create_user(
            "owner-manager@example.com",
            "manager-1",
            UserRole.PROJECT_MANAGER,
        )
        self.new_manager = self.create_user(
            "new-manager@example.com",
            "manager-2",
            UserRole.PROJECT_MANAGER,
        )
        self.other_manager = self.create_user(
            "other-manager@example.com",
            "manager-3",
            UserRole.PROJECT_MANAGER,
        )
        self.inactive_manager = self.create_user(
            "inactive-manager@example.com",
            "manager-4",
            UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.survey_engineer = self.create_user(
            "engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.viewer = self.create_user(
            "viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.inactive_user = self.create_user(
            "inactive-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
            is_active=False,
        )
        self.project = Project.objects.create(
            name="Victoria Island Survey",
            description="Initial description",
            location="Lagos",
            project_manager=self.owner_manager,
            created_by=self.admin,
            status="active",
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_administrator_updates_project_metadata(self):
        project = update_project(
            actor=self.admin,
            project=self.project,
            name="Updated Project Name",
            description="Updated description",
            location="Abuja",
        )

        project.refresh_from_db()

        self.assertEqual(project.name, "Updated Project Name")
        self.assertEqual(project.description, "Updated description")
        self.assertEqual(project.location, "Abuja")
        self.assertEqual(project.project_manager, self.owner_manager)
        self.assertEqual(project.created_by, self.admin)
        self.assertEqual(project.status, "active")

    def test_owning_project_manager_updates_project_metadata(self):
        project = update_project(
            actor=self.owner_manager,
            project=self.project,
            name="Manager Updated Name",
            description=None,
            location=None,
        )

        project.refresh_from_db()

        self.assertEqual(project.name, "Manager Updated Name")
        self.assertIsNone(project.description)
        self.assertIsNone(project.location)
        self.assertEqual(project.project_manager, self.owner_manager)

    def test_non_owning_project_manager_is_denied(self):
        with self.assertRaisesMessage(
            PermissionDenied,
            "Only active administrators and the owning project manager can update a project.",
        ):
            update_project(
                actor=self.other_manager,
                project=self.project,
                name="Blocked Update",
            )

    def test_survey_engineer_viewer_and_inactive_user_are_denied(self):
        blocked_users = (self.survey_engineer, self.viewer, self.inactive_user)

        for blocked_user in blocked_users:
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can update a project.",
            ):
                update_project(
                    actor=blocked_user,
                    project=self.project,
                    name=f"Blocked Update {blocked_user.pk}",
                )

    def test_administrator_transfers_ownership_to_active_project_manager(self):
        project = update_project(
            actor=self.admin,
            project=self.project,
            project_manager=self.new_manager,
        )

        project.refresh_from_db()

        self.assertEqual(project.project_manager, self.new_manager)
        self.assertFalse(user_can_manage_project(self.owner_manager, project))
        self.assertTrue(user_can_manage_project(self.new_manager, project))

    def test_owning_project_manager_transfers_ownership_to_another_project_manager(self):
        project = update_project(
            actor=self.owner_manager,
            project=self.project,
            project_manager=self.new_manager,
        )

        project.refresh_from_db()

        self.assertEqual(project.project_manager, self.new_manager)
        self.assertFalse(user_can_manage_project(self.owner_manager, project))
        self.assertTrue(user_can_manage_project(self.new_manager, project))

    def test_inactive_or_non_project_manager_transfer_target_is_rejected(self):
        for invalid_target, message in (
            (self.inactive_manager, "Project manager must be active."),
            (self.survey_engineer, "Assigned project manager must have the PROJECT_MANAGER role."),
        ):
            with self.assertRaisesMessage(ValidationError, message):
                update_project(
                    actor=self.admin,
                    project=self.project,
                    project_manager=invalid_target,
                )

        self.project.refresh_from_db()
        self.assertEqual(self.project.project_manager, self.owner_manager)

    def test_project_updated_audit_record_is_correct(self):
        update_project(
            actor=self.admin,
            project=self.project,
            name="Audit Updated Project",
            project_manager=self.new_manager,
        )

        audit_log = AuditLog.objects.get()
        self.project.refresh_from_db()

        self.assertEqual(audit_log.action, AuditAction.PROJECT_UPDATED)
        self.assertEqual(audit_log.entity_type, "project")
        self.assertEqual(audit_log.entity_id, self.project.pk)
        self.assertEqual(audit_log.user, self.admin)
        self.assertEqual(audit_log.project, self.project)
        self.assertIsNone(audit_log.survey)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_write_failure_rolls_back_project_update(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            update_project(
                actor=self.admin,
                project=self.project,
                name="Should Roll Back",
                location="Abuja",
                project_manager=self.new_manager,
            )

        mocked_record_audit_event.assert_called_once()
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "Victoria Island Survey")
        self.assertEqual(self.project.location, "Lagos")
        self.assertEqual(self.project.project_manager, self.owner_manager)
        self.assertEqual(AuditLog.objects.count(), 0)


class ArchiveProjectServiceTests(TestCase):
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
        self.survey_engineer = self.create_user(
            "engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.viewer = self.create_user(
            "viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.inactive_admin = self.create_user(
            "inactive-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
            is_active=False,
        )
        self.project = Project.objects.create(
            name="Victoria Island Survey",
            description="Initial description",
            location="Lagos",
            project_manager=self.owner_manager,
            created_by=self.admin,
            status="active",
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_administrator_can_archive_a_project(self):
        project = archive_project(actor=self.admin, project=self.project)

        project.refresh_from_db()

        self.assertEqual(project.status, "archived")

    def test_owning_project_manager_can_archive_their_project(self):
        project = archive_project(actor=self.owner_manager, project=self.project)

        project.refresh_from_db()

        self.assertEqual(project.status, "archived")

    def test_non_owners_and_inactive_user_are_denied(self):
        blocked_users = (
            self.other_manager,
            self.survey_engineer,
            self.viewer,
            self.inactive_admin,
        )

        for blocked_user in blocked_users:
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can archive a project.",
            ):
                archive_project(actor=blocked_user, project=self.project)

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "active")
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_archiving_sets_only_status_and_preserves_project_record(self):
        original_id = self.project.pk
        original_name = self.project.name
        original_description = self.project.description
        original_location = self.project.location
        original_manager = self.project.project_manager
        original_creator = self.project.created_by

        archive_project(actor=self.admin, project=self.project)

        persisted_project = Project.objects.get(pk=original_id)

        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(persisted_project.status, "archived")
        self.assertEqual(persisted_project.name, original_name)
        self.assertEqual(persisted_project.description, original_description)
        self.assertEqual(persisted_project.location, original_location)
        self.assertEqual(persisted_project.project_manager, original_manager)
        self.assertEqual(persisted_project.created_by, original_creator)

    def test_already_archived_project_is_rejected_without_another_audit_event(self):
        archive_project(actor=self.admin, project=self.project)
        self.project.refresh_from_db()

        with self.assertRaisesMessage(ValidationError, "Only active projects can be archived."):
            archive_project(actor=self.admin, project=self.project)

        self.assertEqual(self.project.status, "archived")
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROJECT_ARCHIVED).count(),
            1,
        )

    def test_project_archived_audit_record_is_correct(self):
        archive_project(actor=self.admin, project=self.project)

        audit_log = AuditLog.objects.get()
        self.project.refresh_from_db()

        self.assertEqual(audit_log.action, AuditAction.PROJECT_ARCHIVED)
        self.assertEqual(audit_log.entity_type, "project")
        self.assertEqual(audit_log.entity_id, self.project.pk)
        self.assertEqual(audit_log.user, self.admin)
        self.assertEqual(audit_log.project, self.project)
        self.assertIsNone(audit_log.survey)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_write_failure_rolls_back_archive_status_change(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            archive_project(actor=self.admin, project=self.project)

        mocked_record_audit_event.assert_called_once()
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "active")
        self.assertEqual(AuditLog.objects.count(), 0)


class ProjectMembershipServiceTests(TestCase):
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
        self.survey_engineer = self.create_user(
            "engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.viewer = self.create_user(
            "viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.inactive_user = self.create_user(
            "inactive-user@example.com",
            "inactive-1",
            UserRole.VIEWER,
            is_active=False,
        )
        self.admin_target = self.create_user(
            "target-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
        )
        self.manager_target = self.create_user(
            "target-manager@example.com",
            "manager-3",
            UserRole.PROJECT_MANAGER,
        )
        self.project = Project.objects.create(
            name="Victoria Island Survey",
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

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_administrator_can_add_and_remove_valid_member(self):
        membership = add_project_member(
            actor=self.admin,
            project=self.project,
            member=self.survey_engineer,
        )

        self.assertEqual(membership.project, self.project)
        self.assertEqual(membership.user, self.survey_engineer)
        self.assertEqual(membership.assigned_by, self.admin)
        self.assertTrue(
            ProjectMembership.objects.filter(project=self.project, user=self.survey_engineer).exists()
        )

        remove_project_member(
            actor=self.admin,
            project=self.project,
            member=self.survey_engineer,
        )

        self.assertFalse(
            ProjectMembership.objects.filter(project=self.project, user=self.survey_engineer).exists()
        )

    def test_owning_project_manager_can_add_and_remove_valid_member(self):
        membership = add_project_member(
            actor=self.owner_manager,
            project=self.project,
            member=self.viewer,
        )

        self.assertEqual(membership.assigned_by, self.owner_manager)

        remove_project_member(
            actor=self.owner_manager,
            project=self.project,
            member=self.viewer,
        )

        self.assertFalse(ProjectMembership.objects.filter(project=self.project, user=self.viewer).exists())

    def test_non_owning_project_manager_survey_engineer_viewer_and_inactive_user_are_denied(self):
        blocked_users = (
            self.other_manager,
            self.survey_engineer,
            self.viewer,
            self.inactive_user,
        )

        for blocked_user in blocked_users:
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage project membership.",
            ):
                add_project_member(actor=blocked_user, project=self.project, member=self.viewer)
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage project membership.",
            ):
                remove_project_member(
                    actor=blocked_user,
                    project=self.project,
                    member=self.viewer,
                )

        self.assertEqual(ProjectMembership.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_inactive_administrator_and_project_manager_targets_are_rejected(self):
        invalid_targets = (
            (self.inactive_user, "Project members must be active users."),
            (
                self.admin_target,
                "Project members must have the SURVEY_ENGINEER or VIEWER role.",
            ),
            (
                self.manager_target,
                "Project members must have the SURVEY_ENGINEER or VIEWER role.",
            ),
        )

        for target, message in invalid_targets:
            with self.assertRaisesMessage(ValidationError, message):
                add_project_member(actor=self.admin, project=self.project, member=target)

        self.assertEqual(ProjectMembership.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_duplicate_addition_is_rejected_without_an_audit_event(self):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )

        with self.assertRaisesMessage(ValidationError, "User is already a member of this project."):
            add_project_member(
                actor=self.admin,
                project=self.project,
                member=self.survey_engineer,
            )

        self.assertEqual(ProjectMembership.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_removal_of_non_member_is_rejected_without_an_audit_event(self):
        with self.assertRaisesMessage(ValidationError, "User is not a member of this project."):
            remove_project_member(
                actor=self.admin,
                project=self.project,
                member=self.survey_engineer,
            )

        self.assertEqual(ProjectMembership.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_archived_projects_reject_additions_and_removals_without_audit_events(self):
        ProjectMembership.objects.create(
            project=self.archived_project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Only active projects can have membership changes.",
        ):
            add_project_member(
                actor=self.admin,
                project=self.archived_project,
                member=self.viewer,
            )

        with self.assertRaisesMessage(
            ValidationError,
            "Only active projects can have membership changes.",
        ):
            remove_project_member(
                actor=self.admin,
                project=self.archived_project,
                member=self.survey_engineer,
            )

        self.assertTrue(
            ProjectMembership.objects.filter(project=self.archived_project, user=self.survey_engineer).exists()
        )
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_add_and_remove_audit_records_have_expected_action_actor_project_and_details(self):
        add_project_member(
            actor=self.admin,
            project=self.project,
            member=self.survey_engineer,
        )
        remove_project_member(
            actor=self.admin,
            project=self.project,
            member=self.survey_engineer,
        )

        audit_logs = list(AuditLog.objects.order_by("id"))

        self.assertEqual(len(audit_logs), 2)

        add_audit = audit_logs[0]
        self.assertEqual(add_audit.action, AuditAction.PROJECT_UPDATED)
        self.assertEqual(add_audit.project, self.project)
        self.assertEqual(add_audit.user, self.admin)
        self.assertEqual(add_audit.details, {"operation": "added", "member_id": self.survey_engineer.pk})

        remove_audit = audit_logs[1]
        self.assertEqual(remove_audit.action, AuditAction.PROJECT_UPDATED)
        self.assertEqual(remove_audit.project, self.project)
        self.assertEqual(remove_audit.user, self.admin)
        self.assertEqual(
            remove_audit.details,
            {"operation": "removed", "member_id": self.survey_engineer.pk},
        )

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_write_failure_rolls_back_member_addition(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            add_project_member(
                actor=self.admin,
                project=self.project,
                member=self.survey_engineer,
            )

        mocked_record_audit_event.assert_called_once()
        self.assertFalse(
            ProjectMembership.objects.filter(project=self.project, user=self.survey_engineer).exists()
        )
        self.assertEqual(AuditLog.objects.count(), 0)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_write_failure_rolls_back_member_removal(self, mocked_record_audit_event):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )

        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            remove_project_member(
                actor=self.admin,
                project=self.project,
                member=self.survey_engineer,
            )

        mocked_record_audit_event.assert_called_once()
        self.assertTrue(
            ProjectMembership.objects.filter(project=self.project, user=self.survey_engineer).exists()
        )
        self.assertEqual(AuditLog.objects.count(), 0)


class SiteManagementServiceTests(TestCase):
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
        self.survey_engineer = self.create_user(
            "engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.viewer = self.create_user(
            "viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.inactive_admin = self.create_user(
            "inactive-admin@example.com",
            "admin-2",
            UserRole.ADMINISTRATOR,
            is_active=False,
        )
        self.inactive_owner_manager = self.create_user(
            "inactive-owner-manager@example.com",
            "manager-3",
            UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.project = Project.objects.create(
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
            project=self.project,
            name="Existing Site",
            coordinates=Point(3.4723, 6.4281, srid=4326),
        )
        self.archived_site = Site.objects.create(
            project=self.archived_project,
            name="Archived Site",
            coordinates=Point(3.5, 6.5, srid=4326),
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def test_administrator_can_create_update_and_delete_site(self):
        site = create_site(
            actor=self.admin,
            project=self.project,
            name="Admin Created Site",
            coordinates=Point(3.6, 6.4, srid=4326),
        )

        self.assertEqual(site.project, self.project)
        self.assertEqual(site.coordinate_reference_system, "EPSG:4326")

        update_site(
            actor=self.admin,
            site=site,
            name="Admin Updated Site",
            coordinates=Point(3.7, 6.45, srid=4326),
            coordinate_reference_system="EPSG:3857",
        )
        site.refresh_from_db()
        self.assertEqual(site.name, "Admin Updated Site")
        self.assertEqual(site.coordinates.srid, 4326)
        self.assertAlmostEqual(site.coordinates.x, 3.7)
        self.assertAlmostEqual(site.coordinates.y, 6.45)
        self.assertEqual(site.coordinate_reference_system, "EPSG:3857")

        delete_site(actor=self.admin, site=site)
        self.assertFalse(Site.objects.filter(pk=site.pk).exists())

    def test_owning_project_manager_can_create_update_and_delete_site(self):
        site = create_site(
            actor=self.owner_manager,
            project=self.project,
            name="Manager Site",
            coordinates=Point(3.61, 6.41, srid=4326),
        )

        update_site(
            actor=self.owner_manager,
            site=site,
            name="Manager Site Updated",
        )
        site.refresh_from_db()
        self.assertEqual(site.name, "Manager Site Updated")

        delete_site(actor=self.owner_manager, site=site)
        self.assertFalse(Site.objects.filter(pk=site.pk).exists())

    def test_survey_engineer_viewer_inactive_users_and_non_owning_project_manager_are_denied(self):
        blocked_users = (
            self.survey_engineer,
            self.viewer,
            self.inactive_admin,
            self.inactive_owner_manager,
            self.other_manager,
        )

        for blocked_user in blocked_users:
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage sites.",
            ):
                create_site(
                    actor=blocked_user,
                    project=self.project,
                    name=f"Blocked Site {blocked_user.pk}",
                    coordinates=Point(3.62, 6.42, srid=4326),
                )
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage sites.",
            ):
                update_site(
                    actor=blocked_user,
                    site=self.site,
                    name=f"Blocked Update {blocked_user.pk}",
                )
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage sites.",
            ):
                delete_site(actor=blocked_user, site=self.site)

        self.assertEqual(Site.objects.filter(project=self.project).count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_archived_project_rejects_create_update_and_delete_without_audit_event(self):
        with self.assertRaisesMessage(ValidationError, "Only active projects can have site changes."):
            create_site(
                actor=self.admin,
                project=self.archived_project,
                name="Archived Create",
                coordinates=Point(3.63, 6.43, srid=4326),
            )

        with self.assertRaisesMessage(ValidationError, "Only active projects can have site changes."):
            update_site(
                actor=self.admin,
                site=self.archived_site,
                name="Archived Update",
            )

        with self.assertRaisesMessage(ValidationError, "Only active projects can have site changes."):
            delete_site(actor=self.admin, site=self.archived_site)

        self.assertTrue(Site.objects.filter(pk=self.archived_site.pk).exists())
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_non_4326_coordinates_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Site coordinates must use SRID 4326."):
            create_site(
                actor=self.admin,
                project=self.project,
                name="Invalid Coordinates Create",
                coordinates=Point(3.64, 6.44, srid=3857),
            )

        with self.assertRaisesMessage(ValidationError, "Site coordinates must use SRID 4326."):
            update_site(
                actor=self.admin,
                site=self.site,
                coordinates=Point(3.65, 6.45, srid=3857),
            )

        self.site.refresh_from_db()
        self.assertAlmostEqual(self.site.coordinates.x, 3.4723)
        self.assertAlmostEqual(self.site.coordinates.y, 6.4281)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_create_update_and_delete_write_expected_audit_records(self):
        created_site = create_site(
            actor=self.admin,
            project=self.project,
            name="Audited Site",
            coordinates=Point(3.66, 6.46, srid=4326),
        )
        created_site_id = created_site.pk
        update_site(
            actor=self.owner_manager,
            site=created_site,
            name="Audited Site Updated",
        )
        delete_site(actor=self.admin, site=created_site)

        audit_logs = list(AuditLog.objects.order_by("id"))
        self.assertEqual(len(audit_logs), 3)

        for audit_log, expected_action, expected_user in (
            (audit_logs[0], AuditAction.SITE_CREATED, self.admin),
            (audit_logs[1], AuditAction.SITE_UPDATED, self.owner_manager),
            (audit_logs[2], AuditAction.SITE_DELETED, self.admin),
        ):
            self.assertEqual(audit_log.action, expected_action)
            self.assertEqual(audit_log.entity_type, "site")
            self.assertEqual(audit_log.entity_id, created_site_id)
            self.assertEqual(audit_log.user, expected_user)
            self.assertEqual(audit_log.project, self.project)
            self.assertIsNone(audit_log.survey)

    def test_noop_update_returns_existing_site_without_write_or_audit(self):
        original_updated_at = self.site.updated_at

        returned_site = update_site(actor=self.admin, site=self.site)

        self.site.refresh_from_db()
        self.assertEqual(returned_site.pk, self.site.pk)
        self.assertEqual(self.site.updated_at, original_updated_at)
        self.assertEqual(AuditLog.objects.count(), 0)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_failure_rolls_back_site_create(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            create_site(
                actor=self.admin,
                project=self.project,
                name="Rollback Create",
                coordinates=Point(3.67, 6.47, srid=4326),
            )

        mocked_record_audit_event.assert_called_once()
        self.assertEqual(Site.objects.filter(project=self.project).count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_failure_rolls_back_site_update(self, mocked_record_audit_event):
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            update_site(
                actor=self.admin,
                site=self.site,
                name="Rollback Update",
                coordinates=Point(3.68, 6.48, srid=4326),
            )

        mocked_record_audit_event.assert_called_once()
        self.site.refresh_from_db()
        self.assertEqual(self.site.name, "Existing Site")
        self.assertAlmostEqual(self.site.coordinates.x, 3.4723)
        self.assertAlmostEqual(self.site.coordinates.y, 6.4281)
        self.assertEqual(AuditLog.objects.count(), 0)

    @patch("apps.projects.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_failure_rolls_back_site_delete(self, mocked_record_audit_event):
        site_id = self.site.pk
        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            delete_site(actor=self.admin, site=self.site)

        mocked_record_audit_event.assert_called_once()
        self.assertTrue(Site.objects.filter(pk=site_id).exists())
        self.assertEqual(AuditLog.objects.count(), 0)
