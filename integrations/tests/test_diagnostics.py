from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from integrations.tests.helpers import (
    FakeOAuth,
    VALID_SETTINGS,
    client_factory,
)
from integrations.zoho.client import ZohoClient
from integrations.zoho.diagnostics import (
    ErrorSnapshot,
    ModuleDiagnostic,
    ModuleDiagnosticCategory,
    ModuleDiagnosticsService,
    classify_diagnostic,
    classify_failure,
)
from integrations.zoho.exceptions import (
    ZohoAPIError,
    ZohoAuthorizationError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoSDKError,
)
from integrations.zoho.schemas import FieldMetadata, ModuleMetadata, Organization
from integrations.zoho.sdk.response_parser import response_object
from integrations.zoho.sdk.response_parser import normalize_sdk_exception
from integrations.zoho.settings import ZohoSettings


def module(**overrides) -> ModuleMetadata:
    values = {
        "api_name": "Accounts",
        "module_name": "Accounts",
        "plural_label": "Cuentas",
        "singular_label": "Cuenta",
        "custom_module": False,
        "generated_type": "",
        "api_supported": True,
        "status": "visible",
    }
    values.update(overrides)
    return ModuleMetadata(**values)


def error(
    *,
    status: int | None = 400,
    code: str = "",
    message: str = "Zoho rechazó la consulta.",
    backend: str = "rest",
) -> ErrorSnapshot:
    return ErrorSnapshot(
        backend=backend,
        http_status=status,
        zoho_code=code,
        message=message,
        zoho_status="ERROR",
        detail_keys=("api_name",),
        request_id="req-1",
        exception_class="APIException" if backend == "sdk" else "",
        sdk_code="",
        request_sent=True,
    )


@override_settings(**VALID_SETTINGS)
class SafeRestErrorTests(SimpleTestCase):
    def build_client(self, payload, status=400):
        return ZohoClient(
            oauth=FakeOAuth(),
            config=ZohoSettings.from_django(),
            client_factory=client_factory(
                lambda _request: httpx.Response(
                    status,
                    json=payload,
                    headers={"X-ZOHO-REQUEST-ID": "request-123"},
                )
            ),
            sleeper=lambda _delay: None,
        )

    def test_rest_error_preserves_only_safe_technical_metadata(self):
        payload = {
            "code": "INVALID_MODULE",
            "message": "Accounts for person@example.com secret-token",
            "status": "error",
            "details": {
                "api_name": "Accounts",
                "email": "person@example.com",
                "token": "secret-token",
            },
        }
        with self.assertRaises(ZohoAPIError) as caught:
            self.build_client(payload).get(
                "/crm/v8/settings/fields",
                params={"module": "Accounts"},
                logical_endpoint="fields",
                module="Accounts",
            )
        exc = caught.exception
        self.assertEqual(exc.zoho_code, "INVALID_MODULE")
        self.assertEqual(exc.sdk_code, "")
        self.assertEqual(exc.zoho_status, "ERROR")
        self.assertEqual(exc.detail_keys, ("api_name", "email", "token"))
        self.assertEqual(exc.operation, "fields")
        self.assertEqual(exc.module, "Accounts")
        rendered = str(exc)
        self.assertNotIn("person@example.com", rendered)
        self.assertNotIn("secret-token", rendered)

    def test_status_classes_keep_safe_code(self):
        cases = (
            (403, "NO_PERMISSION", ZohoAuthorizationError),
            (404, "NOT_FOUND", ZohoNotFoundError),
            (429, "TOO_MANY_REQUESTS", ZohoRateLimitError),
            (500, "INTERNAL_ERROR", ZohoAPIError),
        )
        for status, code, expected in cases:
            with self.subTest(status=status):
                with self.assertRaises(expected) as caught:
                    self.build_client({"code": code}, status=status).get(
                        "/crm/v8/settings/fields",
                        logical_endpoint="fields",
                        module="Accounts",
                    )
                self.assertEqual(caught.exception.zoho_code, code)


