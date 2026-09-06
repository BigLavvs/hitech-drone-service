from .support import *


class ProjectApiAuthorizationCsrfMixin:
    def test_unauthenticated_requests_return_401(self):
        with self.auth_settings():
            responses = (
                self.client.get(self.projects_url),
                self.client.get(f"{self.projects_url}/{self.project.pk}"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/members"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/available-members"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/sites"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/sites/{self.site.pk}"),
            )

        for response in responses:
            self.assertEqual(response.status_code, 401)

    def test_forbidden_cross_project_reads_and_writes_return_403(self):
        with self.auth_settings():
            self.authenticate(self.assigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()

            forbidden_project_read = self.client.get(f"{self.projects_url}/{self.other_project.pk}")
            forbidden_site_read = self.client.get(
                f"{self.projects_url}/{self.project.pk}/sites/{self.other_site.pk}"
            )
            forbidden_project_write = self.client.patch(
                f"{self.projects_url}/{self.other_project.pk}",
                {"name": "Blocked"},
                format="json",
            )
            forbidden_site_write = self.client.patch(
                f"{self.projects_url}/{self.other_project.pk}/sites/{self.other_site.pk}",
                {"name": "Blocked"},
                format="json",
            )

        for response in (
            forbidden_project_read,
            forbidden_site_read,
            forbidden_project_write,
            forbidden_site_write,
        ):
            self.assertEqual(response.status_code, 403)

    def test_unsafe_authenticated_requests_require_valid_csrf_token(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            missing_csrf = self.client.post(
                self.projects_url,
                {
                    "name": "Blocked Without CSRF",
                    "project_manager_id": self.new_manager.pk,
                },
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            token = self.add_csrf()
            allowed = self.client.post(
                self.projects_url,
                {
                    "name": "Allowed With CSRF",
                    "project_manager_id": self.new_manager.pk,
                },
                format="json",
            )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed.status_code, 201)
        self.assertTrue(token)

    def test_template_pages_issue_csrf_cookie(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
