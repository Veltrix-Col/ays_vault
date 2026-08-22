from __future__ import annotations

import unittest
from datetime import date

from conciliador.domain.models import Incidente, ReporteConciliacion


def _incidente(tipo: str, *, bloqueante: bool) -> Incidente:
    return Incidente(
        fecha_reporte=date(2026, 1, 15), ramo="salud", periodo="Enero 2026",
        tipo_incidente=tipo, bloqueante=bloqueante,
    )


class ReporteConciliacionTests(unittest.TestCase):
    def test_sin_incidentes_esta_vacio(self):
        reporte = ReporteConciliacion(ramo="salud", periodo="Enero 2026", generado_en=date(2026, 1, 15), incidentes=[])
        self.assertTrue(reporte.esta_vacio)
        self.assertEqual(reporte.total_incidentes, 0)
        self.assertEqual(reporte.total_advertencias, 0)

    def test_solo_advertencias_sigue_esta_vacio(self):
        # Caso real: falta el recibo (PDF) => una advertencia no bloqueante.
        # No debe impedir "esta_vacio" ni el acceso a conciliar en Zoho.
        reporte = ReporteConciliacion(
            ramo="salud", periodo="Enero 2026", generado_en=date(2026, 1, 15),
            incidentes=[_incidente("Validación de recibo (PDF) no realizada", bloqueante=False)],
        )
        self.assertTrue(reporte.esta_vacio)
        self.assertEqual(reporte.total_incidentes, 0)
        self.assertEqual(reporte.total_advertencias, 1)
        # Pero la fila sigue en el Excel para trazabilidad.
        self.assertEqual(len(reporte.to_dataframe()), 1)

    def test_incidente_bloqueante_rompe_esta_vacio(self):
        reporte = ReporteConciliacion(
            ramo="salud", periodo="Enero 2026", generado_en=date(2026, 1, 15),
            incidentes=[
                _incidente("Huérfano en cobro", bloqueante=True),
                _incidente("Validación de recibo (PDF) no realizada", bloqueante=False),
            ],
        )
        self.assertFalse(reporte.esta_vacio)
        self.assertEqual(reporte.total_incidentes, 1)
        self.assertEqual(reporte.total_advertencias, 1)
        self.assertEqual(len(reporte.to_dataframe()), 2)


if __name__ == "__main__":
    unittest.main()
