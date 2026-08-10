from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase, override_settings


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class TemplateSyntaxTests(SimpleTestCase):
    """Ensure shared templates remain parseable as the feature pages are added."""

    template_names = [
        "base.html",
        "admin.html",
        "includes/header.html",
        "includes/sidebar.html",
        "components/page_header.html",
        "components/card.html",
        "components/button.html",
        "components/form_field.html",
        "components/data_table.html",
        "components/status_badge.html",
        "components/empty_state.html",
        "components/loading_state.html",
        "components/error_state.html",
        "components/modal.html",
    ]

    def test_shared_templates_parse_and_render(self):
        request = RequestFactory().get("/")
        context = {
            "page_heading": "Template check",
            "field_id": "name",
            "field_name": "name",
            "field_label": "Name",
            "table_headings": ["Name"],
            "table_rows": [["Example"]],
            "modal_id": "example-modal",
            "modal_title": "Example modal",
        }
        for template_name in self.template_names:
            with self.subTest(template=template_name):
                self.assertIsInstance(get_template(template_name).render(context, request), str)
