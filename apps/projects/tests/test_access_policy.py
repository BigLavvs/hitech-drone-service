from .support import *


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
