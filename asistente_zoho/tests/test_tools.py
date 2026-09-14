from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from asistente_zoho import tools
from cotizacion_colectivos.services.common import ColectivosServiceError
from integrations.zoho.exceptions import ZohoTimeoutError
from integrations.zoho.schemas import Page


def _fake_zoho(*, by_criteria=None, by_field=None, coql=None, org_id="4933790000000020005"):
    zoho = Mock()
    zoho.config.expected_org_id = org_id
    zoho.search.by_criteria.side_effect = by_criteria
    if by_field is not None:
        zoho.search.by_field.return_value = by_field
    if coql is not None:
        zoho.coql.execute.side_effect = coql
    return zoho


class ExtractHelpersTests(SimpleTestCase):
    def test_extrae_documento_y_nombre_de_frase_natural(self):
        self.assertEqual(tools._extract_document("Juan Pérez cc 1037672230"), "1037672230")
        self.assertEqual(tools._extract_name("Juan Pérez cc 1037672230", "1037672230"), "Juan Pérez")

    def test_sin_documento_deja_el_nombre_completo(self):
        self.assertEqual(tools._extract_document("Acme S.A.S."), "")
        # el punto final se recorta al limpiar residuos de puntuación; sigue
        # siendo un prefijo válido para un starts_with contra "Acme S.A.S."
        self.assertEqual(tools._extract_name("Acme S.A.S.", ""), "Acme S.A.S")


class BuscarClienteTests(SimpleTestCase):
    def test_query_vacia_es_rechazada(self):
        with self.assertRaises(ColectivosServiceError):
            tools.buscar_cliente("   ")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_busca_por_documento_y_nombre_y_deduplica(self, colectivos_zoho):
        registro = {
            "id": "111", "Full_Name": "Juan Pérez", "Tipo_de_persona": "Persona natural",
            "Tipo_ID": "CC", "N_mero_de_ID": "1037672230", "Estado": "Cliente",
        }
        # documento + 3 campos de nombre van en un único criterio "or" -- una
        # sola llamada a Zoho, no una por campo.
        zoho = _fake_zoho(by_criteria=[Page(records=(registro,))])
        colectivos_zoho.return_value = zoho

        resultado = tools.buscar_cliente("Juan Pérez cc 1037672230")

        zoho.search.by_criteria.assert_called_once()
        criterio = zoho.search.by_criteria.call_args.kwargs["criteria"]
        self.assertIn("N_mero_de_ID", criterio)
        self.assertIn("Full_Name", criterio)
        self.assertIn("Raz_n_social", criterio)
        self.assertIn("Nombre_comercial", criterio)
        self.assertEqual(resultado["total"], 1)
        cliente = resultado["clientes"][0]
        self.assertEqual(cliente["nombre"], "Juan Pérez")
        self.assertNotIn("1037672230", str(resultado))
        self.assertEqual(
            cliente["link"], "https://crm.zoho.com/crm/org4933790000000020005/tab/Contacts/111",
        )

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_solo_documento_sin_nombre_usable_igual_busca(self, colectivos_zoho):
        zoho = _fake_zoho(by_criteria=[Page(records=())])
        colectivos_zoho.return_value = zoho
        resultado = tools.buscar_cliente("1037672230")
        self.assertEqual(resultado["total"], 0)
        zoho.search.by_criteria.assert_called_once()
        self.assertIn("N_mero_de_ID", zoho.search.by_criteria.call_args.kwargs["criteria"])

    def test_texto_puramente_numerico_muy_corto_no_es_documento_valido(self):
        # No hay heurística infalible para distinguir "un saludo" de "un nombre
        # corto real" -- lo que sí puede validar el tool es que un documento
        # detectado tenga longitud razonable (ver _DOCUMENT_PATTERN, 5+ dígitos).
        self.assertEqual(tools._extract_document("cita a las 3"), "")

    def test_sin_ningun_criterio_construible_es_rechazado(self):
        with self.assertRaises(ColectivosServiceError):
            tools.buscar_cliente("hi")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_error_zoho_se_traduce(self, colectivos_zoho):
        zoho = Mock()
        zoho.search.by_criteria.side_effect = ZohoTimeoutError("boom")
        colectivos_zoho.return_value = zoho
        with self.assertRaises(ColectivosServiceError):
            tools.buscar_cliente("Acme S.A.S.")


