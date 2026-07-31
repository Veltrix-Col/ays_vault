from __future__ import annotations

import logging
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from zohocrmsdk.src.com.zoho.crm.api.util.choice import Choice

from integrations.zoho.backends.sdk import SDKBackend
from integrations.zoho.exceptions import (
    ZohoAuthenticationError,
    ZohoConfigurationError,
    ZohoSDKError,
    ZohoValidationError,
)
from integrations.zoho.facade import ZohoFacade
from integrations.zoho.factory import get_zoho, reset_zoho_for_tests
from integrations.zoho.schemas import (
    FieldMetadata,
    ModuleMetadata,
    Organization,
    Page,
)
from integrations.zoho.sdk.initializer import initialize_sdk, reset_sdk_for_tests
from integrations.zoho.sdk.response_parser import response_object
from integrations.zoho.sdk.token_store import InMemorySDKTokenStore
from integrations.zoho.settings import ZohoSettings

VALID = {
    "ZOHO_ACTIVE_PROFILE": "production",
    "ZOHO_ENABLED": True,
    "ZOHO_PRODUCTION_CLIENT_ID": "",
    "ZOHO_PRODUCTION_CLIENT_SECRET": "",
    "ZOHO_PRODUCTION_REFRESH_TOKEN": "",
    "ZOHO_SANDBOX_ENABLED": False,
    "ZOHO_SANDBOX_CLIENT_ID": "",
    "ZOHO_SANDBOX_CLIENT_SECRET": "",
    "ZOHO_SANDBOX_REFRESH_TOKEN": "",
    "ZOHO_CLIENT_ID": "client-id-test",
    "ZOHO_CLIENT_SECRET": "client-secret-test",
    "ZOHO_REFRESH_TOKEN": "refresh-token-test",
    "ZOHO_REDIRECT_URI": "http://localhost:8000/integrations/zoho/callback/",
    "ZOHO_ACCOUNTS_BASE_URL": "https://accounts.zoho.com",
    "ZOHO_API_BASE_URL": "https://www.zohoapis.com",
    "ZOHO_OAUTH_SCOPES": "ZohoCRM.modules.READ",
    "ZOHO_REQUEST_TIMEOUT_SECONDS": "15",
    "ZOHO_MAX_RETRIES": "2",
    "ZOHO_BACKEND": "sdk",
    "ZOHO_SDK_LOG_LEVEL": "INFO",
}


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.calls = []

    def get_organization(self):
        return Organization("1", "AYS Seguros", environment="production")

    def list_modules(self):
        return (ModuleMetadata("Polizas", "Polizas", "Pólizas", "Póliza"),)

    def list_fields(self, module):
        return (FieldMetadata("Name", "Nombre", "text"),)

    def list_records(self, module, *, fields, page, limit):
        self.calls.append(("list", module, tuple(fields), page, limit))
        return Page(({"id": "1"},), page=page, count=1)

    def get_record_by_id(self, module, record_id, *, fields):
        return {"id": record_id}

    def search(self, module, *, criteria, fields, page, limit):
        self.calls.append(("search", criteria))
        return Page((), page=page)

    def execute_coql(self, query, *, offset, limit):
        self.calls.append(("coql", query))
        return Page(())


class FakeSDKObject:
    def __init__(self, **values):
        self.values = values

    def __getattr__(self, name):
        if name.startswith("get_"):
            key = name[4:]
            return lambda: self.values.get(key)
        raise AttributeError(name)


class FakeSDKResponse:
    def __init__(self, value=None, status=200):
        self.value = value
        self.status = status

    def get_status_code(self):
        return self.status

    def get_object(self):
        return self.value


