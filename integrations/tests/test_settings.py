from django.test import SimpleTestCase, override_settings

from integrations.zoho.constants import DEFAULT_SCOPES
from integrations.zoho.exceptions import ZohoConfigurationError
from integrations.zoho.settings import ZohoSettings, validate_api_domain
from integrations.tests.helpers import VALID_SETTINGS

VALID = VALID_SETTINGS


class ZohoSettingsTests(SimpleTestCase):
    @override_settings(**VALID)
    def test_valid_read_only_configuration(self):
        config = ZohoSettings.from_django().validate(require_refresh_token=True)
        self.assertTrue(config.enabled)
        self.assertEqual(config.scopes, DEFAULT_SCOPES)
        self.assertEqual(config.max_retries, 2)

    @override_settings(
        **{**VALID, "ZOHO_PRODUCTION_EXPECTED_ORG_ID": "expected-org-id"}
    )
    def test_expected_organization_id_is_optional_and_hidden_from_repr(self):
        config = ZohoSettings.from_django()
        self.assertEqual(config.expected_org_id, "expected-org-id")
        self.assertNotIn("expected-org-id", repr(config))

    @override_settings(**{**VALID, "ZOHO_ENABLED": False})
    def test_disabled_never_validates_as_available(self):
        with self.assertRaisesMessage(ZohoConfigurationError, "deshabilitada"):
            ZohoSettings.from_django().validate()

    @override_settings(**{**VALID, "ZOHO_CLIENT_SECRET": ""})
    def test_missing_credentials_are_safe(self):
        with self.assertRaises(ZohoConfigurationError) as caught:
            ZohoSettings.from_django().validate()
        self.assertNotIn("client-secret", str(caught.exception))

    @override_settings(**{**VALID, "ZOHO_REQUEST_TIMEOUT_SECONDS": "invalid"})
    def test_invalid_timeout(self):
        with self.assertRaisesMessage(ZohoConfigurationError, "número válido"):
            ZohoSettings.from_django()

    @override_settings(**{**VALID, "ZOHO_MAX_RETRIES": "99"})
    def test_invalid_retry_limit(self):
        with self.assertRaisesMessage(ZohoConfigurationError, "rango permitido"):
            ZohoSettings.from_django()

    @override_settings(**{**VALID, "ZOHO_API_BASE_URL": "http://attacker.invalid"})
    def test_invalid_api_url_rejected(self):
        with self.assertRaises(ZohoConfigurationError):
            ZohoSettings.from_django().validate()

    @override_settings(**{**VALID, "ZOHO_OAUTH_SCOPES": "ZohoCRM.modules.ALL"})
    def test_write_capable_scope_rejected(self):
        with self.assertRaisesMessage(ZohoConfigurationError, "exclusivamente de lectura"):
            ZohoSettings.from_django().validate()

    @override_settings(**VALID)
    def test_only_profile_data_center_domain_is_supported(self):
        self.assertEqual(
            validate_api_domain("https://www.zohoapis.com"),
            "https://www.zohoapis.com",
        )
        with self.assertRaises(ZohoConfigurationError):
            validate_api_domain("https://sandbox.zohoapis.eu")
        with self.assertRaises(ZohoConfigurationError):
            validate_api_domain("https://zoho.attacker.invalid")

    @override_settings(**{**VALID, "APP_ENV": "production"})
    def test_production_rejects_http_redirect_uri(self):
        with self.assertRaisesMessage(ZohoConfigurationError, "HTTPS"):
            ZohoSettings.from_django().validate()
