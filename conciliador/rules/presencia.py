"""Reglas sobre presencia/ausencia de registros entre Zoho y el cobro
(las 4 diferencias que se piden en cualquier ramo: activos que no aparecen,
excluidos que siguen apareciendo, y registros del cobro sin relacion)."""

from __future__ import annotations

from conciliador.domain.models import Incidente
from conciliador.parsing.normalizadores import strip_accents
from conciliador.rules.base import RuleContext, buscar_novedad, incidente_base


class DatoIncompletoExcluidoConCobroRule:
    """'Excluido con cobro' sin fecha de retiro: no se puede saber si debe
    seguir facturando este mes o no -- se marca como anomalia de datos en
    vez de asumir silenciosamente un lado u otro."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        es_excluido_con_cobro = ctx.relacion["estado_asegurado"].apply(
            lambda e: strip_accents(e).lower().startswith("excluido con cobro")
        )
        filas = ctx.relacion[es_excluido_con_cobro & ctx.relacion["fecha_retiro"].isna()]
        incidentes = []
        for _, fila in filas.iterrows():
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            incidentes.append(incidente_base(
                ctx, fila, "Dato incompleto: Excluido con cobro sin fecha de retiro",
                reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion="Revisar fecha de retiro en Zoho para saber si debe seguir facturando.",
            ))
        return incidentes


class ActivoAusenteEnCobroRule:
    """Activo (o excluido-con-cobro vigente) en Zoho que no aparece en el cobro."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        faltantes = ctx.relacion[ctx.relacion["esperado"] & ~ctx.relacion[ctx.clave_col].isin(ctx.claves_cobro)]
        incidentes = []
        for _, fila in faltantes.iterrows():
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            incidentes.append(incidente_base(
                ctx, fila, "Activo/con cobro vigente en Zoho pero ausente en el cobro del asegurador",
                reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion="El asegurador deberia estar cobrando este registro y no lo incluyo.",
            ))
        return incidentes


class ExcluidoIndebidoEnCobroRule:
    """Excluido en Zoho (o excluido-con-cobro ya vencido) que sigue en el cobro."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        no_esperados = ctx.relacion[~ctx.relacion["esperado"]]
        indebidos = no_esperados[no_esperados[ctx.clave_col].isin(ctx.claves_cobro)]
        incidentes = []
        for _, fila in indebidos.iterrows():
            fila_cobro = ctx.cobro[ctx.cobro[ctx.clave_col] == fila[ctx.clave_col]].iloc[0]
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            incidentes.append(incidente_base(
                ctx, fila, "Excluido en Zoho pero sigue apareciendo en el cobro",
                valor_cobro=fila_cobro["valor_cobro"], valor_iva_cobro=fila_cobro["valor_iva_cobro"],
                valor_total_cobro=fila_cobro["valor_total_cobro"], diferencia_valor=fila_cobro["valor_cobro"],
                direccion="Cobro indebido", reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion="El asegurador deberia haber retirado este registro del cobro.",
            ))
        return incidentes


class HuerfanoEnCobroRule:
    """Registro en el cobro que no existe en la relacion de asegurados de Zoho."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        huerfanas = ctx.cobro[~ctx.cobro[ctx.clave_col].isin(ctx.claves_relacion)]
        incidentes = []
        for _, fila in huerfanas.iterrows():
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            creada = "Si" if fila["documento"] in ctx.personas else "No"
            incidentes.append(incidente_base(
                ctx, fila, "Registro en el cobro sin registro en la relacion de asegurados de Zoho",
                estado_zoho="No existe", valor_zoho=None,
                reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion=("Posible novedad que no quedo reflejada en la relacion de asegurados."
                             f" Persona ya creada en Zoho: {creada}."),
            ))
        return incidentes
