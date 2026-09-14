"""Metadata de presentación de los ramos para el Cotizador de Renovaciones.

No contiene lógica de extracción: el motor real vive en el paquete
`gestor_cotizaciones`. Las aseguradoras del selector "Compañía actual" se
derivan siempre de `gestor_cotizaciones/config/ramos/<ramo>/adaptadores.yaml`
para que agregar una aseguradora nueva (solo config + un adaptador, sin tocar
Django) la haga aparecer aquí automáticamente.
"""
from __future__ import annotations

from pathlib import Path

from gestor_cotizaciones.core.registry import cargar_registro

CONFIG_DIR = Path(__file__).resolve().parent.parent / "gestor_cotizaciones" / "config"

# Ramos habilitados y su nombre visible (el orden define el del selector).
# Salud y Vida Grupo se agregan aquí cuando tengan su `config/ramos/<ramo>/`
# y su `gestor_cotizaciones/ramos/<ramo>/render.py`.
RAMOS_HABILITADOS: list[tuple[str, str]] = [
    ("movilidad", "Movilidad Colectivos"),
]
RAMO_CHOICES = list(RAMOS_HABILITADOS)
RAMO_CODIGOS = frozenset(codigo for codigo, _ in RAMOS_HABILITADOS)


def aseguradoras_de_ramo(codigo: str) -> list[tuple[str, str]]:
    """[(id, nombre)] de las aseguradoras con adaptador registrado para el ramo,
    para poblar el selector de "compañía actual" (la que aporta la prima
    anterior en el consolidado)."""
    if codigo not in RAMO_CODIGOS:
        return []
    registro = cargar_registro(codigo, base=CONFIG_DIR)
    return [(a["id"], a["id"].upper()) for a in registro["adaptadores"]]


def ids_conocidos_de_ramo(codigo: str) -> frozenset[str]:
    return frozenset(id_ for id_, _ in aseguradoras_de_ramo(codigo))


def catalogo_aseguradoras() -> dict[str, list[tuple[str, str]]]:
    """Mapa {codigo_ramo: [(id, nombre)]} -- se serializa a JSON para el frontend."""
    return {codigo: aseguradoras_de_ramo(codigo) for codigo in RAMO_CODIGOS}