class SafeSDKErrorTests(SimpleTestCase):
    def test_api_exception_is_safe_and_structured(self):
        remote = SimpleNamespace(
            get_code=lambda: "NOT_SUPPORTED",
            get_message=lambda: "private person@example.com token-secret",
            get_status=lambda: "error",
            get_details=lambda: {
                "module": "Accounts",
                "email": "person@example.com",
            },
        )
        response = SimpleNamespace(
            get_status_code=lambda: 400,
            get_object=lambda: remote,
        )
        with self.assertRaises(ZohoSDKError) as caught:
            response_object(response, operation="fields", module="Accounts")
        exc = caught.exception
        self.assertEqual(exc.zoho_code, "NOT_SUPPORTED")
        self.assertEqual(exc.detail_keys, ("email", "module"))
        self.assertEqual(exc.sdk_exception_class, "SimpleNamespace")
        self.assertTrue(exc.request_sent)
        self.assertNotIn("person@example.com", str(exc))
        self.assertNotIn("token-secret", str(exc))

    def test_sdk_exception_before_response_is_redacted(self):
        class SDKException(Exception):
            def get_code(self):
                return "INVALID_MODULE"

            def get_message(self):
                return "person@example.com refresh-token-secret"

            def get_details(self):
                return {"email": "person@example.com", "token": "secret"}

        exc = normalize_sdk_exception(
            SDKException("raw private text"),
            operation="fields",
            module="Accounts",
            request_sent=None,
        )
        self.assertEqual(exc.sdk_code, "INVALID_MODULE")
        self.assertEqual(exc.zoho_code, "")
        self.assertEqual(exc.sdk_exception_class, "SDKException")
        self.assertIsNone(exc.request_sent)
        self.assertNotIn("person@example.com", str(exc))
        self.assertNotIn("refresh-token-secret", str(exc))


class ClassificationTests(SimpleTestCase):
    def classify(self, snapshot, metadata=None):
        return classify_diagnostic(
            metadata or module(),
            sdk_fields=None,
            rest_fields=None,
            sdk_error=None,
            rest_error=snapshot,
        )

    def test_known_400_codes(self):
        self.assertEqual(
            self.classify(error(code="INVALID_MODULE")),
            ModuleDiagnosticCategory.UNSUPPORTED_MODULE,
        )
        self.assertEqual(
            self.classify(error(code="NOT_SUPPORTED")),
            ModuleDiagnosticCategory.FIELDS_NOT_AVAILABLE,
        )

    def test_metadata_categories(self):
        self.assertEqual(
            self.classify(error(), module(status="disabled")),
            ModuleDiagnosticCategory.DISABLED_MODULE,
        )
        self.assertEqual(
            self.classify(error(), module(generated_type="internal")),
            ModuleDiagnosticCategory.INTERNAL_MODULE,
        )
        self.assertEqual(
            self.classify(error(), module(generated_type="virtual")),
            ModuleDiagnosticCategory.VIRTUAL_MODULE,
        )

    def test_authorization_scope_not_found_and_temporary(self):
        cases = (
            (error(status=403, code="NO_PERMISSION"), ModuleDiagnosticCategory.PERMISSION_DENIED),
            (error(status=403, code="OAUTH_SCOPE_MISMATCH"), ModuleDiagnosticCategory.SCOPE_INSUFFICIENT),
            (error(status=404, code="NOT_FOUND"), ModuleDiagnosticCategory.NOT_FOUND),
            (error(status=429), ModuleDiagnosticCategory.TEMPORARY_ERROR),
            (error(status=500), ModuleDiagnosticCategory.TEMPORARY_ERROR),
        )
        for snapshot, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.classify(snapshot), expected)

    def test_invalid_api_name_is_distinct(self):
        self.assertEqual(
            self.classify(error(code="INVALID_API_NAME")),
            ModuleDiagnosticCategory.INVALID_API_NAME,
        )

    def test_explicit_permission_error_overrides_api_supported_flag(self):
        self.assertEqual(
            self.classify(
                error(status=403, code="NO_PERMISSION"),
                module(api_supported=False),
            ),
            ModuleDiagnosticCategory.PERMISSION_DENIED,
        )

    def test_empty_unknown_and_sdk_fallback_success(self):
        self.assertEqual(
            classify_diagnostic(
                module(),
                sdk_fields=0,
                rest_fields=0,
                sdk_error=None,
                rest_error=None,
            ),
            ModuleDiagnosticCategory.FIELDS_NOT_AVAILABLE,
        )
        self.assertEqual(
            self.classify(error(code="UNRECOGNIZED")),
            ModuleDiagnosticCategory.UNKNOWN,
        )
        self.assertEqual(
            classify_diagnostic(
                module(),
                sdk_fields=None,
                rest_fields=4,
                sdk_error=error(backend="sdk"),
                rest_error=None,
            ),
            ModuleDiagnosticCategory.SDK_INCOMPATIBILITY,
        )

    def test_exception_classifier_uses_safe_code(self):
        exc = ZohoAuthorizationError(
            "El alcance OAuth no autoriza esta consulta.",
            status_code=403,
            zoho_code="OAUTH_SCOPE_MISMATCH",
            backend="rest",
        )
        self.assertEqual(
            classify_failure(module(), exc),
            ModuleDiagnosticCategory.SCOPE_INSUFFICIENT,
        )


