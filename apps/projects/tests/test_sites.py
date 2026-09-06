from .support import *


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
