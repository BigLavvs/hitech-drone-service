from django.test import SimpleTestCase


class RootRouteTests(SimpleTestCase):
    def test_root_redirects_to_established_login_entry_point(self):
        response = self.client.get("/")

        self.assertRedirects(response, "/login")
