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
        self.assertIn("<code>.geojson</code>, <code>.json</code>, <code>.geo.json</code>", html)
        self.assertIn("Supported 3D files:", html)
        self.assertIn("Maximum file size: 10 GB", html)
        self.assertIn("Maximum combined survey uploads: 50 GB", html)
        self.assertIn(
            'accept=".tif,.tiff,.png,.jpg,.jpeg,.kml,.geojson,.json,.geo.json,.obj,.glb,.gltf,.las,.laz,.ply,.stl"',
            html,
        )
        self.assertIn('class="survey-upload-form" data-upload-form hidden', html)
        self.assertIn("data-upload-primary-help", html)
        self.assertIn("data-upload-primary-selection", html)
        self.assertIn("data-upload-assets-field", html)
        self.assertIn('data-upload-assets-field hidden', html)
        self.assertIn("data-upload-gltf-folder-field", html)
        self.assertIn('id="survey-gltf-folder" type="file" webkitdirectory directory multiple disabled', html)
        self.assertIn("data-upload-gltf-folder-help", html)
        self.assertIn("data-upload-gltf-folder-selection", html)
        self.assertIn("data-upload-assets-picker-help", html)
        self.assertIn('name="assets" type="file" multiple disabled', html)
        self.assertIn("data-upload-assets-label", html)
        self.assertIn("data-upload-assets-help", html)
        self.assertIn("data-upload-assets-selection", html)
        self.assertIn("data-upload-assets-list", html)
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
        helper = Path("static/js/modules/gltf-bundle.js").read_text(encoding="utf-8")

        self.assertIn(
            'const PRIMARY_UPLOAD_ACCEPT =\n  ".tif,.tiff,.png,.jpg,.jpeg,.kml,.geojson,.json,.geo.json,.obj,.glb,.gltf,.las,.laz,.ply,.stl";',
            script,
        )
        self.assertIn("const RELATED_ASSET_RULES = {", script)
        self.assertIn('".gltf": {', script)
        self.assertIn('label: "GLTF related assets for a .gltf primary file",', script)
        self.assertIn(
            'help: "Fallback picker: choose every referenced .bin, .png, .jpg, or .jpeg file directly if folder selection is unavailable.",',
            script,
        )
        self.assertIn('accept: ".bin,.png,.jpg,.jpeg",', script)
        self.assertIn("handlePrimaryFileChange()", script)
        self.assertIn("handleAssetFileChange()", script)
        self.assertIn("handleGltfFolderChange()", script)
        self.assertIn("syncUploadAssetField()", script)
        self.assertIn("renderPrimarySelection()", script)
        self.assertIn("renderAssetSelection()", script)
        self.assertIn("renderGltfFolderSelection()", script)
        self.assertIn("handleUploadSelectionClick(event)", script)
        self.assertIn('this.assetField.hidden = !assetRule;', script)
        self.assertIn('this.assetFileInput.disabled = !assetRule;', script)
        self.assertIn('const showGltfFolder = currentExtension === ".gltf";', script)
        self.assertIn('this.assetFileInput.removeAttribute("accept");', script)
        self.assertIn(
            'appendRelatedAssetsToFormData(formData, this.selectedAssetFiles);',
            script,
        )
        self.assertIn('this.assetPickerHelp.textContent = "Add related assets";', script)
        self.assertIn('this.assetPickerHelp.textContent = "Choose related assets";', script)
        self.assertIn('this.primaryFileHelp.textContent = "Choose or replace the primary survey dataset file.";', script)
        self.assertIn('this.selectedAssets = mergeRelatedAssetSelections(this.selectedAssets, assets);', script)
        self.assertIn('this.selectedAssets = removeRelatedAssetSelection(this.selectedAssets, key);', script)
        self.assertIn('removeAction: "remove-asset",', script)
        self.assertIn("Related assets are allowed only when the primary file is .obj or .gltf.", script)
        self.assertIn(
            "GLTF bundle folder selection is available only for a .gltf primary file.",
            script,
        )
        self.assertIn("Unsupported primary file type selected.", script)
        self.assertIn("self-review is not permitted for the survey creator.", script)
        self.assertIn("export function selectReferencedGltfBundleAssets", helper)
        self.assertIn("export function appendRelatedAssetsToFormData", helper)
        self.assertIn("export function mergeRelatedAssetSelections", helper)
        self.assertIn("export function removeRelatedAssetSelection", helper)
        self.assertIn(
            'return `${label} ${sourceLabel}: ${displayNames.join(", ")}`;',
            helper,
        )

    def test_global_hidden_rule_preserves_dynamic_visibility_controls(self):
        stylesheet = Path("static/css/app.css").read_text(encoding="utf-8")

        self.assertIn("[hidden] { display: none !important; }", stylesheet)