@override_settings(**VALID)
class FacadeContractTests(SimpleTestCase):
    def test_public_contract_is_sdk_independent(self):
        backend = FakeBackend()
        zoho = ZohoFacade(backend)
        self.assertEqual(zoho.organization.get().company_name, "AYS Seguros")
        self.assertEqual(zoho.metadata.list_modules()[0].api_name, "Polizas")
        self.assertEqual(
            zoho.records.list(module="Polizas", fields=["id"], limit=20).count, 1
        )
        self.assertEqual(
            zoho.records.get_by_id(module="Polizas", record_id="123")["id"], "123"
        )
        zoho.search.by_field(
            module="Contacts",
            field="Numero_de_documento",
            value="12,3",
        )
        self.assertIn(r"12\,3", backend.calls[-1][1])

    def test_no_write_methods_are_exposed(self):
        zoho = ZohoFacade(FakeBackend())
        forbidden = {
            "create", "update", "delete", "upsert", "save", "write",
            "upload", "attach", "execute_function",
        }
        for service in (
            zoho.organization,
            zoho.metadata,
            zoho.records,
            zoho.search,
            zoho.coql,
        ):
            self.assertTrue(forbidden.isdisjoint(dir(service)))


class SDKConfigurationTests(SimpleTestCase):
    def tearDown(self):
        reset_zoho_for_tests()

    @override_settings(**VALID)
    def test_sdk_is_default_and_factory_is_lazy(self):
        with patch("integrations.zoho.backends.sdk.initialize_sdk") as initialize:
            zoho = get_zoho()
        self.assertEqual(zoho.backend_name, "sdk")
        initialize.assert_not_called()

    @override_settings(**{**VALID, "ZOHO_BACKEND": "rest"})
    def test_rest_backend_can_be_selected(self):
        self.assertEqual(get_zoho().backend_name, "rest")

    @override_settings(**{**VALID, "ZOHO_BACKEND": "arbitrary"})
    def test_invalid_backend_is_rejected(self):
        with self.assertRaises(ZohoConfigurationError):
            ZohoSettings.from_django()

    @override_settings(**{**VALID, "ZOHO_SDK_LOG_LEVEL": "TRACE"})
    def test_invalid_log_level_is_rejected(self):
        with self.assertRaises(ZohoConfigurationError):
            ZohoSettings.from_django()

    @override_settings(**{**VALID, "ZOHO_ENABLED": False})
    def test_disabled_configuration_can_be_loaded_without_initializing(self):
        self.assertFalse(ZohoSettings.from_django().enabled)


class SDKTokenStoreTests(SimpleTestCase):
    def test_access_token_is_memory_only_and_refresh_is_not_replaced(self):
        permanent = "permanent-refresh"
        store = InMemorySDKTokenStore(permanent)
        token = Mock()
        token.get_id.return_value = "token-1"
        store.save_token(token)
        token.set_refresh_token.assert_called_once_with(permanent)
        self.assertIs(store.find_token(token), token)
        self.assertEqual(store.get_tokens(), [token])

    def test_delete_does_not_touch_files_or_database(self):
        store = InMemorySDKTokenStore("refresh")
        store.delete_tokens()
        self.assertEqual(store.get_tokens(), [])
        self.assertFalse(hasattr(store, "file_path"))
        self.assertFalse(hasattr(store, "database"))


class SDKInitializerTests(SimpleTestCase):
    def tearDown(self):
        reset_sdk_for_tests()

    @override_settings(**VALID)
    def test_initializes_once_even_with_concurrent_callers(self):
        with TemporaryDirectory() as directory, override_settings(
            ZOHO_SDK_RESOURCE_PATH=directory
        ), patch(
            "zohocrmsdk.src.com.zoho.crm.api.initializer.Initializer.initialize"
        ) as initialize:
            results = []
            threads = [threading.Thread(target=lambda: results.append(initialize_sdk())) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(results), 4)
            self.assertEqual(initialize.call_count, 1)
            self.assertEqual(len({id(item) for item in results}), 1)

    @override_settings(**{**VALID, "ZOHO_REFRESH_TOKEN": ""})
    def test_missing_refresh_token_fails_safely(self):
        with self.assertRaises(ZohoConfigurationError) as caught:
            initialize_sdk()
        self.assertNotIn("client-secret-test", str(caught.exception))

    @override_settings(**VALID)
    def test_sdk_initialization_error_does_not_expose_secret(self):
        with TemporaryDirectory() as directory, override_settings(
            ZOHO_SDK_RESOURCE_PATH=directory
        ), patch(
            "zohocrmsdk.src.com.zoho.crm.api.initializer.Initializer.initialize",
            side_effect=RuntimeError("secret should not propagate"),
        ), self.assertLogs("integrations.zoho", logging.ERROR) as logs:
            with self.assertRaises(ZohoSDKError) as caught:
                initialize_sdk()
        combined = str(caught.exception) + " ".join(logs.output)
        self.assertNotIn("client-secret-test", combined)
        self.assertNotIn("refresh-token-test", combined)


