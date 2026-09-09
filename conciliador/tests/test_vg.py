from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from conciliador.domain.exceptions import FormatoArchivoNoReconocidoError
from conciliador.sources.vg import cargar_cobro_vg_deudores, datos_extra_vg


def _escribir_hoja(ruta: Path, sheet_name: str, filas: list[list]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for fila in filas:
        ws.append(fila)
    wb.save(ruta)


def _archivo_ava(ruta: Path) -> None:
    _escribir_hoja(ruta, "Desglose de Coberturas", [
        ["AVA - Reporte de cobro", None, None, None, None, None],
        ["# RIESGO", "ID AFILIADO", "ID ASEGURADO", "NOMBRE DEL ASEGURADO", "PRIMA ASEGURADO", "VALOR IVA"],
        [1, "C111", "C111", "ANA PEREZ", 1000.0, 0.0],
        [2, "C111", "C222", "LUIS PEREZ", 2000.0, 0.0],
        [None, None, None, "TOTAL", 3000.0, 0.0],
    ])


def _archivo_riesgos_vigentes_sin_metadata(ruta: Path) -> None:
    _escribir_hoja(ruta, "Facturas colectivas", [
        ["ID Afiliado", "ID Asegurado", "Nombre", "Certificado", "Prima Periodo"],
        ["C111", "C111", "ANA PEREZ", 2001, "$                   430.00"],
        ["C222", "C222", "LUIS GOMEZ", 2002, 500.0],
        ["C222", "C222", "LUIS GOMEZ", 2003, 120.5],  # mismo afiliado+asegurado, otro credito/certificado
    ])


def _archivo_riesgos_vigentes_con_metadata(ruta: Path) -> None:
    _escribir_hoja(ruta, "083000000000", [
        [None, None, None, None, None],
        [None, "Poliza", "083000000000", None, None],
        [None, "Fecha exportacion", "2026-08-01", None, None],
        ["ID Afiliado", "ID Asegurado", "Nombre", "Certificado", "Prima Periodo"],
        ["C111", "C111", "ANA PEREZ", 3001, 430.0],
    ])


def _archivo_no_reconocido(ruta: Path) -> None:
    _escribir_hoja(ruta, "Hoja1", [["ColumnaRara", "OtraColumna"], [1, 2]])


def _archivo_con_codigo_credito(ruta: Path, *, encabezado_credito: str) -> None:
    _escribir_hoja(ruta, "083000000000", [
        ["ID Afiliado", "ID Asegurado", "Nombre", "Certificado", encabezado_credito, "Prima Periodo"],
        ["C111", "C111", "ANA PEREZ", 2001, "26000195", 430.0],
        ["C222", "C222", "LUIS GOMEZ", 2002, None, 500.0],  # sin codigo de credito para este riesgo
    ])


class CargarCobroVgDeudoresAvaTests(unittest.TestCase):
    def test_sigue_leyendo_el_formato_ava_sin_cambios(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "AVA_Cobro.xlsx"
            _archivo_ava(ruta)

            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["documento"]), ["111", "222"])
            self.assertEqual(list(df["subriesgo"]), ["1", "2"])
            self.assertEqual(list(df["valor_total_cobro"]), [1000.0, 2000.0])

    def test_datos_extra_incluye_desglose_de_coberturas(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "AVA_Cobro.xlsx"
            _archivo_ava(ruta)
            extra = datos_extra_vg(ruta)
            self.assertIn("desglose_coberturas", extra)
            self.assertEqual(len(extra["desglose_coberturas"]), 2)


class CargarCobroVgDeudoresRiesgosVigentesTests(unittest.TestCase):
    def test_sin_bloque_de_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "exportado_muestra.xlsx"
            _archivo_riesgos_vigentes_sin_metadata(ruta)

            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["documento"]), ["111", "222", "222"])
            self.assertEqual(list(df["documento_titular"]), ["111", "222", "222"])
            # Mismo afiliado+asegurado, Certificado distinto -> subriesgo/clave distintos.
            self.assertEqual(list(df["subriesgo"]), ["2001", "2002", "2003"])
            self.assertEqual(len(set(df["clave"])), 3)

    def test_prima_periodo_como_texto_con_simbolo_de_moneda(self):
        # '$                   430.00': si se parseara con pd.to_numeric quedaria en 0.0 en silencio.
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "exportado_muestra.xlsx"
            _archivo_riesgos_vigentes_sin_metadata(ruta)
            df = cargar_cobro_vg_deudores(ruta)
            self.assertAlmostEqual(df["valor_total_cobro"].iloc[0], 430.0)

    def test_prima_periodo_numerica_tambien_funciona(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "exportado_muestra.xlsx"
            _archivo_riesgos_vigentes_sin_metadata(ruta)
            df = cargar_cobro_vg_deudores(ruta)
            self.assertAlmostEqual(df["valor_total_cobro"].iloc[1], 500.0)

    def test_con_bloque_de_metadata_antes_de_la_tabla(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "RIESGOS-083000000000-VIGENTES-1.xls"
            _archivo_riesgos_vigentes_con_metadata(ruta)

            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["documento"]), ["111"])
            self.assertEqual(list(df["subriesgo"]), ["3001"])
            self.assertAlmostEqual(df["valor_total_cobro"].iloc[0], 430.0)

    def test_no_tiene_iva(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "exportado_muestra.xlsx"
            _archivo_riesgos_vigentes_sin_metadata(ruta)
            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["valor_iva_cobro"]), [0.0, 0.0, 0.0])

    def test_datos_extra_no_incluye_desglose_de_coberturas(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "exportado_muestra.xlsx"
            _archivo_riesgos_vigentes_sin_metadata(ruta)
            self.assertEqual(datos_extra_vg(ruta), {})

    def test_sin_columna_codigo_credito_queda_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "exportado_muestra.xlsx"
            _archivo_riesgos_vigentes_sin_metadata(ruta)
            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["codigo_credito"]), ["", "", ""])


class CodigoCreditoTests(unittest.TestCase):
    def test_columna_con_nombre_correcto(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "RIESGOS-083000000000-VIGENTES-1.xls"
            _archivo_con_codigo_credito(ruta, encabezado_credito="Código de Crédito")
            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["codigo_credito"]), ["26000195", ""])

    def test_columna_con_codificacion_corrompida(self):
        # El archivo real de Sura trae el nombre de esta columna con el
        # caracter de reemplazo Unicode en vez de las tildes (ver docstring
        # de _PATRON_COLUMNA_CODIGO_CREDITO en conciliador.sources.vg).
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "RIESGOS-083000000000-VIGENTES-1.xls"
            _archivo_con_codigo_credito(ruta, encabezado_credito="C�digo de Cr�dito")
            df = cargar_cobro_vg_deudores(ruta)
            self.assertEqual(list(df["codigo_credito"]), ["26000195", ""])


class FormatoNoReconocidoTests(unittest.TestCase):
    def test_lanza_error_util(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "raro.xlsx"
            _archivo_no_reconocido(ruta)
            with self.assertRaises(FormatoArchivoNoReconocidoError):
                cargar_cobro_vg_deudores(ruta)


if __name__ == "__main__":
    unittest.main()
