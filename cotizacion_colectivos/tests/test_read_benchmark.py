from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings


class _Search:
    def by_criteria(self, *, module, **kwargs):
        if module == "Polizas":
            return SimpleNamespace(records=[{
                "id": "4234567890123456789",
                "Name": "valor-no-renderizado",
                "Ramo": "Salud colectivo",
            }], more_records=False)
        return SimpleNamespace(records=[], more_records=False)


class _Records:
    def get_by_id(self, **kwargs):
        return {"id": "4234567890123456789", "Name": "interno", "Ramo": "Salud colectivo"}


class _Facade:
    def __init__(self):
        self.organization = SimpleNamespace(get=lambda: SimpleNamespace(environment="production"))
        self.metadata = SimpleNamespace(list_fields=lambda module: ())
        self.search = _Search()
        self.records = _Records()
        self.coql = SimpleNamespace(execute=lambda query: SimpleNamespace(records=[]))


class _WorkspaceService:
    profile = "production"
    backend = "sdk"
    preparation_status = "hit"
    timings = {
        "remote_queries": 4, "records_queries": 1, "search_queries": 1,
        "coql_queries": 2, "policy_lookup_ms": 10, "risks1_query_ms": 20,
    }

    def __init__(self, *args, **kwargs):
        self.timings = dict(type(self).timings)
        if not kwargs.get("zoho"):
            self.timings["remote_queries"] = 0

    def group(self, token, **kwargs):
        return SimpleNamespace(branch_code="91"), (SimpleNamespace(),)


@override_settings(ZOHO_ACTIVE_PROFILE="production")
class ReadBenchmarkTests(SimpleTestCase):
    def test_confirmation_is_mandatory(self):
        with self.assertRaises(CommandError):
            call_command("colectivos_benchmark_read", profile="production")

    @patch(
        "cotizacion_colectivos.management.commands.colectivos_benchmark_read.get_zoho",
        side_effect=lambda **kwargs: _Facade(),
    )
    def test_three_runs_per_backend_are_safe_and_do_not_print_identifiers(self, get_zoho_mock):
        output = StringIO()
        call_command(
            "colectivos_benchmark_read",
            profile="production",
            policy="091000811814",
            allow_production_read=True,
            stdout=output,
        )
        text = output.getvalue()
        self.assertEqual(get_zoho_mock.call_count, 6)
        self.assertIn("SDK frío", text)
        self.assertIn("REST frío", text)
        self.assertNotIn("4234567890123456789", text)
        self.assertNotIn("valor-no-renderizado", text)

    def test_workspace_benchmark_requires_explicit_confirmation(self):
        with self.assertRaises(CommandError):
            call_command("colectivos_benchmark_workspace", profile="production")

    @patch(
        "cotizacion_colectivos.management.commands.colectivos_benchmark_workspace.build_current_policy_workbook",
        return_value=b"xlsx",
    )
    @patch(
        "cotizacion_colectivos.management.commands.colectivos_benchmark_workspace.invalidate_policy_preparation",
    )
    @patch(
        "cotizacion_colectivos.management.commands.colectivos_benchmark_workspace.PolicyService",
        side_effect=lambda *args, **kwargs: _WorkspaceService(*args, **kwargs),
    )
    @patch(
        "cotizacion_colectivos.management.commands.colectivos_benchmark_workspace.get_colectivos_zoho",
        return_value=_Facade(),
    )
    def test_workspace_benchmark_reports_only_safe_aggregate_metrics(
        self, _get_zoho, _service, _invalidate, _workbook,
    ):
        output = StringIO()
        call_command(
            "colectivos_benchmark_workspace",
            profile="production",
            policy="091000811814",
            allow_production_read=True,
            stdout=output,
        )
        text = output.getvalue()
        self.assertIn("hidratación_remota=", text)
        self.assertIn("consultas_zoho_despues_de_hidratar=0", text)
        self.assertNotIn("4234567890123456789", text)
        self.assertNotIn("valor-no-renderizado", text)
