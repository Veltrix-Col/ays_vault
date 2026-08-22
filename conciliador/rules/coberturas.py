"""Regla propia de VG: valida que el desglose de coberturas de AVA sea
consistente internamente (suma de primas por cobertura + IVA == PRIMA
ASEGURADO). No compara contra Zoho, es un chequeo de calidad del archivo
de la aseguradora."""

from __future__ import annotations

from conciliador.domain.models import Incidente
from conciliador.parsing.normalizadores import normalize_doc
from conciliador.rules.base import RuleContext
from conciliador.sources.vg import validar_suma_coberturas


class SumaCoberturasRule:
    def generar(self, ctx: RuleContext) -> list[Incidente]:
        desglose = ctx.datos_extra.get("desglose_coberturas")
        if desglose is None:
            return []
        anomalas = validar_suma_coberturas(desglose)
        incidentes = []
        for _, fila in anomalas.iterrows():
            direccion = "Suma coberturas > Prima" if fila["_diferencia"] < 0 else "Suma coberturas < Prima"
            incidentes.append(Incidente(
                fecha_reporte=ctx.hoy, ramo=ctx.ramo, periodo=ctx.periodo,
                tipo_incidente="Desglose de coberturas: la suma de primas por cobertura no cuadra con PRIMA ASEGURADO",
                documento=normalize_doc(fila["ID ASEGURADO"]), nombre=fila["NOMBRE DEL ASEGURADO"],
                parentesco=fila.get("PARENTESCO", ""),
                valor_cobro=fila["_suma_coberturas"], valor_iva_cobro=fila.get("VALOR IVA"),
                valor_total_cobro=fila["PRIMA ASEGURADO"], diferencia_valor=fila["_diferencia"],
                direccion=direccion,
                observacion="Revisar el desglose de coberturas de este asegurado con la aseguradora.",
            ))
        return incidentes
