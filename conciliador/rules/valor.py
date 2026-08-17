"""Estrategias de comparacion de valor (patron Strategy): ambas implementan
IncidentRule, asi que el motor las trata igual que cualquier otra regla --
elegir una u otra es una linea en el RamoConfig del ramo, no un if/else
dentro del motor."""

from __future__ import annotations

from conciliador.domain.models import Incidente
from conciliador.rules.base import RuleContext, buscar_novedad, incidente_base


class ComparacionExactaRule:
    """Cualquier diferencia >= `umbral_pesos` entre el valor de Zoho y el del
    cobro se reporta; por debajo del umbral se asume redondeo/ruido y no es
    incidente. Sirve para ramos donde el valor deberia ser identico mes a mes
    salvo error (Movilidad, Salud, VG Voluntario).

    `umbral_pesos` es mutable a proposito (no solo un parametro de __init__):
    el backend Django (`conciliacion.services.processor`) lo sobreescribe en
    cada corrida desde `settings.CONCILIACION_UMBRAL_VALOR_EXACTO`, para que
    el umbral se pueda ajustar por configuracion sin tocar codigo. El mismo
    patron ya lo usa `cli.py` con `ComparacionEstadisticaRule.zscore_umbral`."""

    def __init__(self, umbral_pesos: float = 10.0):
        self.umbral_pesos = umbral_pesos

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        comunes = ctx.relacion[ctx.relacion["esperado"] & ctx.relacion[ctx.clave_col].isin(ctx.claves_cobro)]
        incidentes = []
        for _, fila in comunes.iterrows():
            fila_cobro = ctx.cobro[ctx.cobro[ctx.clave_col] == fila[ctx.clave_col]].iloc[0]
            diferencia = round(fila_cobro["valor_cobro"] - fila["valor_zoho"], 2)
            if abs(diferencia) < self.umbral_pesos:
                continue
            direccion = "Subió" if diferencia > 0 else "Bajó"
            reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
            incidentes.append(incidente_base(
                ctx, fila, "Diferencia de valor cobrado vs. valor en Zoho",
                valor_cobro=fila_cobro["valor_cobro"], valor_iva_cobro=fila_cobro["valor_iva_cobro"],
                valor_total_cobro=fila_cobro["valor_total_cobro"], diferencia_valor=diferencia,
                direccion=direccion, reportado_en_novedades=reportado, detalle_novedad=detalle,
                observacion=f"El valor cobrado {direccion.lower()} respecto al valor registrado en Zoho.",
            ))
        return incidentes


class ComparacionEstadisticaRule:
    """En vez de un umbral fijo en pesos, calcula la variacion %% (cobro vs.
    Zoho) de TODOS los registros activos del mes, saca el promedio y la
    desviacion estandar de esa variacion, y solo reporta los registros cuya
    variacion cae fuera de promedio +/- `zscore_umbral` desviaciones.

    Pensado para ramos donde el valor cambia mes a mes de forma esperada
    (VG Deudores: la prima baja con el saldo del credito) y un umbral fijo
    en pesos o en %% no tiene sentido -- el "rango normal" se calcula con
    los datos del propio mes en vez de inventarse un numero."""

    def __init__(self, zscore_umbral: float = 3.0, minimo_muestra: int = 10):
        self.zscore_umbral = zscore_umbral
        self.minimo_muestra = minimo_muestra

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        comunes = ctx.relacion[ctx.relacion["esperado"] & ctx.relacion[ctx.clave_col].isin(ctx.claves_cobro)]
        merged = comunes.merge(
            ctx.cobro[[ctx.clave_col, "valor_cobro", "valor_iva_cobro", "valor_total_cobro"]],
            on=ctx.clave_col, how="left",
        )

        con_base = merged[merged["valor_zoho"] > 0].copy()
        con_base["pct"] = (con_base["valor_cobro"] - con_base["valor_zoho"]) / con_base["valor_zoho"] * 100
        rango = self._rango_esperado(con_base["pct"])

        incidentes: list[Incidente] = []
        for _, fila in merged.iterrows():
            diferencia = round(fila["valor_cobro"] - fila["valor_zoho"], 2)
            if fila["valor_zoho"] <= 0:
                incidentes.extend(self._incidente_valor_zoho_cero(ctx, fila, diferencia))
                continue
            if rango is None:
                continue
            incidente = self._incidente_fuera_de_rango(ctx, fila, diferencia, rango, len(con_base))
            if incidente is not None:
                incidentes.append(incidente)
        return incidentes

    def _rango_esperado(self, variaciones) -> tuple[float, float, float] | None:
        if len(variaciones) < self.minimo_muestra:
            return None
        promedio, desviacion = variaciones.mean(), variaciones.std()
        return promedio - self.zscore_umbral * desviacion, promedio + self.zscore_umbral * desviacion, promedio

    def _incidente_valor_zoho_cero(self, ctx: RuleContext, fila, diferencia: float) -> list[Incidente]:
        if abs(diferencia) < 1:
            return []
        reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
        return [incidente_base(
            ctx, fila, "Diferencia de valor cobrado vs. valor en Zoho (valor en Zoho es $0)",
            valor_cobro=fila["valor_cobro"], valor_iva_cobro=fila["valor_iva_cobro"],
            valor_total_cobro=fila["valor_total_cobro"], diferencia_valor=diferencia,
            reportado_en_novedades=reportado, detalle_novedad=detalle,
            observacion="El valor en Zoho es $0 pero se esta cobrando; no se puede calcular variacion %, revisar manualmente.",
        )]

    def _incidente_fuera_de_rango(self, ctx: RuleContext, fila, diferencia: float,
                                   rango: tuple[float, float, float], tamano_muestra: int) -> Incidente | None:
        lo, hi, promedio = rango
        pct = round((fila["valor_cobro"] - fila["valor_zoho"]) / fila["valor_zoho"] * 100, 2)
        if lo <= pct <= hi:
            return None
        direccion = "Subió más de lo esperado" if pct > hi else "Bajó más de lo esperado"
        reportado, detalle = buscar_novedad(fila[ctx.clave_col], fila["documento"], ctx)
        return incidente_base(
            ctx, fila, "Variacion de prima fuera del rango esperado del mes",
            valor_cobro=fila["valor_cobro"], valor_iva_cobro=fila["valor_iva_cobro"],
            valor_total_cobro=fila["valor_total_cobro"], diferencia_valor=diferencia,
            variacion_pct=pct, direccion=direccion, reportado_en_novedades=reportado, detalle_novedad=detalle,
            observacion=(f"Variacion de {pct:.1f}% frente al valor en Zoho. El rango esperado este mes es "
                         f"de {lo:.1f}% a {hi:.1f}% (promedio {promedio:.1f}%, calculado sobre {tamano_muestra} "
                         f"registros activos)."),
        )
