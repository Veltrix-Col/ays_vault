from __future__ import annotations

import unittest

import pandas as pd

from conciliador.service import _codigos_credito_pendientes


class CodigosCreditoPendientesTests(unittest.TestCase):
    def test_none_cuando_el_cobro_no_tiene_la_columna(self):
        cobro = pd.DataFrame({"documento": ["111"]})
        self.assertIsNone(_codigos_credito_pendientes(cobro))

    def test_lista_vacia_cuando_ninguna_fila_trae_valor(self):
        cobro = pd.DataFrame({
            "documento_titular": ["111"], "documento": ["111"],
            "subriesgo": ["2001"], "codigo_credito": [""],
        })
        self.assertEqual(_codigos_credito_pendientes(cobro), [])

    def test_solo_incluye_filas_con_codigo_credito(self):
        cobro = pd.DataFrame({
            "documento_titular": ["111", "222"], "documento": ["111", "222"],
            "subriesgo": ["2001", "2002"], "codigo_credito": ["26000195", ""],
        })
        pendientes = _codigos_credito_pendientes(cobro)
        self.assertEqual(pendientes, [
            {"documento_titular": "111", "documento": "111", "subriesgo": "2001", "codigo_credito": "26000195"},
        ])


if __name__ == "__main__":
    unittest.main()
