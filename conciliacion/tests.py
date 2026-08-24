import glob
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from ays_zoho_sdk.exceptions import ZohoAuthenticationError

from .forms import ConciliacionUploadForm
from .ramos_ui import CAMPOS_ARCHIVO, RAMO_CODIGOS, catalogo_slots

# Datos de ejemplo del proyecto Conciliador (fuera del repo). Las pruebas
# end-to-end se ejecutan solo si están disponibles localmente.
_SAMPLE_ROOT = os.environ.get(
    "CONCILIADOR_SAMPLE_ROOT",
    r"C:/Users/user/Desktop/Proyectos/Veltrix/AyS IA/Conciliador",
)


def _xlsx_bytes():
    wb = Workbook()
    wb.active["A1"] = "x"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _archivo(nombre, contenido, tipo):
    return SimpleUploadedFile(nombre, contenido, content_type=tipo)


class CatalogoSlotsTests(TestCase):
    def test_todos_los_ramos_tienen_slots_completos(self):
        catalogo = catalogo_slots()
        self.assertEqual(set(catalogo), set(RAMO_CODIGOS))
        for ramo, slots in catalogo.items():
            campos = {slot["campo"] for slot in slots}
            self.assertEqual(campos, set(CAMPOS_ARCHIVO), ramo)
            for slot in slots:
                for clave in ("label", "help", "accept", "required", "temporal", "nota_temporal"):
                    self.assertIn(clave, slot)


