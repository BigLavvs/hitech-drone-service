from .support import *


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

    def test_stale_active_project_instance_cannot_archive_twice(self):
        stale_instance = Project.objects.get(pk=self.project.pk)

        archive_project(actor=self.admin, project=self.project)

        with self.assertRaisesMessage(ValidationError, "Only active projects can be archived."):
            archive_project(actor=self.admin, project=stale_instance)

        self.assertEqual(AuditLog.objects.filter(action=AuditAction.PROJECT_ARCHIVED).count(), 1)

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
