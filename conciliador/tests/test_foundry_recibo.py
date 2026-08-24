from __future__ import annotations

import unittest

from conciliador.sources.foundry_recibo import ReciboExtraido


class ReciboExtraidoDesdeJsonTests(unittest.TestCase):
    """`fecha_expedicion` es el unico campo del DTO validado con un formato
    estricto (YYYY-MM-DD, el mismo que ya usan las escrituras de fecha a
    Zoho): un valor que el modelo devuelva en otro formato debe degradar a
    `None` en vez de propagarse tal cual hacia una escritura posterior."""

    def _campos(self, **overrides) -> dict:
        base = {
            "numero_poliza": "123", "numero_recibo": "R-1",
            "valor_sin_iva": 90.0, "valor_iva": 10.0, "valor_total_a_pagar": 100.0,
            "fecha_expedicion": "2026-01-10",
        }
        base.update(overrides)
        return base

    def test_fecha_expedicion_iso_valida_se_conserva(self):
        recibo = ReciboExtraido.desde_json(self._campos())
        self.assertEqual(recibo.fecha_expedicion, "2026-01-10")

    def test_fecha_expedicion_null_queda_none(self):
        recibo = ReciboExtraido.desde_json(self._campos(fecha_expedicion=None))
        self.assertIsNone(recibo.fecha_expedicion)

    def test_fecha_expedicion_con_formato_invalido_degrada_a_none(self):
        recibo = ReciboExtraido.desde_json(self._campos(fecha_expedicion="15/03/2026"))
        self.assertIsNone(recibo.fecha_expedicion)

    def test_fecha_expedicion_no_calendario_degrada_a_none(self):
        recibo = ReciboExtraido.desde_json(self._campos(fecha_expedicion="2026-02-30"))
        self.assertIsNone(recibo.fecha_expedicion)


if __name__ == "__main__":
    unittest.main()