class DetalleClienteTests(SimpleTestCase):
    def test_record_id_invalido_es_rechazado(self):
        with self.assertRaises(ColectivosServiceError):
            tools.detalle_cliente("no-es-un-id")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_incluye_polizas_relacionadas_con_link(self, colectivos_zoho):
        # El contacto se resuelve por search.by_criteria(id:equals:...), nunca
        # por records.get_by_id (poco confiable en el backend SDK de Zoho --
        # mismo hallazgo que ya documenta EntityDetailService). Las pólizas
        # relacionadas se traen en una sola consulta COQL, no una por id.
        contacto = Page(records=({
            "id": "111", "Full_Name": "Juan Pérez", "Tipo_ID": "CC",
            "N_mero_de_ID": "1037672230", "Estado": "Cliente", "Email": "juan@example.com",
        },))
        relacion = Page(records=({"id": "999", "P_liza": {"id": "222"}},))
        vacio = Page(records=())
        poliza = Page(records=({
            "id": "222", "Name": "POL-0913", "Ramo": "Vida",
            "Aseguradora1": "Aseguradora X", "Estado_de_la_p_liza": "Vigente",
        },))
        zoho = _fake_zoho(by_criteria=[contacto, relacion, vacio, vacio], coql=[poliza])
        colectivos_zoho.return_value = zoho

        resultado = tools.detalle_cliente("111")

        zoho.coql.execute.assert_called_once()
        self.assertTrue(resultado["encontrado"])
        self.assertEqual(resultado["nombre"], "Juan Pérez")
        self.assertEqual(len(resultado["polizas"]), 1)
        self.assertEqual(
            resultado["polizas"][0]["link"],
            "https://crm.zoho.com/crm/org4933790000000020005/tab/Polizas/222",
        )

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_un_rol_fallido_no_descarta_las_polizas_de_los_otros_roles(self, colectivos_zoho):
        contacto = Page(records=({"id": "111", "Full_Name": "Juan Pérez", "Estado": "Cliente"},))
        relacion_ok = Page(records=({"id": "999", "P_liza": {"id": "222"}},))
        poliza = Page(records=({"id": "222", "Name": "POL-0913", "Ramo": "Vida", "Estado_de_la_p_liza": "Vigente"},))
        zoho = _fake_zoho(
            by_criteria=[contacto, relacion_ok, ZohoTimeoutError("boom"), ZohoTimeoutError("boom")],
            coql=[poliza],
        )
        colectivos_zoho.return_value = zoho

        resultado = tools.detalle_cliente("111")

        self.assertEqual(len(resultado["polizas"]), 1)

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_no_encontrado(self, colectivos_zoho):
        zoho = _fake_zoho(by_criteria=[Page(records=())])
        colectivos_zoho.return_value = zoho
        resultado = tools.detalle_cliente("111")
        self.assertFalse(resultado["encontrado"])


class ObtenerPolizaTests(SimpleTestCase):
    def _fake_zoho_field(self, records):
        zoho = Mock()
        zoho.config.expected_org_id = "4933790000000020005"
        zoho.search.by_field.return_value = Page(records=tuple(records))
        return zoho

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_encuentra_poliza_unica(self, colectivos_zoho):
        colectivos_zoho.return_value = self._fake_zoho_field([
            {"id": "222", "Name": "POL-0913", "Estado_de_la_p_liza": "Vigente",
             "Ramo": "Vida", "Aseguradora1": "Aseguradora X", "Tomador_principal1": "Acme S.A.S."},
        ])
        resultado = tools.obtener_poliza("0913")
        self.assertTrue(resultado["encontrada"])
        self.assertEqual(resultado["tomador"], "Acme S.A.S.")
        self.assertEqual(
            resultado["link"], "https://crm.zoho.com/crm/org4933790000000020005/tab/Polizas/222",
        )

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_no_encontrada(self, colectivos_zoho):
        colectivos_zoho.return_value = self._fake_zoho_field([])
        resultado = tools.obtener_poliza("no-existe")
        self.assertFalse(resultado["encontrada"])

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_ambigua_no_revela_ningun_registro(self, colectivos_zoho):
        colectivos_zoho.return_value = self._fake_zoho_field([{"id": "1111111111"}, {"id": "2222222222"}])
        resultado = tools.obtener_poliza("0913")
        self.assertFalse(resultado["encontrada"])
        self.assertEqual(resultado["motivo"], "ambiguo")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_error_zoho_se_traduce_sin_propagar_detalle_interno(self, colectivos_zoho):
        zoho = Mock()
        zoho.search.by_field.side_effect = ZohoTimeoutError("boom")
        colectivos_zoho.return_value = zoho
        with self.assertRaises(ColectivosServiceError):
            tools.obtener_poliza("0913")


class TareasTests(SimpleTestCase):
    def _fake_zoho(self, records):
        zoho = Mock()
        zoho.config.expected_org_id = "4933790000000020005"
        zoho.search.by_field.return_value = Page(records=tuple(records))
        return zoho

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_mis_tareas_filtra_por_correo_responsable(self, colectivos_zoho):
        zoho = self._fake_zoho([
            {"id": "333", "Subject": "Revisar novedad", "Estado": "Abierta", "Responsable": "Sara Rua"},
        ])
        colectivos_zoho.return_value = zoho
        resultado = tools.mis_tareas("sara.rua@segurosays.com")
        self.assertEqual(resultado["total"], 1)
        self.assertEqual(zoho.search.by_field.call_args.kwargs["field"], "Correo_responsable")
        self.assertEqual(zoho.search.by_field.call_args.kwargs["value"], "sara.rua@segurosays.com")

    @patch("asistente_zoho.tools.colectivos_zoho")
    def test_tareas_por_responsable_busca_por_nombre(self, colectivos_zoho):
        zoho = self._fake_zoho([])
        colectivos_zoho.return_value = zoho
        tools.tareas_por_responsable("Sara Rua")
        self.assertEqual(zoho.search.by_field.call_args.kwargs["field"], "Responsable")
        self.assertEqual(zoho.search.by_field.call_args.kwargs["value"], "Sara Rua")
