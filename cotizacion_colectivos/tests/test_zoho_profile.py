from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from integrations.zoho.exceptions import ZohoConfigurationError
from integrations.zoho.schemas import Organization

from cotizacion_colectivos.zoho import (
    cached_metadata_fields,
    cached_metadata_modules,
    get_colectivos_environment,
    get_colectivos_profile,
    get_colectivos_zoho,
    normalize_colectivos_profile,
)


class FakeOrganizationFacade:
    def __init__(self, environment):
        self.environment = environment
        self.calls = 0

    def get(self):
        self.calls += 1
        return Organization("safe-org", "Organización", environment=self.environment)


def facade(profile, reported_environment=None):
    return SimpleNamespace(
        profile=profile,
        environment=profile,
        organization=FakeOrganizationFacade(
            profile if reported_environment is None else reported_environment
        ),
    )


class ColectivosProfileConfigurationTests(SimpleTestCase):
    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
    def test_default_is_sandbox(self):
        self.assertEqual(settings.ZOHO_ACTIVE_PROFILE, "sandbox")
        self.assertEqual(get_colectivos_profile(), "sandbox")
        obsolete_name = "COTIZACION_" + "COLECTIVOS_ZOHO_PROFILE"
        self.assertFalse(hasattr(settings, obsolete_name))

    def test_sandbox_and_production_are_valid(self):
        self.assertEqual(normalize_colectivos_profile("sandbox"), "sandbox")
        self.assertEqual(normalize_colectivos_profile("production"), "production")

    def test_spaces_and_case_are_normalized(self):
        self.assertEqual(normalize_colectivos_profile("  ProDuction  "), "production")
        with override_settings(ZOHO_ACTIVE_PROFILE=" SANDBOX "):
            self.assertEqual(get_colectivos_profile(), "sandbox")

    def test_invalid_and_empty_values_fail_closed(self):
        for value in ("", "qa", "future", "production,sandbox"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                normalize_colectivos_profile(value)

    def test_environment_ui_is_closed_and_safe(self):
        with override_settings(ZOHO_ACTIVE_PROFILE="production"):
            self.assertEqual(
                get_colectivos_environment(),
                {"profile": "production", "label": "Producción", "css_class": "production"},
            )


class ColectivosProfileResolutionTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_sandbox_calls_only_sandbox_and_validates_environment_once(self, get_zoho):
        selected = facade("sandbox")
        get_zoho.return_value = selected
        with override_settings(ZOHO_ACTIVE_PROFILE="sandbox"):
            self.assertIs(get_colectivos_zoho(), selected)
        get_zoho.assert_called_once_with(profile="sandbox")
        self.assertEqual(selected.organization.calls, 1)

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_production_calls_only_production(self, get_zoho):
        selected = facade("production")
        get_zoho.return_value = selected
        with self.assertLogs("cotizacion_colectivos", level="INFO") as captured:
            with override_settings(ZOHO_ACTIVE_PROFILE="production"):
                self.assertIs(get_colectivos_zoho(), selected)
        get_zoho.assert_called_once_with(profile="production")
        output = " ".join(captured.output)
        self.assertIn("operation=organization", output)
        self.assertIn("duration_ms=", output)
        self.assertNotIn("safe-org", output)

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_organization_validation_is_cached_for_five_minutes(self, get_zoho):
        selected = facade("sandbox")
        get_zoho.return_value = selected
        timings = {}
        with override_settings(
            ZOHO_ACTIVE_PROFILE="sandbox",
            COLECTIVOS_ORGANIZATION_CACHE_TTL_SECONDS=300,
        ):
            get_colectivos_zoho(timings=timings)
            get_colectivos_zoho(timings=timings)
        self.assertEqual(selected.organization.calls, 1)
        self.assertEqual(timings["organization_cache_hit"], 1)
        self.assertEqual(timings["organization_ms"], 0)

    def test_metadata_is_cached_by_profile_and_backend(self):
        metadata = SimpleNamespace(
            module_calls=0,
            field_calls=0,
        )

        def list_modules():
            metadata.module_calls += 1
            return ("Contacts",)

        def list_fields(module):
            metadata.field_calls += 1
            return (f"{module}.id",)

        metadata.list_modules = list_modules
        metadata.list_fields = list_fields
        selected = SimpleNamespace(
            profile="sandbox", backend_name="sdk", metadata=metadata
        )
        with override_settings(COLECTIVOS_METADATA_CACHE_TTL_SECONDS=1800):
            self.assertEqual(cached_metadata_modules(selected), ("Contacts",))
            self.assertEqual(cached_metadata_modules(selected), ("Contacts",))
            self.assertEqual(cached_metadata_fields(selected, "Contacts"), ("Contacts.id",))
            self.assertEqual(cached_metadata_fields(selected, "Contacts"), ("Contacts.id",))
        self.assertEqual(metadata.module_calls, 1)
        self.assertEqual(metadata.field_calls, 1)

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_mismatched_reported_environment_is_blocked_without_fallback(self, get_zoho):
        get_zoho.return_value = facade("sandbox", "production")
        with override_settings(ZOHO_ACTIVE_PROFILE="sandbox"):
            with self.assertRaises(ZohoConfigurationError):
                get_colectivos_zoho()
        get_zoho.assert_called_once_with(profile="sandbox")

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_production_rejects_sandbox_environment_without_fallback(self, get_zoho):
        get_zoho.return_value = facade("production", "sandbox")
        with override_settings(ZOHO_ACTIVE_PROFILE="production"):
            with self.assertRaises(ZohoConfigurationError):
                get_colectivos_zoho()
        get_zoho.assert_called_once_with(profile="production")

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_unknown_or_missing_environment_is_blocked(self, get_zoho):
        for environment in ("", "developer", "unknown"):
            with self.subTest(environment=environment):
                get_zoho.reset_mock()
                get_zoho.return_value = facade("production", environment)
                with override_settings(ZOHO_ACTIVE_PROFILE="production"):
                    with self.assertRaises(ZohoConfigurationError):
                        get_colectivos_zoho()
                get_zoho.assert_called_once_with(profile="production")

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_configuration_error_never_tries_other_profile(self, get_zoho):
        get_zoho.side_effect = ZohoConfigurationError("secret configuration")
        with override_settings(ZOHO_ACTIVE_PROFILE="production"):
            with self.assertRaises(ZohoConfigurationError):
                get_colectivos_zoho()
        get_zoho.assert_called_once_with(profile="production")

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_obsolete_application_variable_is_ignored(self, get_zoho):
        selected = facade("sandbox")
        get_zoho.return_value = selected
        obsolete_name = "COTIZACION_" + "COLECTIVOS_ZOHO_PROFILE"
        with override_settings(
            ZOHO_ACTIVE_PROFILE="sandbox",
            **{obsolete_name: "production"},
        ):
            self.assertIs(get_colectivos_zoho(), selected)
        get_zoho.assert_called_once_with(profile="sandbox")

    def test_templates_do_not_hardcode_sandbox_badge(self):
        template = (
            Path(settings.BASE_DIR) / "templates" / "cotizacion_colectivos" / "base.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Sandbox · Solo lectura", template)
        self.assertIn("zoho_environment.label", template)
