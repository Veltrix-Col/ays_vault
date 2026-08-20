from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from conciliador.rules.base import RuleContext
from conciliador.rules.recibo import ReciboConciliacionRule
from conciliador.sources.content_understanding import ReciboExtraido


def _ctx(*, datos_extra: dict, cobro: pd.DataFrame | None = None) -> RuleContext:
    if cobro is None:
        cobro = pd.DataFrame([{"documento": "111", "valor_total_cobro": 100.0}])
    return RuleContext(
        relacion=pd.DataFrame(), cobro=cobro, novedades=pd.DataFrame(), personas=set(),
        mes=1, anio=2026, ramo="salud", clave_col="documento",
        periodo="Enero 2026", hoy=date(2026, 1, 15), datos_extra=datos_extra,
    )


class ReciboConciliacionRuleTests(unittest.TestCase):
    """La validacion de recibo (PDF) con IA es, por ahora, solo advertencia:
    ningun Incidente que produce esta regla debe ser bloqueante, sin importar
    si el recibo falta, cuadra o no cuadra."""

    def test_recibo_no_disponible_es_advertencia_no_bloqueante(self):
        ctx = _ctx(datos_extra={})
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(len(incidentes), 1)
        self.assertFalse(incidentes[0].bloqueante)

    def test_recibo_que_no_cuadra_es_advertencia_no_bloqueante(self):
        recibo = ReciboExtraido(poliza="123", valor_total=1000.0, riesgos=[{"valor_total": 1}])
        ctx = _ctx(datos_extra={"recibo_cu": recibo})
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertTrue(incidentes)
        self.assertTrue(all(not i.bloqueante for i in incidentes))

    def test_recibo_que_cuadra_es_advertencia_no_bloqueante(self):
        recibo = ReciboExtraido(poliza="123", valor_total=100.0, riesgos=[{"valor_total": 100.0}])
        ctx = _ctx(datos_extra={"recibo_cu": recibo})
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(len(incidentes), 1)
        self.assertFalse(incidentes[0].bloqueante)


if __name__ == "__main__":
    unittest.main()
