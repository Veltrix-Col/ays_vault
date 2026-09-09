from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from conciliador.domain.exceptions import FormatoArchivoNoReconocidoError
from conciliador.sources.salud import cargar_cobro_salud, inferir_periodo_salud


def _escribir_excel(datos: dict, ruta: Path, *, sheet_name: str = "Sheet1") -> None:
    with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
        pd.DataFrame(datos).to_excel(writer, index=False, sheet_name=sheet_name)


class CargarCobroSaludPorchatTests(unittest.TestCase):
    def _archivo(self, ruta: Path) -> None:
        _escribir_excel({
            "DOCUMENTO": ["C111", "C222"],
            "NOMBRE BENEFICIARIO": ["Ana", "Bea"],
            "APELLIDO1 BENEFICIARIO": ["Perez", "Gomez"],
            "APELLIDO2 BENEFICIARIO": ["", ""],
            "NOMBRE TITULAR": ["Ana", "Carlos"],
            "APELLIDO1 TITULAR": ["Perez", "Gomez"],
            "APELLIDO2 TITULAR": ["", ""],
            "TARIFA": [100000, 200000],
            "IVA": [19000, 38000],
            "PERIODO": ["07/01/2026", "07/01/2026"],
        }, ruta)

    def test_carga_formato_porchat(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "porchat.xlsx"
            self._archivo(ruta)

            df = cargar_cobro_salud(ruta)
            self.assertEqual(list(df["documento"]), ["111", "222"])
            self.assertEqual(list(df["valor_cobro"]), [100000.0, 200000.0])
            self.assertEqual(list(df["valor_total_cobro"]), [119000.0, 238000.0])

    def test_periodo_viene_de_la_columna_periodo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "porchat.xlsx"
            self._archivo(ruta)
            self.assertEqual(inferir_periodo_salud(ruta), (7, 2026))

    def test_mes_y_anio_explicitos_no_leen_el_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "porchat.xlsx"
            self._archivo(ruta)
            self.assertEqual(inferir_periodo_salud(ruta, mes=3, anio=2027), (3, 2027))


class CargarCobroSaludSuraTests(unittest.TestCase):
    def _archivo(self, ruta: Path) -> None:
        _escribir_excel({
            "Tomador": ["EL SOCIAL SAS", "EL SOCIAL SAS"],
            "NIT Tomador": [9002970561, 9002970561],
            "Fecha": ["2026-08-01", "2026-08-01"],
            "Poliza": [91000813714, 91000813714],
            "Nombre Afiliado": ["JUAN GREGORIO DUQUE POSADA", "JUAN GREGORIO DUQUE POSADA"],
            "Numero de identificacion (afiliado)": [71744503, 71744503],
            "Nombre Asegurado": ["JUAN GREGORIO DUQUE POSADA", "CANDELARIA ZAPATA LOPEZ"],
            "Numero de identificacion (asegurado)": [71744503, 1035015652],
            "Parentesco": ["AFILIADO(A)", "HIJO(A)"],
            "Prima periodo": ["$529.579", "$529.579"],
            "Descuento EPS SURA": ["$11.400", "$11.400"],
            "IVA": ["$26.478,95", "$26.478,95"],
            "Prima total": ["$556.057,95", "$556.057,95"],
        }, ruta, sheet_name="Asegurados")

    def test_carga_formato_sura_directo_con_extension_xls(self):
        # Extension .xls: el archivo de muestra real es OLE2/BIFF legado, pero
        # ExcelWriter(engine="openpyxl") solo puede escribir zip -- lo que
        # importa aqui es que ningun engine quede forzado en salud.py, para
        # que pandas detecte por contenido cualquiera de los dos casos.
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "CobroAsegurados_el social.xls"
            self._archivo(ruta)

            df = cargar_cobro_salud(ruta)
            self.assertEqual(list(df["documento"]), ["71744503", "1035015652"])
            self.assertAlmostEqual(df["valor_cobro"].iloc[0], 529579.0)
            self.assertAlmostEqual(df["valor_iva_cobro"].iloc[0], 26478.95)
            self.assertAlmostEqual(df["valor_total_cobro"].iloc[0], 556057.95)
            self.assertEqual(df["nombre"].iloc[1], "CANDELARIA ZAPATA LOPEZ")
            self.assertEqual(df["nombre_titular"].iloc[1], "JUAN GREGORIO DUQUE POSADA")

    def test_periodo_viene_de_la_columna_fecha(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "CobroAsegurados_el social.xls"
            self._archivo(ruta)
            self.assertEqual(inferir_periodo_salud(ruta), (8, 2026))


class DetectarFormatoTests(unittest.TestCase):
    def test_formato_no_reconocido_lanza_error_util(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "raro.xlsx"
            _escribir_excel({"ColumnaRara": [1, 2]}, ruta)
            with self.assertRaises(FormatoArchivoNoReconocidoError):
                cargar_cobro_salud(ruta)


if __name__ == "__main__":
    unittest.main()
