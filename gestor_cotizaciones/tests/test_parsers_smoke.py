"""Smoke test de los 3 parsers deterministas de Movilidad (sin credenciales de IA).

Corre los adaptadores de AXA/Bolívar/SURA contra `gestor_cotizaciones/samples/`
con `llm=None` (solo lo determinista: vehículos, tasas, deducibles de RC) y
valida los conteos de vehículos que ya documentó el prototipo original. No
valida coberturas narrativas ni el consolidado (eso requiere IA real, ver
`gestor_cotizaciones/cli.py`).

Las cotizaciones de muestra son datos reales de cliente (tomador, NIT, flota,
primas): nunca se versionan (ver `.gitignore`). Este test se salta -- no
falla -- si nadie las puso en `gestor_cotizaciones/samples/` en esta máquina;
mismo criterio que `conciliacion.tests` con `CONCILIADOR_SAMPLE_ROOT`.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from gestor_cotizaciones.core.adaptador import cargar_catalogo
from gestor_cotizaciones.core.registry import asignar_archivos, cargar_registro, instanciar

PKG_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = PKG_DIR / "config"
SAMPLES = sorted(
    p for p in (PKG_DIR / "samples").glob("40_*") if p.suffix in {".xlsx", ".docx"}
)

VEHICULOS_ESPERADOS = {"sura": 24, "bolivar": 30, "axa": 30}


def _extraer_todas() -> dict[str, object]:
    catalogo = cargar_catalogo("movilidad", base=CONFIG_DIR)
    registro = cargar_registro("movilidad", base=CONFIG_DIR)
    grupos, sueltos = asignar_archivos(registro, SAMPLES)
    assert not sueltos, f"archivos de muestra sin adaptador: {sueltos}"

    cotizaciones = {}
    for aseguradora_id, archivos in grupos.items():
        adaptador = instanciar(
            registro, aseguradora_id, catalogo=catalogo, llm=None,
            es_actual=(aseguradora_id == "sura"), opciones={"nit": "8909268031"},
        )
        cotizaciones[aseguradora_id] = adaptador.extraer(archivos)
    return cotizaciones


class ParsersDeterministasMovilidadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SAMPLES:
            raise unittest.SkipTest(
                "No hay cotizaciones de muestra en gestor_cotizaciones/samples/ en esta "
                "máquina (son datos reales de cliente, no se versionan)."
            )
        cls.cotizaciones = _extraer_todas()

    def test_detecta_las_tres_aseguradoras(self) -> None:
        self.assertEqual(set(self.cotizaciones), set(VEHICULOS_ESPERADOS))

    def test_conteo_de_vehiculos_por_aseguradora(self) -> None:
        for aseguradora_id, esperado in VEHICULOS_ESPERADOS.items():
            cot = self.cotizaciones[aseguradora_id]
            self.assertEqual(
                len(cot.vehiculos), esperado,
                f"{aseguradora_id}: se esperaban {esperado} vehículos, se obtuvieron {len(cot.vehiculos)}",
            )

    def test_planes_no_vacios(self) -> None:
        for aseguradora_id, cot in self.cotizaciones.items():
            self.assertTrue(cot.planes, f"{aseguradora_id}: sin planes")

    def test_tasas_por_clase_en_axa_y_bolivar(self) -> None:
        # SURA cotiza a tasa individual (por vehículo, no por clase/rango): su
        # tabla de tasas queda vacía por diseño, ver README del prototipo.
        for aseguradora_id in ("axa", "bolivar"):
            cot = self.cotizaciones[aseguradora_id]
            self.assertTrue(cot.tasas, f"{aseguradora_id}: sin tasas")


if __name__ == "__main__":
    unittest.main()
