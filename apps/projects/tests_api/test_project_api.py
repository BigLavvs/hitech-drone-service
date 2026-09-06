from .support import *


class ProjectApiProjectMixin:
    def test_project_list_filters_by_role_and_assignment(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            admin_response = self.client.get(self.projects_url)

            self.authenticate(self.owner_manager)
            owner_response = self.client.get(self.projects_url)

            self.authenticate(self.assigned_engineer)
            assigned_engineer_response = self.client.get(self.projects_url)

            self.authenticate(self.assigned_viewer)
            assigned_viewer_response = self.client.get(self.projects_url)

            self.authenticate(self.unassigned_engineer)
            unassigned_engineer_response = self.client.get(self.projects_url)

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["count"], 3)

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in owner_response.json()["results"]],
            [self.project.id, self.archived_project.id],
        )
        self.assertEqual(
            [item["id"] for item in assigned_engineer_response.json()["results"]],
            [self.project.id],
        )
        self.assertEqual(
            [item["id"] for item in assigned_viewer_response.json()["results"]],
            [self.project.id],
        )
        self.assertEqual(unassigned_engineer_response.json()["count"], 0)

    def test_administrator_can_create_project_and_audit_is_written(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.projects_url,
                {
                    "name": "Lekki Phase 1 Road Expansion",
                    "description": "Dualisation",
                    "location": "Lekki, Lagos",
                    "project_manager_id": self.new_manager.pk,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["project_manager_id"], self.new_manager.pk)
        self.assertEqual(body["created_by"], self.admin.pk)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(AuditLog.objects.get().action, AuditAction.PROJECT_CREATED)

    def test_project_manager_can_create_project_and_is_assigned_as_manager(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.projects_url,
                {"name": "PM Created Project"},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["project_manager_id"], self.owner_manager.pk)

    def test_administrator_project_manager_assignment_validation(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            missing_manager = self.client.post(
                self.projects_url,
                {"name": "Missing Manager"},
                format="json",
            )
            inactive_manager = self.client.post(
                self.projects_url,
                {
                    "name": "Inactive Manager",
                    "project_manager_id": self.inactive_manager.pk,
                },
                format="json",
            )

        self.assertEqual(missing_manager.status_code, 400)
        self.assertEqual(inactive_manager.status_code, 400)

    def test_create_payloads_require_documented_fields(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            missing_project_name = self.client.post(
                self.projects_url,
                {"project_manager_id": self.new_manager.pk},
                format="json",
            )
            missing_site_name = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {"coordinates": {"lat": 6.4, "lng": 3.4}},
                format="json",
            )
            missing_site_coordinates = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {"name": "Incomplete Site"},
                format="json",
            )

        self.assertEqual(missing_project_name.status_code, 400)
        self.assertEqual(missing_site_name.status_code, 400)
        self.assertEqual(missing_site_coordinates.status_code, 400)

    def test_unexpected_write_field_is_rejected_with_400(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            project_response = self.client.post(
                self.projects_url,
                {
                    "name": "Unexpected Field Project",
                    "project_manager_id": self.new_manager.pk,
                    "status": "archived",
                },
                format="json",
            )
            site_response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}/sites/{self.site.pk}",
                {"project_id": self.other_project.pk},
                format="json",
            )

        self.assertEqual(project_response.status_code, 400)
        self.assertEqual(site_response.status_code, 400)

    def test_invalid_coordinate_ranges_and_conflicting_crs_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            invalid_latitude = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Invalid Latitude",
                    "coordinates": {"lat": 91, "lng": 3.4},
                },
                format="json",
            )
            invalid_longitude = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Invalid Longitude",
                    "coordinates": {"lat": 6.4, "lng": 181},
                },
                format="json",
            )
            conflicting_crs = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Invalid CRS",
                    "coordinates": {"lat": 6.4, "lng": 3.4},
                    "coordinate_reference_system": "EPSG:3857",
                },
                format="json",
            )

        self.assertEqual(invalid_latitude.status_code, 400)
        self.assertEqual(invalid_longitude.status_code, 400)
        self.assertEqual(conflicting_crs.status_code, 400)

    def test_string_coordinate_values_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "String Coordinates",
                    "coordinates": {"lat": "6.4", "lng": "3.4"},
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)

    def test_non_finite_coordinate_values_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                '{"name":"Non Finite Coordinates","coordinates":{"lat":NaN,"lng":3.4}}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)

    def test_unknown_write_field_is_rejected_for_form_style_request_data(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.projects_url,
                {
                    "name": "Form Project",
                    "project_manager_id": str(self.new_manager.pk),
                    "unexpected": "value",
                },
            )

        self.assertEqual(response.status_code, 400)

    def test_owner_can_update_and_archive_without_hard_delete(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            update_response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}",
                {"name": "Updated Project Name"},
                format="json",
            )
            archive_response = self.client.delete(f"{self.projects_url}/{self.project.pk}")

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(archive_response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "archived")
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("action", flat=True)),
            [AuditAction.PROJECT_UPDATED, AuditAction.PROJECT_ARCHIVED],
        )

    def test_project_manager_reassignment_attempt_is_rejected(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}",
                {"project_manager_id": self.new_manager.pk},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.project_manager, self.owner_manager)
