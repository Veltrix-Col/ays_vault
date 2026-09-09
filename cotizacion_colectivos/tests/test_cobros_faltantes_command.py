from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from cotizacion_colectivos.block2.cobros_faltantes import BaselineRow


COMMAND_MODULE = "cotizacion_colectivos.management.commands.colectivos_validate_cobros_faltantes"


class _Coql:
    def __init__(self):
        self.calls = []

    def execute(self, query, *, offset, limit):
        self.calls.append({"query": query, "offset": offset, "limit": limit})
        if " from Polizas " in query:
            return SimpleNamespace(
                records=(
                    {
                        "id": "1000000000000000001",
                        "Name": "POL-1",
                        "Estado_de_la_p_liza": "Vigente",
                        "Modo_de_pago": "Fraccionado",
                        "Cambio_de_intermediario": "No",
                        "Ramo": "Vida grupo",
                        "P_liza_Fecha_de_inicio_vigencia": "2026-01-01",
                        "P_liza_Fecha_fin_de_la_vigencia": "2026-12-31",
                        "Fecha_2": "2026-09-01",
                    },
                ),
                more_records=False,
            )
        return SimpleNamespace(
            records=(
                {
                    "id": "2000000000000000001",
                    "P_liza": {"id": "1000000000000000001", "name": "POL-1"},
                    "Name": "Cobro 2",
                    "Observaciones": "",
                    "Certificado_Fecha_de_inicio_de_vigencia": "2026-09-01",
                },
            ),
            more_records=False,
        )


class _Zoho:
    profile = "production"

    def __init__(self, environment="production"):
        self.organization = SimpleNamespace(
            get=lambda: SimpleNamespace(environment=environment)
        )
        self.coql = _Coql()


@override_settings(ZOHO_PRODUCTION_WRITE_ENABLED=False)
class CobrosFaltantesCommandTests(SimpleTestCase):
    baseline = [BaselineRow("POL-1", effective=1, expected=1)]

    def test_rejects_non_production_profile_before_any_read(self):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command(
                "colectivos_validate_cobros_faltantes",
                profile="sandbox",
                baseline="unused.xlsx",
                as_of="2026-09-07",
                allow_production_read=True,
            )

    def test_requires_explicit_production_read_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--allow-production-read"):
            call_command(
                "colectivos_validate_cobros_faltantes",
                profile="production",
                baseline="unused.xlsx",
                as_of="2026-09-07",
            )

    @override_settings(ZOHO_PRODUCTION_WRITE_ENABLED=True)
    def test_rejects_when_production_write_is_enabled(self):
        with self.assertRaisesMessage(CommandError, "debe permanecer en false"):
            call_command(
                "colectivos_validate_cobros_faltantes",
                profile="production",
                baseline="unused.xlsx",
                as_of="2026-09-07",
                allow_production_read=True,
            )

    @patch(f"{COMMAND_MODULE}.read_baseline", return_value=baseline)
    @patch(f"{COMMAND_MODULE}.get_zoho", return_value=_Zoho(environment="sandbox"))
    def test_rejects_organization_that_does_not_report_production(self, _get_zoho, _read):
        with self.assertRaisesMessage(CommandError, "no confirmo el entorno Production"):
            call_command(
                "colectivos_validate_cobros_faltantes",
                profile="production",
                baseline="unused.xlsx",
                as_of="2026-09-07",
                allow_production_read=True,
            )

    @patch(f"{COMMAND_MODULE}.read_baseline", return_value=baseline)
    def test_success_path_uses_only_select_coql_and_prints_summary(self, _read):
        zoho = _Zoho()
        stdout = StringIO()
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=zoho):
            call_command(
                "colectivos_validate_cobros_faltantes",
                profile="production",
                baseline="unused.xlsx",
                as_of="2026-09-07",
                allow_production_read=True,
                stdout=stdout,
            )
        self.assertEqual(len(zoho.coql.calls), 2)
        self.assertTrue(all(call["query"].casefold().startswith("select ") for call in zoho.coql.calls))
        combined_queries = " ".join(call["query"].casefold() for call in zoho.coql.calls)
        for forbidden in ("create", "update", "delete", "attachment", "publisher"):
            self.assertNotIn(forbidden, combined_queries)
        self.assertIn("Matched: 1", stdout.getvalue())
        self.assertIn("Different: 0", stdout.getvalue())

    @patch(
        f"{COMMAND_MODULE}.read_baseline",
        return_value=[BaselineRow("POL-1", effective=9, expected=9)],
    )
    def test_differences_produce_command_error_unless_explicitly_allowed(self, _read):
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=_Zoho()):
            with self.assertRaisesMessage(CommandError, "encontro diferencias"):
                call_command(
                    "colectivos_validate_cobros_faltantes",
                    profile="production",
                    baseline="unused.xlsx",
                    as_of="2026-09-07",
                    allow_production_read=True,
                    stdout=StringIO(),
                )
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=_Zoho()):
            call_command(
                "colectivos_validate_cobros_faltantes",
                profile="production",
                baseline="unused.xlsx",
                as_of="2026-09-07",
                allow_production_read=True,
                allow_differences=True,
                stdout=StringIO(),
            )
