from .support import *


class ProjectApiMembershipMixin:
    def test_project_membership_endpoints_enforce_owner_admin_scope(self):
        with self.auth_settings():
            self.authenticate(self.assigned_viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_members = self.client.get(f"{self.projects_url}/{self.project.pk}/members")
            viewer_candidates = self.client.get(
                f"{self.projects_url}/{self.project.pk}/available-members"
            )
            viewer_add = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )

            self.authenticate(self.other_manager, enforce_csrf_checks=True)
            self.add_csrf()
            other_manager_remove = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.assigned_engineer.pk}"
            )

        for response in (viewer_members, viewer_candidates, viewer_add, other_manager_remove):
            self.assertEqual(response.status_code, 403)

    def test_admin_and_owner_can_list_members_and_available_candidates(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            admin_members = self.client.get(f"{self.projects_url}/{self.project.pk}/members")
            admin_candidates = self.client.get(
                f"{self.projects_url}/{self.project.pk}/available-members"
            )

            self.authenticate(self.owner_manager)
            owner_members = self.client.get(f"{self.projects_url}/{self.project.pk}/members")
            owner_candidates = self.client.get(
                f"{self.projects_url}/{self.project.pk}/available-members"
            )

        for response in (admin_members, admin_candidates, owner_members, owner_candidates):
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            admin_members.json(),
            [
                {
                    "id": self.assigned_engineer.pk,
                    "email": self.assigned_engineer.email,
                    "role": UserRole.SURVEY_ENGINEER,
                },
                {
                    "id": self.assigned_viewer.pk,
                    "email": self.assigned_viewer.email,
                    "role": UserRole.VIEWER,
                },
            ],
        )
        self.assertEqual(owner_members.json(), admin_members.json())
        self.assertEqual(
            admin_candidates.json(),
            [
                {
                    "id": self.unassigned_engineer.pk,
                    "email": self.unassigned_engineer.email,
                    "role": UserRole.SURVEY_ENGINEER,
                },
                {
                    "id": self.unassigned_viewer.pk,
                    "email": self.unassigned_viewer.email,
                    "role": UserRole.VIEWER,
                },
            ],
        )
        self.assertEqual(owner_candidates.json(), admin_candidates.json())

    def test_membership_add_remove_validation_archived_constraint_and_audit(self):
        inactive_viewer = self.create_user(
            "inactive-viewer@example.com",
            "viewer-3",
            UserRole.VIEWER,
            is_active=False,
        )

        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            added = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )
            duplicate = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )
            invalid_role = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.new_manager.pk},
                format="json",
            )
            inactive_target = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": inactive_viewer.pk},
                format="json",
            )
            removed = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.unassigned_engineer.pk}"
            )
            remove_missing = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.unassigned_engineer.pk}"
            )
            archived_members = self.client.get(
                f"{self.projects_url}/{self.archived_project.pk}/members"
            )
            archived_candidates = self.client.get(
                f"{self.projects_url}/{self.archived_project.pk}/available-members"
            )
            archived_add = self.client.post(
                f"{self.projects_url}/{self.archived_project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )

        self.assertEqual(added.status_code, 201)
        self.assertEqual(
            added.json(),
            {
                "id": self.unassigned_engineer.pk,
                "email": self.unassigned_engineer.email,
                "role": UserRole.SURVEY_ENGINEER,
            },
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(invalid_role.status_code, 400)
        self.assertEqual(inactive_target.status_code, 400)
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(remove_missing.status_code, 400)
        self.assertEqual(archived_members.status_code, 400)
        self.assertEqual(archived_candidates.status_code, 400)
        self.assertEqual(archived_add.status_code, 400)
        self.assertFalse(
            ProjectMembership.objects.filter(project=self.project, user=self.unassigned_engineer).exists()
        )
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("details", flat=True)),
            [
                {"operation": "added", "member_id": self.unassigned_engineer.pk},
                {"operation": "removed", "member_id": self.unassigned_engineer.pk},
            ],
        )

    def test_membership_mutations_require_valid_csrf(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            missing_csrf = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            token = self.add_csrf()
            allowed_post = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )
            allowed_delete = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.unassigned_engineer.pk}"
            )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed_post.status_code, 201)
        self.assertEqual(allowed_delete.status_code, 204)
        self.assertTrue(token)
