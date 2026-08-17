from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from conciliador.rules.base import RuleContext
from conciliador.rules.duplicados import IdentidadInconsistenteRule, NovedadContradictoriaRule

NOVEDADES_COLUMNAS = ["placa", "documento", "nombre", "estado_novedad", "fecha_novedad",
                       "fecha_ingreso", "fecha_retiro", "valor_novedad", "observaciones"]


def _novedades(filas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(filas, columns=NOVEDADES_COLUMNAS)
    df["fecha_novedad"] = pd.to_datetime(df["fecha_novedad"])
    return df


def _ctx(**overrides) -> RuleContext:
    base = dict(
        relacion=pd.DataFrame(),
        cobro=pd.DataFrame(),
        novedades=_novedades([]),
        personas=set(),
        mes=1, anio=2026, ramo="vg_voluntario", clave_col="clave",
        periodo="Enero 2026", hoy=date(2026, 1, 15), datos_extra={},
    )
    base.update(overrides)
    return RuleContext(**base)


class NovedadContradictoriaRuleTests(unittest.TestCase):
    def setUp(self):
        self.regla = NovedadContradictoriaRule()

    def test_reporta_documento_con_exclusion_e_ingreso(self):
        novedades = _novedades([
            {"placa": "", "documento": "111", "nombre": "Ana", "estado_novedad": "Exclusión",
             "fecha_novedad": "2026-01-05", "fecha_ingreso": pd.NaT, "fecha_retiro": "2026-01-05",
             "valor_novedad": 0, "observaciones": ""},
            {"placa": "", "documento": "111", "nombre": "Ana", "estado_novedad": "Ingreso",
             "fecha_novedad": "2026-01-10", "fecha_ingreso": "2026-01-10", "fecha_retiro": pd.NaT,
             "valor_novedad": 50000, "observaciones": ""},
        ])
        incidentes = self.regla.generar(_ctx(novedades=novedades))
        self.assertEqual(len(incidentes), 1)
        self.assertEqual(incidentes[0].documento, "111")
        self.assertIn("contradictorias", incidentes[0].tipo_incidente)
        self.assertIn("Exclusión", incidentes[0].detalle_novedad)
        self.assertIn("Ingreso", incidentes[0].detalle_novedad)

    def test_no_reporta_dos_novedades_del_mismo_tipo(self):
        novedades = _novedades([
            {"placa": "", "documento": "222", "nombre": "Bea", "estado_novedad": "Ingreso",
             "fecha_novedad": "2026-01-05", "fecha_ingreso": "2026-01-05", "fecha_retiro": pd.NaT,
             "valor_novedad": 1000, "observaciones": ""},
            {"placa": "", "documento": "222", "nombre": "Bea", "estado_novedad": "Ingreso por traslado",
             "fecha_novedad": "2026-01-06", "fecha_ingreso": "2026-01-06", "fecha_retiro": pd.NaT,
             "valor_novedad": 1000, "observaciones": ""},
        ])
        self.assertEqual(self.regla.generar(_ctx(novedades=novedades)), [])

    def test_no_reporta_documento_unico(self):
        novedades = _novedades([
            {"placa": "", "documento": "333", "nombre": "Caz", "estado_novedad": "Exclusión",
             "fecha_novedad": "2026-01-05", "fecha_ingreso": pd.NaT, "fecha_retiro": "2026-01-05",
             "valor_novedad": 0, "observaciones": ""},
        ])
        self.assertEqual(self.regla.generar(_ctx(novedades=novedades)), [])

    def test_novedades_vacias(self):
        self.assertEqual(self.regla.generar(_ctx()), [])


class IdentidadInconsistenteRuleTests(unittest.TestCase):
    def setUp(self):
        self.regla = IdentidadInconsistenteRule()

    def _cobro(self, filas: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(filas)

    def _relacion(self, filas: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(filas)
        df["esperado"] = True
        return df

    def test_reporta_asegurado_distinto_en_misma_casilla_afiliado_subriesgo(self):
        cobro = self._cobro([
            {"documento_titular": "A1", "subriesgo": "1", "documento": "P1", "clave": "A1_P1_1"},
            {"documento_titular": "A1", "subriesgo": "2", "documento": "P1", "clave": "A1_P1_2"},
        ])
        relacion = self._relacion([
            {"documento_titular": "A1", "subriesgo": "1", "documento": "P1", "nombre": "P1",
             "nombre_titular": "A1", "parentesco": "Titular", "estado_asegurado": "Activo",
             "valor_zoho": 100.0, "clave": "A1_P1_1"},
            {"documento_titular": "A1", "subriesgo": "2", "documento": "Q1", "nombre": "Q1",
             "nombre_titular": "A1", "parentesco": "Conyuge", "estado_asegurado": "Activo",
             "valor_zoho": 80.0, "clave": "A1_Q1_2"},
        ])
        incidentes = self.regla.generar(_ctx(cobro=cobro, relacion=relacion))
        self.assertEqual(len(incidentes), 1)
        incidente = incidentes[0]
        self.assertIn("Q1", incidente.estado_zoho)
        self.assertIn("afiliado A1", incidente.observacion)
        self.assertIn("subriesgo 2", incidente.observacion)

    def test_no_reporta_cuando_coincide(self):
        cobro = self._cobro([
            {"documento_titular": "A1", "subriesgo": "1", "documento": "P1", "clave": "A1_P1_1"},
        ])
        relacion = self._relacion([
            {"documento_titular": "A1", "subriesgo": "1", "documento": "P1", "nombre": "P1",
             "nombre_titular": "A1", "parentesco": "Titular", "estado_asegurado": "Activo",
             "valor_zoho": 100.0, "clave": "A1_P1_1"},
        ])
        self.assertEqual(self.regla.generar(_ctx(cobro=cobro, relacion=relacion)), [])

    def test_sin_columna_documento_titular_no_falla(self):
        cobro = pd.DataFrame({"documento": ["P1"], "clave": ["x"]})
        relacion = pd.DataFrame({"documento": ["P1"], "clave": ["x"], "esperado": [True]})
        self.assertEqual(self.regla.generar(_ctx(cobro=cobro, relacion=relacion)), [])

    def test_dataframes_vacios_no_falla(self):
        self.assertEqual(self.regla.generar(_ctx()), [])


if __name__ == "__main__":
    unittest.main()
