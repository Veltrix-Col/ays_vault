from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from cotizador.services import CotizadorOutput, CotizadorProcessingError


def _xlsx_like(nombre: str = "40_Axa_V1_Flota.xlsx") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as paquete:
        paquete.writestr("[Content_Types].xml", "<Types/>")
    return SimpleUploadedFile(
        nombre, buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _datos_formulario():
    return {"ramo": "movilidad", "nit": "8909268031", "actual": "sura", "otra_aseguradora": ""}


class CotizadorUploadViewTests(TestCase):
    def test_get_renderiza_el_formulario(self):
        response = self.client.get(reverse("cotizador:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cotizador de Renovaciones")

    @patch("cotizador.views.generar_consolidado")
    def test_post_exitoso_devuelve_el_xlsx_como_adjunto(self, mock_generar):
        mock_generar.return_value = CotizadorOutput(
            content=b"contenido-del-excel",
            filename="Consolidado_movilidad_8909268031.xlsx",
            summary={"ramo": "movilidad", "aseguradoras": ["axa", "sura"]},
        )
        response = self.client.post(
            reverse("cotizador:index"),
            data={**_datos_formulario(), "archivos": [_xlsx_like()]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"contenido-del-excel")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("X-Cotizador-Summary", response)
        mock_generar.assert_called_once()

    @patch("cotizador.views.generar_consolidado")
    def test_error_de_negocio_reconstruye_el_formulario_con_el_mensaje(self, mock_generar):
        mock_generar.side_effect = CotizadorProcessingError("No reconozco estos archivos: rara.xlsx")
        response = self.client.post(
            reverse("cotizador:index"),
            data={**_datos_formulario(), "archivos": [_xlsx_like()]},
        )
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "No reconozco estos archivos", status_code=422)

    def test_post_sin_archivos_no_llega_al_motor(self):
        with patch("cotizador.views.generar_consolidado") as mock_generar:
            response = self.client.post(reverse("cotizador:index"), data=_datos_formulario())
        self.assertEqual(response.status_code, 422)
        mock_generar.assert_not_called()
