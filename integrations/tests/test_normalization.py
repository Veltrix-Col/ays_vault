from __future__ import annotations

from django.test import SimpleTestCase
from zohocrmsdk.src.com.zoho.crm.api.util.choice import Choice

from integrations.zoho.exceptions import ZohoInvalidResponseError
from integrations.zoho.normalization import (
    normalize_choice_text,
    normalize_organization_environment,
    safe_value_class,
)


class GetterValue:
    def __init__(self, value):
        self._value = value

    def get_value(self):
        return self._value


class AttributeValue:
    def __init__(self, value):
        self.value = value


class ZohoNormalizationTests(SimpleTestCase):
    def test_official_choice_sandbox_normalizes(self):
        value = Choice("sandbox")
        self.assertEqual(normalize_organization_environment(value), "sandbox")
        self.assertEqual(safe_value_class(value), "Choice")

    def test_string_normalizes(self):
        self.assertEqual(
            normalize_organization_environment("sandbox"),
            "sandbox",
        )

    def test_get_value_object_normalizes(self):
        self.assertEqual(
            normalize_organization_environment(GetterValue("developer")),
            "developer",
        )

    def test_value_attribute_object_normalizes(self):
        self.assertEqual(
            normalize_organization_environment(AttributeValue("bigin")),
            "bigin",
        )

    def test_whitespace_and_case_normalize(self):
        self.assertEqual(
            normalize_organization_environment("  SANDBOX  "),
            "sandbox",
        )

    def test_missing_value_fails_closed(self):
        with self.assertRaises(ZohoInvalidResponseError):
            normalize_organization_environment(None)

    def test_unknown_value_fails_closed(self):
        with self.assertRaises(ZohoInvalidResponseError):
            normalize_organization_environment("other")

    def test_generated_type_choice_is_plain_text(self):
        value = normalize_choice_text(
            Choice("internal"),
            field="module.generated_type",
        )
        self.assertEqual(value, "internal")
        self.assertNotIn("Choice object", value)
