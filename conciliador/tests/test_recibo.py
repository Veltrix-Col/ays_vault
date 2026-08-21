from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from conciliador.rules.base import RuleContext
from conciliador.rules.recibo import ReciboConciliacionRule
from conciliador.sources.foundry_recibo import ReciboExtraido


def _ctx(*, datos_extra: dict, poliza: str = "123", cobro: pd.DataFrame | None = None) -> RuleContext:
    if cobro is None:
        cobro = pd.DataFrame([{"documento": "111", "valor_total_cobro": 100.0}])
    return RuleContext(
        relacion=pd.DataFrame(), cobro=cobro, novedades=pd.DataFrame(), personas=set(),
        mes=1, anio=2026, ramo="salud", clave_col="documento",
        periodo="Enero 2026", hoy=date(2026, 1, 15), datos_extra=datos_extra, poliza=poliza,
    )


def _recibo(**overrides) -> ReciboExtraido:
    base = dict(
        numero_poliza="123", numero_recibo="R-1",
        valor_sin_iva=90.0, valor_iva=10.0, valor_total_a_pagar=100.0,
    )
    base.update(overrides)
    return ReciboExtraido(**base)


class ReciboConciliacionRuleTests(unittest.TestCase):
    """La validacion de recibo (PDF) con IA es, por ahora, solo advertencia:
    ningun Incidente que produce esta regla debe ser bloqueante, sin importar
    si el recibo falta, la poliza no cuadra o el valor no cuadra."""

    def test_recibo_no_disponible_es_advertencia_no_bloqueante(self):
        ctx = _ctx(datos_extra={})
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(len(incidentes), 1)
        self.assertFalse(incidentes[0].bloqueante)

    def test_poliza_y_valor_cuadran_no_genera_incidentes(self):
        ctx = _ctx(datos_extra={"recibo_cu": _recibo()}, poliza="123")
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(incidentes, [])

    def test_poliza_cuadra_tolerando_ceros_a_la_izquierda(self):
        ctx = _ctx(datos_extra={"recibo_cu": _recibo(numero_poliza="00123")}, poliza="123")
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(incidentes, [])

    def test_poliza_que_no_cuadra_genera_incidente_no_bloqueante(self):
        ctx = _ctx(datos_extra={"recibo_cu": _recibo(numero_poliza="999")}, poliza="123")
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(len(incidentes), 1)
        self.assertFalse(incidentes[0].bloqueante)
        self.assertIn("póliza", incidentes[0].tipo_incidente.lower())

    def test_valor_que_no_cuadra_genera_incidente_no_bloqueante(self):
        ctx = _ctx(datos_extra={"recibo_cu": _recibo(valor_total_a_pagar=1000.0)}, poliza="123")
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertEqual(len(incidentes), 1)
        self.assertFalse(incidentes[0].bloqueante)
        self.assertIn("valor_total_a_pagar", incidentes[0].tipo_incidente)

    def test_numero_recibo_y_valores_iva_quedan_en_la_observacion(self):
        ctx = _ctx(datos_extra={"recibo_cu": _recibo(numero_poliza="999", numero_recibo="R-42")}, poliza="123")
        incidentes = ReciboConciliacionRule().generar(ctx)
        self.assertIn("R-42", incidentes[0].observacion)


if __name__ == "__main__":
    unittest.main()
