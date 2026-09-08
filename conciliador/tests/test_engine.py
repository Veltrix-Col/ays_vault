from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from conciliador.engine import esperado_en_cobro


def _fila(estado_asegurado: str, fecha_retiro=pd.NaT) -> pd.Series:
    return pd.Series({"estado_asegurado": estado_asegurado, "fecha_retiro": fecha_retiro})


class EsperadoEnCobroTests(unittest.TestCase):
    def test_activo_es_esperado(self):
        self.assertTrue(esperado_en_cobro(_fila("Activo"), mes=1, anio=2026))

    def test_activo_con_ajuste_es_esperado(self):
        # Estado real del picklist Riesgos1.Estado (docs/zoho/*/latest/picklists.json):
        # factura igual que "Activo", solo cambia el valor.
        self.assertTrue(esperado_en_cobro(_fila("Activo con ajuste"), mes=1, anio=2026))

    def test_activo_sin_cobro_no_es_esperado(self):
        # Opuesto de "Activo con ajuste": activo pero deliberadamente sin facturar.
        self.assertFalse(esperado_en_cobro(_fila("Activo sin cobro"), mes=1, anio=2026))

    def test_excluido_con_cobro_del_mes_es_esperado(self):
        fila = _fila("Excluido con cobro", fecha_retiro=pd.Timestamp(2026, 1, 15))
        self.assertTrue(esperado_en_cobro(fila, mes=1, anio=2026))

    def test_excluido_con_cobro_de_otro_mes_no_es_esperado(self):
        fila = _fila("Excluido con cobro", fecha_retiro=pd.Timestamp(2025, 12, 15))
        self.assertFalse(esperado_en_cobro(fila, mes=1, anio=2026))

    def test_excluido_con_cobro_sin_fecha_retiro_es_esperado(self):
        self.assertTrue(esperado_en_cobro(_fila("Excluido con cobro"), mes=1, anio=2026))

    def test_excluido_no_es_esperado(self):
        self.assertFalse(esperado_en_cobro(_fila("Excluido"), mes=1, anio=2026))

    def test_cancelado_no_es_esperado(self):
        self.assertFalse(esperado_en_cobro(_fila("Cancelado"), mes=1, anio=2026))

    def test_congelado_no_es_esperado(self):
        self.assertFalse(esperado_en_cobro(_fila("Congelado"), mes=1, anio=2026))


if __name__ == "__main__":
    unittest.main()
