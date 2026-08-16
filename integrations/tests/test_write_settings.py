from django.conf import settings
from django.test import SimpleTestCase, override_settings

from integrations.zoho.settings import ZohoSettings


class ZohoWriteSettingsTests(SimpleTestCase):
    def test_project_defaults_are_safe(self):
        self.assertFalse(settings.ZOHO_SANDBOX_WRITE_ENABLED)
        self.assertFalse(settings.ZOHO_PRODUCTION_WRITE_ENABLED)
        self.assertFalse(settings.COLECTIVOS_TASK_PUBLISH_ENABLED)
        self.assertEqual(settings.COLECTIVOS_TASK_WRITE_CONFIRMATION, "")

    @override_settings(ZOHO_SANDBOX_WRITE_ENABLED=False)
    def test_sdk_sees_sandbox_write_disabled(self):
        self.assertFalse(ZohoSettings.from_django("sandbox").write_enabled)

    @override_settings(ZOHO_SANDBOX_WRITE_ENABLED=True)
    def test_sdk_sees_sandbox_write_enabled(self):
        self.assertTrue(ZohoSettings.from_django("sandbox").write_enabled)

    @override_settings(ZOHO_PRODUCTION_WRITE_ENABLED=False)
    def test_sdk_sees_production_write_disabled(self):
        self.assertFalse(ZohoSettings.from_django("production").write_enabled)
