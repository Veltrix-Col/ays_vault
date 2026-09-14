from __future__ import annotations

import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.utils.datastructures import MultiValueDict

from cotizador.forms import CotizadorUploadForm

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_like(nombre: str) -> SimpleUploadedFile:
    """Zip mínimo que pasa la validación anti zip-bomb del formulario -- no
    necesita ser un .xlsx real para probar las reglas del formulario."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as paquete:
        paquete.writestr("[Content_Types].xml", "<Types/>")
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type=_XLSX_CONTENT_TYPE)


class CotizadorUploadFormTests(SimpleTestCase):
    def _datos(self, **overrides):
        datos = {"ramo": "movilidad", "nit": "8909268031", "actual": "sura", "otra_aseguradora": ""}
        datos.update(overrides)
        return datos

    def test_actual_se_puebla_con_las_aseguradoras_del_ramo(self):
        form = CotizadorUploadForm(data=self._datos())
        ids = {id_ for id_, _ in form.fields["actual"].choices}
        self.assertEqual(ids, {"sura", "bolivar", "axa"})

    def test_requiere_al_menos_un_archivo(self):
        form = CotizadorUploadForm(data=self._datos(), files=MultiValueDict())
        self.assertFalse(form.is_valid())
        self.assertIn("archivos", form.errors)

    def test_requiere_nit(self):
        form = CotizadorUploadForm(
            data=self._datos(nit=""),
            files=MultiValueDict({"archivos": [_xlsx_like("cotizacion.xlsx")]}),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("nit", form.errors)

    def test_rechaza_extension_no_soportada(self):
        archivo = SimpleUploadedFile("cotizacion.pdf", b"%PDF-1.4", content_type="application/pdf")
        form = CotizadorUploadForm(data=self._datos(), files=MultiValueDict({"archivos": [archivo]}))
        self.assertFalse(form.is_valid())
        self.assertIn("archivos", form.errors)

    def test_acepta_varios_archivos_de_distintas_aseguradoras(self):
        archivos = [_xlsx_like("40_Axa_V1_Flota.xlsx"), _xlsx_like("40_Sura_V1_Flota.xlsx")]
        form = CotizadorUploadForm(data=self._datos(), files=MultiValueDict({"archivos": archivos}))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["archivos"]), 2)

    def test_otra_aseguradora_no_puede_repetir_una_ya_configurada(self):
        form = CotizadorUploadForm(
            data=self._datos(otra_aseguradora="SURA"),
            files=MultiValueDict({"archivos": [_xlsx_like("cotizacion.xlsx")]}),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("otra_aseguradora", form.errors)
