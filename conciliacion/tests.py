import glob
import io
import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

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
            "fuente": "excel",
        }, {
            "cobro": _archivo("Porchat.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "recibo": _archivo("Recibo.pdf", b"%PDF-1.7\n%%EOF", "application/pdf"),
            "relacion": _archivo("Zoho_Salud.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "personas": _archivo("Personas_Zoho.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        }

    def test_formulario_valido_sin_novedades(self):
        data, files = self._datos_validos_salud()
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
            if hits and slot in ("relacion", "cobro", "personas"):
                rutas[slot] = hits[0]
            elif hits:
                rutas[slot] = hits[0]
        for obligatorio in ("relacion", "cobro", "personas"):
            if obligatorio not in rutas:
                self.skipTest(f"Falta muestra '{obligatorio}' en {base}")
        return rutas

    def test_post_end_to_end_salud(self):
        rutas = self._muestras("Salud", {
            "relacion": "Relación_Asegurados_Zoho_Salud*.xlsx",
            "cobro": "*Porchat*.xlsx",
            "personas": "Personas_Zoho*.xlsx",
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
                                    {"ramo": "salud", "poliza": "12345", "fuente": "excel", **files})
        self.assertEqual(response.status_code, 200, getattr(response, "content", b"")[:300])
        self.assertIn("X-Conciliacion-Summary", response)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
