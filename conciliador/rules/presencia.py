"""Reglas sobre presencia/ausencia de registros entre Zoho y el cobro: activos
que no aparecen, excluidos que siguen apareciendo, registros del cobro sin
relacion, y 'excluido con cobro' que ya paso su mes de gracia."""

from __future__ import annotations

from conciliador.domain.models import Incidente
from conciliador.parsing.normalizadores import strip_accents
from conciliador.rules.base import RuleContext, buscar_novedad, incidente_base
from conciliador.rules.duplicados import detectar_inconsistencias_identidad


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


class ExcluidoConCobroSinActualizarRule:
    """'Excluido con cobro' cuyo mes de gracia (fecha_retiro) ya paso y que
    ya no aparece en el cobro del asegurador.

    Esto es lo esperado -- el asegurador ya dejo de cobrarlo, ver
    `conciliador.engine.esperado_en_cobro` -- pero significa que la baja ya
    quedo confirmada y el estado en Zoho deberia actualizarse de 'Excluido
    con cobro' a 'Excluido' llano. Sin esta regla, `ActivoAusenteEnCobroRule`
    no lo reporta (no es 'esperado' fuera del mes de gracia) y el registro
    queda colgado en 'Excluido con cobro' sin que nadie lo note."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        es_excluido_con_cobro = ctx.relacion["estado_asegurado"].apply(
            lambda e: strip_accents(e).lower().startswith("excluido con cobro")
        )
        vencidos = ctx.relacion[
            es_excluido_con_cobro & ctx.relacion["fecha_retiro"].notna() & ~ctx.relacion["esperado"]
            & ~ctx.relacion[ctx.clave_col].isin(ctx.claves_cobro)
        ]
        incidentes = []
        for _, fila in vencidos.iterrows():
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            incidentes.append(incidente_base(
                ctx, fila, "Excluido con cobro que ya no aparece en el cobro: actualizar estado a Excluido en Zoho",
                reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion=("El asegurador ya dejo de cobrar este registro (su mes de gracia como 'Excluido con "
                              "cobro' ya paso). El estado en Zoho deberia actualizarse a 'Excluido'."),
            ))
        return incidentes


class ActivoAusenteEnCobroRule:
    """Activo (o excluido-con-cobro vigente) en Zoho que no aparece en el cobro."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        _, _, claves_relacion_excl = detectar_inconsistencias_identidad(ctx)
        faltantes = ctx.relacion[
            ctx.relacion["esperado"]
            & ~ctx.relacion[ctx.clave_col].isin(ctx.claves_cobro)
            & ~ctx.relacion[ctx.clave_col].isin(claves_relacion_excl)
        ]
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
        _, claves_cobro_excl, _ = detectar_inconsistencias_identidad(ctx)
        huerfanas = ctx.cobro[
            ~ctx.cobro[ctx.clave_col].isin(ctx.claves_relacion)
            & ~ctx.cobro[ctx.clave_col].isin(claves_cobro_excl)
        ]
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
