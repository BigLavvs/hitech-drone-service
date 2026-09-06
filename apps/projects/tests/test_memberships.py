from .support import *


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

    def test_authorized_users_can_list_current_members_and_available_candidates(self):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )
        extra_engineer = self.create_user(
            "extra-engineer@example.com",
            "engineer-2",
            UserRole.SURVEY_ENGINEER,
        )

        for actor in (self.admin, self.owner_manager):
            with self.subTest(actor=actor.role):
                members = list(get_project_members(actor=actor, project=self.project))
                candidates = list(get_available_project_members(actor=actor, project=self.project))
                self.assertEqual([membership.user_id for membership in members], [self.survey_engineer.pk])
                self.assertEqual(
                    [(candidate.email, candidate.role) for candidate in candidates],
                    [
                        (extra_engineer.email, UserRole.SURVEY_ENGINEER),
                        (self.viewer.email, UserRole.VIEWER),
                    ],
                )

    def test_project_member_user_id_interface_returns_concrete_ids(self):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )

        member_user_ids = get_project_member_user_ids(project_id=self.project.pk)

        self.assertIsInstance(member_user_ids, list)
        self.assertEqual(member_user_ids, [self.survey_engineer.pk])

    @patch("apps.access_control.services.get_active_assignable_project_users")
    def test_available_member_path_passes_concrete_ids_to_access_control(
        self, mocked_get_active_assignable_project_users
    ):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )

        get_available_project_members(actor=self.admin, project=self.project)

        mocked_get_active_assignable_project_users.assert_called_once_with(
            exclude_user_ids=[self.survey_engineer.pk]
        )

    @patch("apps.access_control.services.get_active_assignable_project_users")
    def test_project_scoped_access_control_path_receives_concrete_ids(
        self, mocked_get_active_assignable_project_users
    ):
        from apps.access_control.services import get_active_assignable_project_users_for_project

        ProjectMembership.objects.create(
            project=self.project,
            user=self.survey_engineer,
            assigned_by=self.owner_manager,
        )

        get_active_assignable_project_users_for_project(project=self.project)

        mocked_get_active_assignable_project_users.assert_called_once_with(
            exclude_user_ids=[self.survey_engineer.pk]
        )

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
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage project membership.",
            ):
                list(get_project_members(actor=blocked_user, project=self.project))
            with self.assertRaisesMessage(
                PermissionDenied,
                "Only active administrators and the owning project manager can manage project membership.",
            ):
                list(get_available_project_members(actor=blocked_user, project=self.project))

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
        with self.assertRaisesMessage(
            ValidationError,
            "Only active projects can have membership changes.",
        ):
            list(get_project_members(actor=self.admin, project=self.archived_project))
        with self.assertRaisesMessage(
            ValidationError,
            "Only active projects can have membership changes.",
        ):
            list(get_available_project_members(actor=self.admin, project=self.archived_project))

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
