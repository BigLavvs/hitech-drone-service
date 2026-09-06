from .support import *


class ProjectApiSiteMixin:
    def test_site_crud_permissions_and_coordinate_representation(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            create_response = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Created Site",
                    "coordinates": {"lat": 6.5001, "lng": 3.6002},
                    "coordinate_reference_system": "EPSG:4326",
                },
                format="json",
            )

            site_id = create_response.json()["id"]
            read_response = self.client.get(f"{self.projects_url}/{self.project.pk}/sites/{site_id}")
            update_response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}/sites/{site_id}",
                {"coordinates": {"lat": 6.7001, "lng": 3.8002}},
                format="json",
            )
            updated_site = Site.objects.get(pk=site_id)
            delete_response = self.client.delete(f"{self.projects_url}/{self.project.pk}/sites/{site_id}")

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 204)

        self.assertAlmostEqual(updated_site.coordinates.x, 3.8002)
        self.assertAlmostEqual(updated_site.coordinates.y, 6.7001)
        self.assertEqual(read_response.json()["coordinates"], {"lat": 6.5001, "lng": 3.6002})
        self.assertFalse(Site.objects.filter(pk=site_id).exists())

    def test_site_read_only_users_and_archived_project_write_rules(self):
        with self.auth_settings():
            self.authenticate(self.assigned_viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_post = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {"name": "Blocked", "coordinates": {"lat": 6.4, "lng": 3.4}},
                format="json",
            )
            viewer_patch = self.client.patch(
                f"{self.projects_url}/{self.project.pk}/sites/{self.site.pk}",
                {"name": "Blocked"},
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            archived_post = self.client.post(
                f"{self.projects_url}/{self.archived_project.pk}/sites",
                {"name": "Blocked", "coordinates": {"lat": 6.4, "lng": 3.4}},
                format="json",
            )

        self.assertEqual(viewer_post.status_code, 403)
        self.assertEqual(viewer_patch.status_code, 403)
        self.assertEqual(archived_post.status_code, 400)
