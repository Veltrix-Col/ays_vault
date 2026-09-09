from __future__ import annotations

from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from integrations.zoho.exceptions import ZohoInvalidResponseError

from cotizacion_colectivos.block2.revision_facturacion import (
    reconstruct_revision_facturacion,
)
from cotizacion_colectivos.management.commands.colectivos_validate_revision_facturacion import (
    SAFETY_FLAGS,
)


COMMAND_MODULE = (
    "cotizacion_colectivos.management.commands."
    "colectivos_validate_revision_facturacion"
)
POLICY_ID = "4933790000200703052"
OPERATION_ID = "4933790000300000001"
CONTACT_ID = "4933790000000000001"


POLICY = {
    "id": POLICY_ID,
    "Name": "083005489038",
    "Key": "POL-KEY",
    "L_nea_de_negocio": "Colectivos",
    "Estado_de_la_p_liza": "Vigente",
    "Aseguradora1": {"id": "4933790000000000002", "name": "Aseguradora"},
    "Ramo": "Salud colectivo",
    "Tomador_principal1": {"id": CONTACT_ID, "name": "Tomador lookup"},
    "Modo_de_pago": "Fraccionado",
    "Frecuencia": "Mensual",
    "P_liza_Fecha_de_inicio_vigencia": "2026-08-01",
    "P_liza_Fecha_fin_de_la_vigencia": "2027-08-01",
    "Dia_facturaci_n": 1,
    "Tipo_de_facturaci_n": "Mes corriente",
    "Correo_facturaci_n": "facturacion@example.test",
    "Fecha_2": "2026-09-01",
}
OPERATION = {
    "id": OPERATION_ID,
    "P_liza": {"id": POLICY_ID, "name": "083005489038"},
    "Name": "Cobro 2",
    "Observaciones": "Pendiente",
    "Certificado_Fecha_de_inicio_de_vigencia": "2026-09-01",
    "Fecha_de_expedici_n_de_p_liza": None,
    "N_mero_de_certificado": "0",
    "N_mero_de_certificado_Aseguradora": "21571091",
    "Total_a_pagar_en_OP": 0,
    "Saldo_cartera_aseguradora": None,
}
INSURED = {
    "id": "4933790000400000001",
    "P_liza": {"id": POLICY_ID, "name": "083005489038"},
    "Estado": "Activo",
    "Prima": 100,
    "Pago_total": 349762,
    "Valor_asegurado": 580000000,
}
CONTACT = {"id": CONTACT_ID, "Full_Name": "Tomador Uno"}
NOTE = {
    "id": "4933790000500000001",
    "Parent_Id": {"id": OPERATION_ID, "name": "Cobro 2"},
    "Note_Content": "Nota de operación",
}


def expected_baseline():
    result = reconstruct_revision_facturacion(
        [POLICY], [OPERATION], [INSURED], [CONTACT], [NOTE],
        as_of=date(2026, 9, 7),
    )
    return [dict(result.rows[0].values)]


class _Coql:
    def __init__(self, fail_marker=None):
        self.calls = []
        self.fail_marker = fail_marker

    def execute(self, query, *, offset, limit):
        self.calls.append({"query": query, "offset": offset, "limit": limit})
        if self.fail_marker and self.fail_marker in query:
            raise ZohoInvalidResponseError(
                "Respuesta no interpretable.",
                status_code=502,
                zoho_code="INVALID_DATA",
                backend="rest",
                operation="coql",
                sdk_exception_class="JSONDecodeError",
            )
        routes = {
            " from Polizas ": (POLICY,),
            " from Opeeraciones ": (OPERATION,),
            " from Riesgos1 ": (INSURED,),
            " from Contacts ": (CONTACT,),
            " from Notes ": (NOTE,),
        }
        for marker, records in routes.items():
            if marker in query:
                return SimpleNamespace(records=records, more_records=False)
        raise AssertionError(f"Consulta COQL inesperada: {query}")


class _Zoho:
    profile = "production"

    def __init__(self, environment="production", fail_marker=None):
        self.organization = SimpleNamespace(
            get=lambda: SimpleNamespace(environment=environment)
        )
        self.coql = _Coql(fail_marker=fail_marker)


