from .support import *


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
