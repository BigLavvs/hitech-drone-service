from django.test import SimpleTestCase, override_settings

from config.middleware import _build_csp_header


class ContentSecurityPolicyTests(SimpleTestCase):
    def test_nonce_based_policy_does_not_enable_inline_scripts_or_styles(self):
        header = _build_csp_header("synthetic-nonce")

        self.assertIn("style-src-elem 'self' https://cdn.jsdelivr.net 'nonce-synthetic-nonce'", header)
        self.assertNotIn("'unsafe-inline'", header)
        self.assertNotIn("'unsafe-eval'", header)

    @override_settings(R2_ENDPOINT_URL="https://account.r2.cloudflarestorage.com", R2_PUBLIC_URL="")
    def test_configured_r2_origin_and_blob_data_resources_remain_allowed(self):
        header = _build_csp_header("synthetic-nonce")

        self.assertIn("img-src 'self' https://cdn.jsdelivr.net data: blob: https://account.r2.cloudflarestorage.com", header)
        self.assertIn("connect-src 'self' blob: https://account.r2.cloudflarestorage.com", header)
        self.assertIn("media-src 'self' blob: https://account.r2.cloudflarestorage.com", header)
