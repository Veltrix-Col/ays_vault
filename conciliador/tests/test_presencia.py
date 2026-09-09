from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from conciliador.engine import con_columna_esperado
from conciliador.rules.base import RuleContext
from conciliador.rules.presencia import (
    ActivoAusenteEnCobroRule,
    ExcluidoConCobroSinActualizarRule,
    HuerfanoEnCobroRule,
)

NOVEDADES_COLUMNAS = ["placa", "documento", "nombre", "estado_novedad", "fecha_novedad",
                       "fecha_ingreso", "fecha_retiro", "valor_novedad", "observaciones"]


def _novedades_vacio() -> pd.DataFrame:
    df = pd.DataFrame(columns=NOVEDADES_COLUMNAS)
    df["fecha_novedad"] = pd.to_datetime(df["fecha_novedad"])
    return df


def _ctx(*, cobro: pd.DataFrame, relacion: pd.DataFrame) -> RuleContext:
    return RuleContext(
        relacion=relacion, cobro=cobro, novedades=_novedades_vacio(), personas=set(),
        mes=1, anio=2026, ramo="vg_voluntario", clave_col="clave",
        periodo="Enero 2026", hoy=date(2026, 1, 15), datos_extra={},
    )


class SubriesgoSwapNoDuplicaIncidentesTests(unittest.TestCase):
    """Mismo afiliado+asegurado con subriesgo distinto a cada lado (ver
    test_duplicados.IdentidadInconsistenteRuleTests): antes, ademas del
    incidente consolidado de `IdentidadInconsistenteRule`, `HuerfanoEnCobroRule`
    y `ActivoAusenteEnCobroRule` reportaban las mismas dos filas por su cuenta."""

    def _cobro(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"documento_titular": "A1", "subriesgo": "1", "documento": "P1", "clave": "A1_P1_1",
             "valor_cobro": 100.0, "valor_iva_cobro": 0.0, "valor_total_cobro": 100.0},
        ])

    def _relacion(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"documento_titular": "A1", "subriesgo": "2", "documento": "P1", "nombre": "P1",
             "nombre_titular": "A1", "parentesco": "Titular", "estado_asegurado": "Activo",
             "valor_zoho": 100.0, "clave": "A1_P1_2", "esperado": True},
        ])

    def test_huerfano_no_reporta_la_fila_explicada_por_identidad(self):
        incidentes = HuerfanoEnCobroRule().generar(_ctx(cobro=self._cobro(), relacion=self._relacion()))
        self.assertEqual(incidentes, [])

    def test_activo_ausente_no_reporta_la_fila_explicada_por_identidad(self):
        incidentes = ActivoAusenteEnCobroRule().generar(_ctx(cobro=self._cobro(), relacion=self._relacion()))
        self.assertEqual(incidentes, [])


class ExcluidoConCobroSinActualizarRuleTests(unittest.TestCase):
    """Periodo de reporte fijo: Enero 2026 (mes=1, anio=2026, ver `_ctx`)."""

    def _relacion(self, fecha_retiro) -> pd.DataFrame:
        df = pd.DataFrame([
            {"documento_titular": "", "subriesgo": "", "documento": "111", "nombre": "Ana",
             "clave": "111", "estado_asegurado": "Excluido con cobro", "fecha_retiro": fecha_retiro,
             "valor_zoho": 100.0},
        ])
        return con_columna_esperado(df, mes=1, anio=2026)

    def _cobro_vacio(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["clave", "valor_cobro", "valor_iva_cobro", "valor_total_cobro"])

    def test_reporta_cuando_ya_paso_el_mes_de_gracia_y_no_esta_en_el_cobro(self):
        relacion = self._relacion(pd.Timestamp(2025, 11, 15))  # retiro en noviembre, reporte de enero
        incidentes = ExcluidoConCobroSinActualizarRule().generar(_ctx(cobro=self._cobro_vacio(), relacion=relacion))
        self.assertEqual(len(incidentes), 1)
        self.assertIn("actualizar estado", incidentes[0].tipo_incidente.lower())

    def test_no_reporta_durante_el_mes_de_gracia(self):
        relacion = self._relacion(pd.Timestamp(2026, 1, 10))  # retiro en el mismo mes del reporte
        self.assertEqual(
            ExcluidoConCobroSinActualizarRule().generar(_ctx(cobro=self._cobro_vacio(), relacion=relacion)), []
        )

    def test_no_reporta_si_sigue_apareciendo_en_el_cobro(self):
        relacion = self._relacion(pd.Timestamp(2025, 11, 15))
        cobro = pd.DataFrame([{"clave": "111", "valor_cobro": 50.0, "valor_iva_cobro": 0.0, "valor_total_cobro": 50.0}])
        self.assertEqual(ExcluidoConCobroSinActualizarRule().generar(_ctx(cobro=cobro, relacion=relacion)), [])

    def test_no_reporta_sin_fecha_retiro(self):
        # Dato incompleto: lo cubre DatoIncompletoExcluidoConCobroRule, no esta regla.
        relacion = self._relacion(pd.NaT)
        self.assertEqual(
            ExcluidoConCobroSinActualizarRule().generar(_ctx(cobro=self._cobro_vacio(), relacion=relacion)), []
        )


class HuerfanoEnCobroRuleTests(unittest.TestCase):
    def test_reporta_huerfano_genuino(self):
        cobro = pd.DataFrame([
            {"documento_titular": "A1", "subriesgo": "1", "documento": "P1", "clave": "A1_P1_1",
             "valor_cobro": 100.0, "valor_iva_cobro": 0.0, "valor_total_cobro": 100.0},
        ])
        relacion = pd.DataFrame(columns=["documento_titular", "subriesgo", "documento", "clave", "esperado"])
        incidentes = HuerfanoEnCobroRule().generar(_ctx(cobro=cobro, relacion=relacion))
        self.assertEqual(len(incidentes), 1)


if __name__ == "__main__":
    unittest.main()
