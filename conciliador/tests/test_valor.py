from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from conciliador.rules.base import RuleContext
from conciliador.rules.valor import ComparacionExactaRule

NOVEDADES_COLUMNAS = ["placa", "documento", "nombre", "estado_novedad", "fecha_novedad",
                       "fecha_ingreso", "fecha_retiro", "valor_novedad", "observaciones"]


def _ctx(*, valor_zoho: float, valor_cobro: float) -> RuleContext:
    relacion = pd.DataFrame([{
        "documento": "111", "nombre": "Ana", "nombre_titular": "", "parentesco": "",
        "estado_asegurado": "Activo", "valor_zoho": valor_zoho, "esperado": True,
    }])
    cobro = pd.DataFrame([{
        "documento": "111", "valor_cobro": valor_cobro, "valor_iva_cobro": 0.0,
        "valor_total_cobro": valor_cobro,
    }])
    novedades = pd.DataFrame(columns=NOVEDADES_COLUMNAS)
    return RuleContext(
        relacion=relacion, cobro=cobro, novedades=novedades, personas=set(),
        mes=1, anio=2026, ramo="movilidad", clave_col="documento",
        periodo="Enero 2026", hoy=date(2026, 1, 15), datos_extra={},
    )


class ComparacionExactaRuleTests(unittest.TestCase):
    def test_umbral_por_defecto_es_diez_pesos(self):
        self.assertEqual(ComparacionExactaRule().umbral_pesos, 10.0)

    def test_diferencia_menor_al_umbral_no_reporta(self):
        regla = ComparacionExactaRule()  # umbral por defecto: 10
        ctx = _ctx(valor_zoho=100000, valor_cobro=100005)  # diferencia de 5
        self.assertEqual(regla.generar(ctx), [])

    def test_diferencia_igual_al_umbral_si_reporta(self):
        regla = ComparacionExactaRule()
        ctx = _ctx(valor_zoho=100000, valor_cobro=100010)  # diferencia de 10
        incidentes = regla.generar(ctx)
        self.assertEqual(len(incidentes), 1)
        self.assertEqual(incidentes[0].diferencia_valor, 10)

    def test_diferencia_mayor_al_umbral_si_reporta(self):
        regla = ComparacionExactaRule()
        ctx = _ctx(valor_zoho=100000, valor_cobro=100500)
        incidentes = regla.generar(ctx)
        self.assertEqual(len(incidentes), 1)

    def test_umbral_personalizado_se_respeta(self):
        regla = ComparacionExactaRule(umbral_pesos=1.0)
        ctx = _ctx(valor_zoho=100000, valor_cobro=100005)  # diferencia de 5
        incidentes = regla.generar(ctx)
        self.assertEqual(len(incidentes), 1)

    def test_umbral_es_mutable_despues_de_construido(self):
        # Es exactamente lo que hace conciliacion.services.processor al
        # sobreescribir el umbral desde settings.CONCILIACION_UMBRAL_VALOR_EXACTO.
        regla = ComparacionExactaRule()
        regla.umbral_pesos = 50.0
        ctx = _ctx(valor_zoho=100000, valor_cobro=100030)  # diferencia de 30
        self.assertEqual(regla.generar(ctx), [])


if __name__ == "__main__":
    unittest.main()