@override_settings(**{flag: False for flag in SAFETY_FLAGS})
class RevisionFacturacionCommandTests(SimpleTestCase):
    def call(self, **overrides):
        options = {
            "profile": "production",
            "baseline": "unused.xlsx",
            "as_of": "2026-09-07",
            "allow_production_read": True,
            "stdout": StringIO(),
        }
        options.update(overrides)
        return call_command("colectivos_validate_revision_facturacion", **options)

    def test_rejects_non_production_profile_before_any_read(self):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            self.call(profile="sandbox")

    def test_requires_explicit_production_read_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--allow-production-read"):
            self.call(allow_production_read=False)

    def test_every_write_or_publish_guard_must_be_false(self):
        for flag in SAFETY_FLAGS:
            with self.subTest(flag=flag), self.settings(**{flag: True}):
                with self.assertRaisesMessage(CommandError, flag):
                    self.call()

    @patch(f"{COMMAND_MODULE}.read_baseline", side_effect=lambda _path: expected_baseline())
    @patch(f"{COMMAND_MODULE}.get_zoho", return_value=_Zoho(environment="sandbox"))
    def test_rejects_organization_that_does_not_report_production(self, _get_zoho, _read):
        with self.assertRaisesMessage(CommandError, "no confirmo el entorno Production"):
            self.call()

    @patch(f"{COMMAND_MODULE}.read_baseline", side_effect=lambda _path: expected_baseline())
    def test_success_path_uses_only_select_queries_and_prints_summary(self, _read):
        zoho = _Zoho()
        stdout = StringIO()
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=zoho):
            self.call(stdout=stdout)

        self.assertEqual(len(zoho.coql.calls), 5)
        self.assertTrue(
            all(call["query"].casefold().startswith("select ") for call in zoho.coql.calls)
        )
        combined = " ".join(call["query"].casefold() for call in zoho.coql.calls)
        for forbidden in ("create", "update", "delete", "insert", "tasks"):
            self.assertNotIn(forbidden, combined)
        self.assertIn("Matched rows: 1", stdout.getvalue())
        self.assertIn("Different rows: 0", stdout.getvalue())
        self.assertIn("Audit stages:", stdout.getvalue())
        self.assertIn("PLAN_INSTALLMENT_CANDIDATES: 1", stdout.getvalue())
        output = stdout.getvalue()
        self.assertLess(output.index("[READ] Notes:"), output.index("[READ] Contacts:"))
        self.assertIn(
            "[READ] Opeeraciones: batch 1/1, filter=P_liza, values=1",
            output,
        )
        self.assertIn("[READ] Notes: OK, 1 registros", output)

    @patch(f"{COMMAND_MODULE}.read_baseline", side_effect=lambda _path: expected_baseline())
    def test_fetch_error_reports_module_batch_and_sdk_metadata(self, _read):
        stdout = StringIO()
        zoho = _Zoho(fail_marker=" from Notes ")
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=zoho):
            with self.assertRaises(CommandError) as raised:
                self.call(stdout=stdout)

        message = str(raised.exception)
        for fragment in (
            "module=Notes",
            "filter_field=Parent_Id",
            "batch=1",
            "total_batches=1",
            "values=1",
            "category=invalid_response",
            "operation=coql",
            "backend=rest",
            "status_code=502",
            "zoho_code=INVALID_DATA",
            "sdk_exception_class=JSONDecodeError",
        ):
            self.assertIn(fragment, message)
        self.assertNotIn(OPERATION_ID, message)
        self.assertIn("[READ] Notes: batch 1/1", stdout.getvalue())

    @patch(f"{COMMAND_MODULE}.read_baseline")
    def test_differences_fail_unless_explicitly_allowed(self, read_baseline):
        differing = expected_baseline()
        differing[0]["Observaciones"] = "Valor distinto en Analytics"
        read_baseline.return_value = differing
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=_Zoho()):
            with self.assertRaisesMessage(CommandError, "encontro diferencias"):
                self.call()
        with patch(f"{COMMAND_MODULE}.get_zoho", return_value=_Zoho()):
            self.call(allow_differences=True)
