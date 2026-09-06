from .support import *


class ProjectApiPaginationMixin:
    def test_limit_offset_pagination_on_collections(self):
        for index in range(25):
            Project.objects.create(
                name=f"Admin Project {index}",
                project_manager=self.other_manager,
                created_by=self.admin,
            )

        for index in range(25):
            Site.objects.create(
                project=self.project,
                name=f"Site {index}",
                coordinates=Point(3.0 + index / 1000, 6.0 + index / 1000, srid=4326),
            )

        with self.auth_settings():
            self.authenticate(self.admin)
            project_response = self.client.get(f"{self.projects_url}?limit=5&offset=10")
            site_response = self.client.get(
                f"{self.projects_url}/{self.project.pk}/sites?limit=5&offset=10"
            )

        self.assertEqual(project_response.status_code, 200)
        self.assertEqual(len(project_response.json()["results"]), 5)
        self.assertIn("next", project_response.json())
        self.assertEqual(site_response.status_code, 200)
        self.assertEqual(len(site_response.json()["results"]), 5)