class DiagnosticServiceTests(SimpleTestCase):
    def test_sdk_success_does_not_call_rest(self):
        sdk = Mock()
        rest = Mock()
        sdk.list_fields_sdk_only.return_value = (
            FieldMetadata("Name", "Nombre", "text"),
        )
        result = ModuleDiagnosticsService(sdk=sdk, rest=rest).diagnose(module())
        self.assertEqual(result.classification, ModuleDiagnosticCategory.SUPPORTED)
        rest.list_fields.assert_not_called()

    def test_sdk_error_and_rest_error_are_both_preserved(self):
        sdk = Mock()
        rest = Mock()
        sdk.list_fields_sdk_only.side_effect = ZohoSDKError(
            "Operación no admitida.",
            status_code=400,
            zoho_code="NOT_SUPPORTED",
            backend="sdk",
            sdk_exception_class="SDKException",
        )
        rest.list_fields.side_effect = ZohoAPIError(
            "Módulo no admitido.",
            status_code=400,
            zoho_code="INVALID_MODULE",
            backend="rest",
        )
        result = ModuleDiagnosticsService(sdk=sdk, rest=rest).diagnose(module())
        self.assertEqual(
            result.classification, ModuleDiagnosticCategory.UNSUPPORTED_MODULE
        )
        self.assertIsNotNone(result.sdk_error)
        self.assertIsNotNone(result.rest_error)


class DiagnosticCommandTests(SimpleTestCase):
    def diagnostic(self, item):
        return ModuleDiagnostic(
            module=item,
            sdk_fields=None,
            rest_fields=None,
            sdk_error=error(backend="sdk"),
            rest_error=error(code="INVALID_MODULE"),
            classification=ModuleDiagnosticCategory.UNSUPPORTED_MODULE,
            recommendation="omitir",
        )

    def test_single_and_multiple_modules(self):
        modules = (module(), module(api_name="Deals", module_name="Deals"))
        facade = SimpleNamespace(
            profile="production",
            environment="production",
            backend_name="sdk",
            config=SimpleNamespace(sdk_resource_path="runtime/zoho_sdk/production"),
            organization=SimpleNamespace(
                get=lambda: Organization("1", "AYS Seguros", environment="production")
            ),
            metadata=SimpleNamespace(list_modules=lambda: modules)
        )
        service = Mock()
        service.diagnose.side_effect = self.diagnostic
        with patch(
            "integrations.management.commands.zoho_diagnose_modules.get_zoho",
            return_value=facade,
        ), patch(
            "integrations.management.commands.zoho_diagnose_modules."
            "ModuleDiagnosticsService.build",
            return_value=service,
        ):
            single = StringIO()
            call_command("zoho_diagnose_modules", module=["Accounts"], stdout=single)
            self.assertIn("Módulo: Accounts", single.getvalue())
            self.assertEqual(service.diagnose.call_count, 1)
            service.reset_mock()
            multiple = StringIO()
            call_command(
                "zoho_diagnose_modules",
                module=["Accounts", "Deals"],
                stdout=multiple,
            )
            self.assertEqual(service.diagnose.call_count, 2)

    def test_json_contains_only_allowlisted_diagnostic_fields(self):
        item = module()
        facade = SimpleNamespace(
            profile="production",
            environment="production",
            backend_name="sdk",
            config=SimpleNamespace(sdk_resource_path="runtime/zoho_sdk/production"),
            organization=SimpleNamespace(
                get=lambda: Organization("1", "AYS Seguros", environment="production")
            ),
            metadata=SimpleNamespace(list_modules=lambda: (item,))
        )
        service = Mock()
        service.diagnose.return_value = self.diagnostic(item)
        with TemporaryDirectory() as directory, patch(
            "integrations.management.commands.zoho_diagnose_modules.get_zoho",
            return_value=facade,
        ), patch(
            "integrations.management.commands.zoho_diagnose_modules."
            "ModuleDiagnosticsService.build",
            return_value=service,
        ):
            target = Path(directory) / "module_diagnostics.json"
            call_command(
                "zoho_diagnose_modules",
                module=["Accounts"],
                output=str(target),
                stdout=StringIO(),
            )
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(
            set(payload[0]),
            {
                "api_name",
                "etiqueta",
                "estado",
                "backend_intentado",
                "http_status",
                "codigo_zoho",
                "clasificacion",
                "recomendacion",
                "timestamp",
            },
        )
        rendered = json.dumps(payload)
        self.assertNotIn("token", rendered.lower())
        self.assertNotIn("person@", rendered.lower())
