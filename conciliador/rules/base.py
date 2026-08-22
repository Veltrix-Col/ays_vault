"""Contrato de las reglas de incidentes (patron Strategy) y el contexto que
reciben.

Cada regla es una clase pequeña con una sola responsabilidad: mirar el
contexto y devolver 0..N `Incidente`. El motor (`conciliador.engine`) no
sabe nada de reglas concretas, solo itera la lista `RamoConfig.reglas` -- asi
agregar un tipo de incidente nuevo es agregar una clase, no editar una
funcion de 150 lineas (principio abierto/cerrado).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd

from conciliador.domain.models import Incidente


@dataclass
class RuleContext:
    """Todo lo que una regla necesita para evaluarse. Es inmutable en la
    practica: el motor arma un contexto por corrida y se lo pasa a cada
    regla, ninguna regla modifica el contexto de otra."""

    relacion: pd.DataFrame  # ya trae la columna booleana 'esperado'
    cobro: pd.DataFrame
    novedades: pd.DataFrame
    personas: set[str]
    mes: int
    anio: int
    ramo: str
    clave_col: str
    periodo: str
    hoy: date
    datos_extra: dict[str, object] = field(default_factory=dict)
    # Numero de poliza tal como lo escribio el usuario en el formulario (o
    # --poliza en el CLI). "" cuando no aplica (p. ej. modo legado sin API).
    poliza: str = ""

    @property
    def claves_cobro(self) -> set[str]:
        return set(self.cobro[self.clave_col])

    @property
    def claves_relacion(self) -> set[str]:
        return set(self.relacion[self.clave_col])


class IncidentRule(Protocol):
    """Una regla de conciliacion: mira el contexto, devuelve incidentes."""

    def generar(self, ctx: RuleContext) -> list[Incidente]: ...


def buscar_novedad(clave: str, documento: str, ctx: RuleContext) -> tuple[str, str]:
    """Cruza un registro contra el reporte de novedades del ramo (si existe).

    Devuelve ("N/D", ...) si el ramo todavia no tiene archivo de novedades
    -- distinto de ("No", "") para no insinuar que ya se verifico y
    realmente no hay novedad."""
    novedades = ctx.novedades
    if novedades.empty:
        return "N/D", "Archivo de novedades no disponible todavia"
    coincidencias = (novedades[novedades[ctx.clave_col] == clave]
                      if clave and ctx.clave_col in novedades.columns else pd.DataFrame())
    if coincidencias.empty:
        coincidencias = novedades[novedades["documento"] == documento]
    if coincidencias.empty:
        return "No", ""
    detalles = []
    for _, fila in coincidencias.iterrows():
        fecha = fila["fecha_novedad"].date() if pd.notna(fila["fecha_novedad"]) else "s/f"
        detalles.append(f"{fila['estado_novedad']} ({fecha})")
    return "Si", "; ".join(detalles)


def incidente_base(ctx: RuleContext, fila, tipo: str, **overrides) -> Incidente:
    """Construye un Incidente tomando los campos comunes de `fila` (una fila
    de relacion o de cobro) y dejando que la regla concreta sobreescriba
    solo lo que le interesa."""
    datos = {
        "fecha_reporte": ctx.hoy, "ramo": ctx.ramo, "periodo": ctx.periodo, "tipo_incidente": tipo,
        "placa": fila.get("placa", ""), "documento": fila.get("documento", ""), "nombre": fila.get("nombre", ""),
        "titular": fila.get("nombre_titular", ""), "parentesco": fila.get("parentesco", ""),
        "estado_zoho": fila.get("estado_asegurado", ""),
        "valor_zoho": fila.get("valor_zoho"), "valor_cobro": fila.get("valor_cobro"),
        "valor_iva_cobro": fila.get("valor_iva_cobro"), "valor_total_cobro": fila.get("valor_total_cobro"),
    }
    datos.update(overrides)
    return Incidente(**datos)
