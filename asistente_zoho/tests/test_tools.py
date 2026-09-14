from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from asistente_zoho import tools
from cotizacion_colectivos.dto import (
    ClientSearchResult,
    CompanyDetail,
    ContactSummary,
    PolicyDetail,
    RelatedPolicy,
)
from cotizacion_colectivos.services.common import ColectivosServiceError
from integrations.zoho.exceptions import ZohoTimeoutError
from integrations.zoho.schemas import Page


class BuscarClienteTests(SimpleTestCase):
    @patch("asistente_zoho.tools.UnifiedClientSearchService")
    def test_mapea_resultados_con_link_firmado(self, servicio_cls):
        servicio_cls.return_value.search.return_value = (
            ClientSearchResult(
                detail_token="token-abc", source_kind="company",
                display_name="Acme S.A.S.", entity_label="Empresa",
                document_label="NIT", masked_document="•••••1234",
                state="Activo", document="900123451234",
            ),
        )
        resultado = tools.buscar_cliente("acme")
        self.assertEqual(resultado["total"], 1)
        cliente = resultado["clientes"][0]
        self.assertEqual(cliente["nombre"], "Acme S.A.S.")
        self.assertEqual(cliente["documento"], "NIT •••••1234")
        self.assertNotIn("900123451234", str(resultado))
        self.assertEqual(cliente["link"], "/cotizacion-colectivos/clientes/company/token-abc/")

    def test_query_vacia_es_rechazada(self):
        with self.assertRaises(ColectivosServiceError):
            tools.buscar_cliente("  ")


class DetalleClienteTests(SimpleTestCase):
    @patch("asistente_zoho.tools.EntityDetailService")
    def test_empresa_incluye_polizas_con_link(self, servicio_cls):
        servicio_cls.return_value.company.return_value = CompanyDetail(
            display_name="Acme S.A.S.", legal_name="Acme S.A.S.",
            id_type="NIT", masked_document="•••••1234", state="Activo",
            summary=ContactSummary(person_type="Persona jurídica", id_type="NIT",
                                    masked_document="•••••1234", state="Activo"),
            policies=(
                RelatedPolicy(detail_token="poliza-1", masked_reference="Ref. 5678",
                               state="Vigente", branch="Vida grupo", insurer="Aseguradora X"),
            ),
            direct_policies=(), insured=(), risks=(),
        )
        resultado = tools.detalle_cliente("company", "token-abc")
        self.assertEqual(resultado["nombre"], "Acme S.A.S.")
        self.assertEqual(len(resultado["polizas"]), 1)
        self.assertEqual(resultado["polizas"][0]["link"], "/cotizacion-colectivos/polizas/poliza-1/")

    def test_entity_kind_invalido_rechazado(self):
        with patch("asistente_zoho.tools.EntityDetailService"):
            with self.assertRaises(ColectivosServiceError):
                tools.detalle_cliente("otro", "token-abc")


class ObtenerPolizaTests(SimpleTestCase):
    def _fake_zoho(self, records):
        zoho = Mock()
        zoho.search.by_field.return_value = Page(records=tuple(records))
        return zoho

    @patch("asistente_zoho.tools.PolicyService")
    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_encuentra_poliza_unica(self, colectivos_zoho, policy_service_cls):
        colectivos_zoho.return_value = self._fake_zoho([{"id": "1234567890"}])
        policy_service_cls.return_value.detail.return_value = PolicyDetail(
            detail_token="poliza-1", masked_reference="Ref. 5678", branch_code="VIDA",
            branch_name="Vida grupo", classification="confirmed", insurer="Aseguradora X",
            state="Vigente", holder="Acme S.A.S.", start_date="2026-01-01", end_date="2026-12-31",
            renewable="Si", payment_mode="Mensual", frequency="Mensual", installments="12",
            first_installment_date="2026-01-15", payment_calendar=(), insured=(), risks=(),
            active_count=10, excluded_count=1,
        )
        resultado = tools.obtener_poliza("5678")
        self.assertTrue(resultado["encontrada"])
        self.assertEqual(resultado["asegurados_activos"], 10)
        self.assertEqual(resultado["link"], "/cotizacion-colectivos/polizas/poliza-1/")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_no_encontrada(self, colectivos_zoho):
        colectivos_zoho.return_value = self._fake_zoho([])
        resultado = tools.obtener_poliza("no-existe")
        self.assertFalse(resultado["encontrada"])

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_ambigua_no_revela_ningun_registro(self, colectivos_zoho):
        colectivos_zoho.return_value = self._fake_zoho([{"id": "1111111111"}, {"id": "2222222222"}])
        resultado = tools.obtener_poliza("5678")
        self.assertFalse(resultado["encontrada"])
        self.assertEqual(resultado["motivo"], "ambiguo")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_error_zoho_se_traduce_sin_propagar_detalle_interno(self, colectivos_zoho):
        zoho = Mock()
        zoho.search.by_field.side_effect = ZohoTimeoutError("boom")
        colectivos_zoho.return_value = zoho
        with self.assertRaises(ColectivosServiceError):
            tools.obtener_poliza("5678")


class TareasTests(SimpleTestCase):
    def _fake_zoho(self, records):
        zoho = Mock()
        zoho.search.by_field.return_value = Page(records=tuple(records))
        return zoho

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_mis_tareas_filtra_por_correo_responsable(self, colectivos_zoho):
        zoho = self._fake_zoho([
            {"Subject": "Revisar novedad", "Estado": "Abierta", "Responsable": "Sara Rua"},
        ])
        colectivos_zoho.return_value = zoho
        resultado = tools.mis_tareas("sara.rua@segurosays.com")
        self.assertEqual(resultado["total"], 1)
        zoho.search.by_field.assert_called_once()
        self.assertEqual(zoho.search.by_field.call_args.kwargs["field"], "Correo_responsable")
        self.assertEqual(zoho.search.by_field.call_args.kwargs["value"], "sara.rua@segurosays.com")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_tareas_por_responsable_busca_por_nombre(self, colectivos_zoho):
        zoho = self._fake_zoho([])
        colectivos_zoho.return_value = zoho
        tools.tareas_por_responsable("Sara Rua")
        self.assertEqual(zoho.search.by_field.call_args.kwargs["field"], "Responsable")
        self.assertEqual(zoho.search.by_field.call_args.kwargs["value"], "Sara Rua")