class FormularioTests(TestCase):
    def _datos_validos_salud(self):
        xlsx = _xlsx_bytes()
        return {
            "ramo": "salud",
            "poliza": "12345",
        }, {
            "cobro": _archivo("Porchat.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "recibo": _archivo("Recibo.pdf", b"%PDF-1.7\n%%EOF", "application/pdf"),
        }

    def test_formulario_valido_sin_novedades(self):
        data, files = self._datos_validos_salud()
        form = ConciliacionUploadForm(data=data, files=files)
        self.assertTrue(form.is_valid(), form.errors)

    def test_formulario_valido_sin_recibo(self):
        # El recibo (PDF validado con IA) es opcional: solo genera una
        # advertencia informativa, nunca bloquea el envío del formulario.
        data, files = self._datos_validos_salud()
        del files["recibo"]
        form = ConciliacionUploadForm(data=data, files=files)
        self.assertTrue(form.is_valid(), form.errors)

    def test_poliza_requerida(self):
        data, files = self._datos_validos_salud()
        data["poliza"] = "   "
        form = ConciliacionUploadForm(data=data, files=files)
        self.assertFalse(form.is_valid())
        self.assertIn("poliza", form.errors)

    def test_extension_de_cobro_incoherente_con_el_ramo(self):
        # Salud espera .xlsx para el cobro; un .csv debe rechazarse.
        data, files = self._datos_validos_salud()
        files["cobro"] = _archivo("cobro.csv", b"a;b;c\n", "text/csv")
        form = ConciliacionUploadForm(data=data, files=files)
        self.assertFalse(form.is_valid())
        self.assertIn("cobro", form.errors)

    def test_pdf_invalido_rechazado(self):
        data, files = self._datos_validos_salud()
        files["recibo"] = _archivo("recibo.pdf", b"no es un pdf", "application/pdf")
        form = ConciliacionUploadForm(data=data, files=files)
        self.assertFalse(form.is_valid())
        self.assertIn("recibo", form.errors)


class VistaTests(TestCase):
    def test_get_renderiza_formulario(self):
        response = self.client.get(reverse("conciliacion:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conciliador de Facturación")
        self.assertContains(response, 'name="ramo"')

    def _muestras(self, carpeta, patrones):
        base = os.path.join(_SAMPLE_ROOT, carpeta)
        if not os.path.isdir(base):
            self.skipTest(f"Sin datos de ejemplo en {base}")
        rutas = {}
        for slot, patron in patrones.items():
            hits = glob.glob(os.path.join(base, patron))
            if hits:
                rutas[slot] = hits[0]
        if "cobro" not in rutas:
            self.skipTest(f"Falta muestra 'cobro' en {base}")
        return rutas

    def test_post_end_to_end_salud(self):
        # Relación de asegurados y Personas ya no se suben: se consultan
        # directo en Zoho (Full API), por eso este test requiere conectividad
        # real a Zoho ademas de las muestras locales. Se restringe a
        # ZOHO_ACTIVE_PROFILE=sandbox para nunca disparar una consulta real
        # contra Producción solo por correr la suite de tests localmente.
        if getattr(settings, "ZOHO_ACTIVE_PROFILE", "") != "sandbox":
            self.skipTest("Requiere ZOHO_ACTIVE_PROFILE=sandbox: consulta Zoho por API real.")
        rutas = self._muestras("Salud", {
            "cobro": "*Porchat*.xlsx",
            "novedades": "*Novedades*.xlsx",
            "recibo": "*Recibo*Salud*.pdf",
        })
        tipos = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pdf": "application/pdf", ".csv": "text/csv",
        }
        files = {}
        for slot, ruta in rutas.items():
            with open(ruta, "rb") as fh:
                ext = os.path.splitext(ruta)[1].lower()
                files[slot] = _archivo(os.path.basename(ruta), fh.read(), tipos.get(ext, "application/octet-stream"))
        response = self.client.post(reverse("conciliacion:index"),
                                    {"ramo": "salud", "poliza": "12345", **files})
        self.assertEqual(response.status_code, 200, getattr(response, "content", b"")[:300])
        self.assertIn("X-Conciliacion-Summary", response)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def _write_result(succeeded=True, record_id="7000000000001", code="SUCCESS"):
    return SimpleNamespace(records=(SimpleNamespace(
        succeeded=succeeded, record_id=record_id, code=code,
    ),))


def _cobros_candidatos(cobro_id="7000000000001"):
    return [{
        "id": cobro_id, "nombre": "Operación 1", "ramo": "Salud",
        "numero_cuota": "1", "vigencia_inicio": "2026-01-01", "vigencia_fin": "2026-12-31",
    }]


class PrellenarCobroViewTests(TestCase):
    """El boton "Facturar cobro" prellena Certificado/Fecha expedicion/Pago
    total cuota en el Cobro de Zoho Produccion antes de redirigir -- ver
    `conciliacion.services.processor.prellenar_cobro`. Siempre mockeado: no
    hay credenciales de Zoho Producción disponibles (ni deberían usarse) para
    correr esta suite."""

    def setUp(self):
        self.url = reverse("conciliacion:prellenar_cobro")
        self.payload = {
            "poliza": "12345", "cobro_id": "7000000000001",
            "certificado": "R-42", "fecha_expedicion": "2026-01-10",
            "pago_total_cuota": 150000.5,
        }

    def _post(self, payload):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json",
        )

    def test_deshabilitado_por_defecto_rechaza_sin_llamar_a_zoho(self):
        with patch("conciliacion.services.processor.get_zoho") as get_zoho:
            response = self._post(self.payload)
        self.assertEqual(response.status_code, 409)
        get_zoho.assert_not_called()

    def test_metodo_get_no_permitido(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_json_invalido_se_rechaza(self):
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True):
            response = self.client.post(self.url, data=b"no es json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_escribe_los_tres_campos_cuando_el_cobro_pertenece_a_la_poliza(self):
        update = None
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True), \
             patch("conciliacion.services.processor.resolver_cobros_poliza", return_value=_cobros_candidatos()), \
             patch("conciliacion.services.processor.get_zoho") as get_zoho:
            update = get_zoho.return_value.records.update
            update.return_value = _write_result()
            response = self._post(self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        self.assertCountEqual(
            body["campos"],
            ["N_mero_de_certificado", "Fecha_de_expedici_n_de_p_liza", "Valor_de_cuota"],
        )
        get_zoho.assert_called_once_with(profile="production")
        update.assert_called_once_with(
            module="Opeeraciones",
            records=({
                "id": "7000000000001",
                "N_mero_de_certificado": "R-42",
                "Fecha_de_expedici_n_de_p_liza": "2026-01-10",
                "Valor_de_cuota": 150000.5,
            },),
        )

    def test_prellenado_parcial_omite_campos_sin_valor(self):
        payload = {**self.payload, "fecha_expedicion": None, "pago_total_cuota": None}
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True), \
             patch("conciliacion.services.processor.resolver_cobros_poliza", return_value=_cobros_candidatos()), \
             patch("conciliacion.services.processor.get_zoho") as get_zoho:
            update = get_zoho.return_value.records.update
            update.return_value = _write_result()
            response = self._post(payload)
        self.assertEqual(response.status_code, 200, response.content)
        update.assert_called_once_with(
            module="Opeeraciones",
            records=({"id": "7000000000001", "N_mero_de_certificado": "R-42"},),
        )

    def test_cobro_id_ajeno_a_la_poliza_se_rechaza_sin_escribir(self):
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True), \
             patch("conciliacion.services.processor.resolver_cobros_poliza",
                   return_value=_cobros_candidatos(cobro_id="otro-id")), \
             patch("conciliacion.services.processor.get_zoho") as get_zoho:
            response = self._post(self.payload)
        self.assertEqual(response.status_code, 404)
        get_zoho.return_value.records.update.assert_not_called()

    def test_sin_datos_de_recibo_no_escribe_nada(self):
        payload = {**self.payload, "certificado": None, "fecha_expedicion": None, "pago_total_cuota": None}
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True), \
             patch("conciliacion.services.processor.get_zoho") as get_zoho:
            response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        get_zoho.assert_not_called()

    def test_error_de_autenticacion_zoho_se_traduce_a_502(self):
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True), \
             patch("conciliacion.services.processor.get_zoho",
                   side_effect=ZohoAuthenticationError("sin credenciales")):
            response = self._post(self.payload)
        self.assertEqual(response.status_code, 502)

    def test_fecha_con_formato_invalido_se_descarta_pero_los_demas_se_escriben(self):
        payload = {**self.payload, "fecha_expedicion": "10/01/2026"}
        with self.settings(CONCILIACION_COBRO_PREFILL_ENABLED=True), \
             patch("conciliacion.services.processor.resolver_cobros_poliza", return_value=_cobros_candidatos()), \
             patch("conciliacion.services.processor.get_zoho") as get_zoho:
            update = get_zoho.return_value.records.update
            update.return_value = _write_result()
            response = self._post(payload)
        self.assertEqual(response.status_code, 200, response.content)
        update.assert_called_once_with(
            module="Opeeraciones",
            records=({
                "id": "7000000000001",
                "N_mero_de_certificado": "R-42",
                "Valor_de_cuota": 150000.5,
            },),
        )
