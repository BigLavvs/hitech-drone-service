from pathlib import Path

from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase, override_settings


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class SurveyWorkspaceTemplateTests(SimpleTestCase):
    def test_survey_detail_includes_upload_guidance_and_filters(self):
        request = RequestFactory().get("/surveys/12")
        html = get_template("survey_detail.html").render(
            {
                "page_title": "Survey 12",
                "survey_id": 12,
                "active_nav": "projects",
            },
            request,
        )

        self.assertIn("Supported 2D files:", html)
        self.assertIn("Supported 3D files:", html)
        self.assertIn("Maximum file size: 10 GB", html)
        self.assertIn("Maximum combined survey uploads: 50 GB", html)
        self.assertIn(
            'accept=".tif,.tiff,.png,.jpg,.jpeg,.kml,.geojson,.obj,.glb,.gltf,.las,.laz,.ply,.stl"',
            html,
        )
        self.assertIn('accept=".mtl,.png,.jpg,.jpeg"', html)
        self.assertIn("data-upload-assets-field", html)
        self.assertIn("data-approval-guidance", html)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class SurveyWorkspaceJavaScriptTests(SimpleTestCase):
    def test_survey_workspace_contains_client_side_upload_filtering_hooks(self):
        script = Path("static/js/survey-workspace.js").read_text(encoding="utf-8")

        self.assertIn(
            'const PRIMARY_UPLOAD_ACCEPT =\n  ".tif,.tiff,.png,.jpg,.jpeg,.kml,.geojson,.obj,.glb,.gltf,.las,.laz,.ply,.stl";',
            script,
        )
        self.assertIn('const OBJ_ASSET_ACCEPT = ".mtl,.png,.jpg,.jpeg";', script)
        self.assertIn("handlePrimaryFileChange()", script)
        self.assertIn("handleAssetFileChange()", script)
        self.assertIn("syncUploadAssetField()", script)
        self.assertIn("Unsupported primary file type selected.", script)
        self.assertIn("Unsupported OBJ asset type selected.", script)
        self.assertIn("OBJ assets are allowed only when the primary file is .obj.", script)
        self.assertIn("self-review is not permitted for the survey creator.", script)