class SDKResponseAndFallbackTests(SimpleTestCase):
    def test_response_status_401_is_normalized(self):
        response = SimpleNamespace(
            get_status_code=lambda: 401,
            get_object=lambda: SimpleNamespace(get_code=lambda: "INVALID_TOKEN"),
        )
        with self.assertRaises(ZohoAuthenticationError):
            response_object(response, operation="organization")

    @override_settings(**VALID)
    def test_fields_fall_back_only_for_sdk_compatibility_error(self):
        rest = Mock()
        rest.list_fields.return_value = (FieldMetadata("Name", "Name", "text"),)
        backend = SDKBackend(rest_fallback=rest, initializer=lambda _config: None)
        with patch.object(
            backend, "_call", side_effect=ZohoSDKError("unsupported")
        ):
            result = backend.list_fields("Polizas")
        self.assertEqual(result[0].api_name, "Name")
        rest.list_fields.assert_called_once_with("Polizas")

    @override_settings(**VALID)
    def test_authentication_error_never_falls_back(self):
        rest = Mock()
        backend = SDKBackend(rest_fallback=rest, initializer=lambda _config: None)
        with patch.object(
            backend, "_call", side_effect=ZohoAuthenticationError("invalid")
        ):
            with self.assertRaises(ZohoAuthenticationError):
                backend.list_fields("Polizas")
        rest.list_fields.assert_not_called()

    @override_settings(**VALID)
    def test_coql_uses_hardened_rest_fallback(self):
        rest = Mock()
        rest.execute_coql.return_value = Page(())
        backend = SDKBackend(rest_fallback=rest, initializer=lambda _config: None)
        backend.execute_coql("select id from Polizas", limit=10)
        rest.execute_coql.assert_called_once_with(
            "select id from Polizas", offset=0, limit=10
        )

    @override_settings(**VALID)
    def test_validation_blocks_invalid_modules_before_sdk(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=Mock())
        with self.assertRaises(ZohoValidationError):
            backend.list_records("../Contacts", fields=["id"])

    @override_settings(**VALID)
    def test_sdk_normalizes_organization(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        org = FakeSDKObject(
            id="org-1",
            company_name="AYS Seguros",
            country="Colombia",
            time_zone="America/Bogota",
            currency="COP",
            type=Choice("sandbox"),
        )
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.org.org_operations.OrgOperations.get_organization",
            return_value=FakeSDKResponse(FakeSDKObject(org=[org])),
        ), self.assertLogs("integrations.zoho", "INFO") as logs:
            result = backend.get_organization()
        self.assertEqual(result.organization_id, "org-1")
        self.assertEqual(result.company_name, "AYS Seguros")
        self.assertEqual(result.environment, "sandbox")
        diagnostic = " ".join(logs.output)
        self.assertIn("clase_valor=Choice", diagnostic)
        self.assertIn("valor_normalizado=sandbox", diagnostic)
        self.assertNotIn("<Choice object at", diagnostic)

    @override_settings(**VALID)
    def test_sdk_normalizes_custom_and_internal_modules(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        modules = [
            FakeSDKObject(
                api_name="Polizas",
                module_name="Polizas",
                plural_label="Pólizas",
                singular_label="Póliza",
                custom_module=True,
                api_supported=True,
                status="visible",
            ),
            FakeSDKObject(
                api_name="Internal",
                module_name="Internal",
                plural_label="Internal",
                singular_label="Internal",
                custom_module=False,
                api_supported=False,
                generated_type=Choice("internal"),
                status="hidden",
            ),
        ]
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.modules.modules_operations.ModulesOperations.get_modules",
            return_value=FakeSDKResponse(FakeSDKObject(modules=modules)),
        ):
            result = backend.list_modules()
        self.assertTrue(result[0].custom_module)
        self.assertFalse(result[1].api_supported)
        self.assertEqual(result[1].generated_type, "internal")
        self.assertNotIn("Choice object", result[1].generated_type)

    @override_settings(**VALID)
    def test_sdk_normalizes_194_fields(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        fields = [
            FakeSDKObject(
                api_name=f"Field_{number}",
                field_label=f"Field {number}",
                data_type="text",
                required=False,
                custom_field=True,
                pick_list_values=[],
            )
            for number in range(194)
        ]
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.fields.fields_operations.FieldsOperations.get_fields",
            return_value=FakeSDKResponse(FakeSDKObject(fields=fields)),
        ):
            result = backend.list_fields("Polizas")
        self.assertEqual(len(result), 194)
        self.assertEqual(result[-1].api_name, "Field_193")

    @override_settings(**VALID)
    def test_fields_204_is_safe_empty_result(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.fields.fields_operations.FieldsOperations.get_fields",
            return_value=FakeSDKResponse(status=204),
        ):
            self.assertEqual(backend.list_fields("Internal"), ())

    @override_settings(**VALID)
    def test_record_list_is_bounded_and_normalized(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        raw = FakeSDKObject(
            data=[FakeSDKObject(key_values={"id": "1", "Name": "Test"})],
            info=FakeSDKObject(more_records=False, page=1, count=1),
        )
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.record.record_operations.RecordOperations.get_records",
            return_value=FakeSDKResponse(raw),
        ):
            page = backend.list_records(
                "Polizas", fields=["id", "Name"], limit=20
            )
        self.assertEqual(page.records[0], {"id": "1", "Name": "Test"})
        self.assertEqual(page.count, 1)

    @override_settings(**VALID)
    def test_record_by_id_is_normalized(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        raw = FakeSDKObject(
            data=[FakeSDKObject(key_values={"id": "123", "Name": "Test"})]
        )
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.record.record_operations.RecordOperations.get_record",
            return_value=FakeSDKResponse(raw),
        ):
            record = backend.get_record_by_id(
                "Polizas", "123", fields=["id", "Name"]
            )
        self.assertEqual(record["id"], "123")

    @override_settings(**VALID)
    def test_search_result_is_normalized(self):
        backend = SDKBackend(rest_fallback=Mock(), initializer=lambda _config: None)
        raw = FakeSDKObject(
            data=[FakeSDKObject(key_values={"id": "1"})],
            info=FakeSDKObject(more_records=False, page=1, count=1),
        )
        with patch(
            "zohocrmsdk.src.com.zoho.crm.api.record.record_operations.RecordOperations.search_records",
            return_value=FakeSDKResponse(raw),
        ):
            page = backend.search(
                "Contacts",
                criteria="(Name:equals:Test)",
                fields=["id"],
                limit=10,
            )
        self.assertEqual(page.records, ({"id": "1"},))

    @override_settings(**VALID)
    def test_record_limit_is_enforced_before_sdk_call(self):
        initializer = Mock()
        backend = SDKBackend(rest_fallback=Mock(), initializer=initializer)
        with self.assertRaises(ZohoValidationError):
            backend.list_records("Contacts", fields=["id"], limit=9999)
        initializer.assert_not_called()
