"""Regla de conciliacion del recibo (PDF) extraido por Content Understanding.

Corre dos conciliaciones sobre el `ReciboExtraido` que el source de Content
Understanding deja en `ctx.datos_extra["recibo_cu"]` (mismo canal por el que VG
pasa su desglose de coberturas):

  1. valor_total del recibo == suma de valores de listado_riesgos
     (consistencia interna del PDF). Se ENTREGA SIEMPRE el resultado como fila
     del reporte (informativa si cuadra, incidente si no).

  2. valor_total del recibo == suma de valor_total_cobro del archivo de cobro
     (la "relacion de asegurados de la compañia"). Solo se levanta incidente
     si NO cuadra.

La regla no habla con Azure: opera sobre el DTO plano, asi que se prueba con un
`ReciboExtraido` sintetico. Si el recibo no esta disponible (`recibo_cu` None,
p. ej. porque el ramo no tiene PDF o falta la credencial), emite un unico
incidente 'N/D' -- consistente con la filosofia de `buscar_novedad`, que
distingue "no verificado" de "verificado y sin diferencia".
"""

from __future__ import annotations

from conciliador.domain.models import Incidente
from conciliador.sources.content_understanding import ReciboExtraido

_TIPO_RIESGOS = "Conciliación recibo (PDF): valor_total vs suma de riesgos"
_TIPO_COBRO = "Conciliación recibo (PDF): valor_total vs suma del cobro de la compañía"
_TIPO_ND = "Validación de recibo (PDF) no realizada"


class ReciboConciliacionRule:
    """Valida el recibo (PDF) de la aseguradora contra si mismo y contra el cobro."""

    def __init__(self, umbral_pesos: float = 1.0):
        self.umbral_pesos = umbral_pesos

    def generar(self, ctx) -> list[Incidente]:
        recibo = ctx.datos_extra.get("recibo_cu")
        if not isinstance(recibo, ReciboExtraido):
            return [self._incidente_nd(ctx)]

        incidentes = [self._conciliar_riesgos(ctx, recibo)]
        incidente_cobro = self._conciliar_cobro(ctx, recibo)
        if incidente_cobro is not None:
            incidentes.append(incidente_cobro)
        return incidentes

    # -- Validacion 1: valor_total vs suma de riesgos (siempre se reporta) ----
    def _conciliar_riesgos(self, ctx, recibo: ReciboExtraido) -> Incidente:
        suma = recibo.suma_riesgos
        diferencia = round(recibo.valor_total - suma, 2)
        cuadra = abs(diferencia) < self.umbral_pesos
        estado = "Cuadra" if cuadra else "NO cuadra"
        return self._incidente(
            ctx, _TIPO_RIESGOS, recibo, suma_comparada=suma, diferencia=diferencia,
            observacion=(
                f"Póliza {recibo.poliza or 's/n'}: valor_total del recibo "
                f"${recibo.valor_total:,.2f} vs suma de {len(recibo.riesgos)} riesgos "
                f"${suma:,.2f} (diferencia ${diferencia:,.2f}). {estado}."
            ),
        )

    # -- Validacion 2: valor_total vs suma del cobro (solo si no cuadra) ------
    def _conciliar_cobro(self, ctx, recibo: ReciboExtraido) -> Incidente | None:
        if "valor_total_cobro" not in ctx.cobro.columns:
            return None
        suma = round(float(ctx.cobro["valor_total_cobro"].fillna(0).sum()), 2)
        diferencia = round(recibo.valor_total - suma, 2)
        if abs(diferencia) < self.umbral_pesos:
            return None
        return self._incidente(
            ctx, _TIPO_COBRO, recibo, suma_comparada=suma, diferencia=diferencia,
            observacion=(
                f"Póliza {recibo.poliza or 's/n'}: valor_total del recibo "
                f"${recibo.valor_total:,.2f} vs suma del cobro de la compañía "
                f"${suma:,.2f} (diferencia ${diferencia:,.2f}). NO cuadra."
            ),
        )

    # -- Recibo no disponible ------------------------------------------------
    def _incidente_nd(self, ctx) -> Incidente:
        return Incidente(
            fecha_reporte=ctx.hoy, ramo=ctx.ramo, periodo=ctx.periodo,
            tipo_incidente=_TIPO_ND,
            observacion=("No se pudo validar el recibo (PDF): archivo no disponible o "
                         "servicio de extracción sin configurar para este ramo."),
        )

    def _incidente(self, ctx, tipo: str, recibo: ReciboExtraido, *,
                   suma_comparada: float, diferencia: float, observacion: str) -> Incidente:
        return Incidente(
            fecha_reporte=ctx.hoy, ramo=ctx.ramo, periodo=ctx.periodo,
            tipo_incidente=tipo,
            valor_cobro=suma_comparada,
            valor_total_cobro=recibo.valor_total,
            diferencia_valor=diferencia,
            observacion=observacion,
        )
