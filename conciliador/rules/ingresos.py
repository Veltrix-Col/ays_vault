"""Regla de ingresos nuevos del mes: valida contra Personas_Zoho."""

from __future__ import annotations

from conciliador.domain.models import Incidente
from conciliador.rules.base import RuleContext, buscar_novedad, incidente_base


class IngresoNuevoSinPersonaRule:
    """Si la fecha de ingreso cae en el mes/anio del periodo conciliado y el
    documento no esta en Personas_Zoho, falta crear el contacto en Zoho."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        nuevos = ctx.relacion[
            (ctx.relacion["fecha_ingreso"].dt.month == ctx.mes) & (ctx.relacion["fecha_ingreso"].dt.year == ctx.anio)
        ]
        incidentes = []
        for _, fila in nuevos.iterrows():
            if fila["documento"] in ctx.personas:
                continue
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            incidentes.append(incidente_base(
                ctx, fila, "Ingreso nuevo del mes: persona no encontrada en Personas_Zoho",
                reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion="Falta crear el contacto/persona en Zoho.",
            ))
        return incidentes
