from .support import *


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
