from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from integrations.zoho.diagnostics import ModuleDiagnostic, ModuleDiagnosticCategory
from integrations.zoho.schemas import ModuleMetadata, Organization
from ays_zoho_sdk.diagnostics import ErrorSnapshot


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
