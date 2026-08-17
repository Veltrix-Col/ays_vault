"""Reglas de duplicidad e inconsistencia de identidad.

Especialmente relevantes en Vida Grupo, donde la llave de cruce es compuesta
(afiliado + asegurado + subriesgo, ver `conciliador.sources.vg`): una misma
persona puede tener legítimamente varias filas (una por subriesgo), lo que
hace mas facil que pasen desapercibidos dos problemas que las reglas de
presencia/valor (comparan registro a registro por llave completa) no
detectan como tales:

  1. El mismo documento aparece en el reporte de novedades con una novedad
     de exclusion Y una de ingreso: puede ser que en realidad correspondan a
     subriesgos distintos mal reportados, o un error de captura -- en
     cualquier caso, alguien debe revisarlo antes de asumir cual novedad
     vale (`NovedadContradictoriaRule`).

  2. Para el mismo afiliado y subriesgo, el cobro de la aseguradora trae un
     documento de asegurado distinto al que tiene Zoho (p. ej. el afiliado
     aparece duplicado como su propio asegurado en un subriesgo que en Zoho
     es de otro integrante del grupo familiar). Las reglas de presencia si
     detectan las dos mitades de esto (un "huerfano" en el cobro y un
     "ausente" en Zoho), pero como incidentes separados que no dejan claro
     que se trata de la misma casilla afiliado+subriesgo con una identidad
     distinta a cada lado (`IdentidadInconsistenteRule`).
"""

from __future__ import annotations

import pandas as pd

from conciliador.domain.models import Incidente
from conciliador.parsing.normalizadores import strip_accents
from conciliador.rules.base import RuleContext, incidente_base

_PATRONES_EXCLUSION = ("exclu", "retir", "baja", "cancel")
_PATRONES_INGRESO = ("ingres", "alta", "afiliacion", "inclusion")


def _clasificar_novedad(estado: str) -> str | None:
    normalizado = strip_accents(str(estado)).lower()
    if any(patron in normalizado for patron in _PATRONES_EXCLUSION):
        return "exclusion"
    if any(patron in normalizado for patron in _PATRONES_INGRESO):
        return "ingreso"
    return None


def _detalle_novedades(grupo: pd.DataFrame) -> str:
    detalles = []
    for _, fila in grupo.iterrows():
        fecha = fila["fecha_novedad"].date() if pd.notna(fila["fecha_novedad"]) else "s/f"
        detalles.append(f"{fila['estado_novedad']} ({fecha})")
    return "; ".join(detalles)


class NovedadContradictoriaRule:
    """Documento con novedad de exclusion Y de ingreso en el mismo reporte."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        novedades = ctx.novedades
        if novedades.empty:
            return []
        incidentes = []
        for documento, grupo in novedades.groupby("documento"):
            if not documento or len(grupo) < 2:
                continue
            categorias = {c for c in grupo["estado_novedad"].apply(_clasificar_novedad) if c}
            if not {"exclusion", "ingreso"} <= categorias:
                continue
            fila = grupo.iloc[0]
            incidentes.append(incidente_base(
                ctx, fila, "Novedades contradictorias para el mismo documento (exclusión e ingreso a la vez)",
                reportado_en_novedades="Si", detalle_novedad=_detalle_novedades(grupo),
                observacion=("El documento aparece con novedades de exclusión e ingreso en el mismo reporte. "
                              "Revisar si corresponde a subriesgos distintos mal registrados como si fueran el "
                              "mismo, o a un error de captura en Zoho, antes de asumir cual novedad es la vigente."),
            ))
        return incidentes


class IdentidadInconsistenteRule:
    """Mismo afiliado + subriesgo, documento de asegurado distinto entre el
    cobro de la aseguradora y la relacion vigente de Zoho."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        if "documento_titular" not in ctx.cobro.columns or "documento_titular" not in ctx.relacion.columns:
            return []
        cobro = ctx.cobro[ctx.cobro["documento_titular"] != ""]
        relacion_activa = ctx.relacion[ctx.relacion["esperado"] & (ctx.relacion["documento_titular"] != "")]
        if cobro.empty or relacion_activa.empty:
            return []

        casillas_cobro = cobro.groupby(["documento_titular", "subriesgo"])["documento"].apply(set)
        casillas_zoho = relacion_activa.groupby(["documento_titular", "subriesgo"])["documento"].apply(set)

        incidentes = []
        for llave in casillas_cobro.index.intersection(casillas_zoho.index):
            documentos_cobro = casillas_cobro.loc[llave]
            documentos_zoho = casillas_zoho.loc[llave]
            if documentos_cobro == documentos_zoho:
                continue
            afiliado, subriesgo = llave
            fila = relacion_activa[
                (relacion_activa["documento_titular"] == afiliado) & (relacion_activa["subriesgo"] == subriesgo)
            ].iloc[0]
            incidentes.append(incidente_base(
                ctx, fila,
                "Posible identidad de asegurado inconsistente entre el cobro y Zoho para el mismo afiliado y subriesgo",
                estado_zoho=f"Zoho: {', '.join(sorted(documentos_zoho))}",
                observacion=(f"Para el afiliado {afiliado} (subriesgo {subriesgo}), el cobro reporta el/los "
                              f"documento(s) {', '.join(sorted(documentos_cobro))}, pero en Zoho el asegurado de esa "
                              f"casilla es {', '.join(sorted(documentos_zoho))}. Revisar si es un error de identidad "
                              "(p. ej. el afiliado duplicado como su propio asegurado) antes de tratarlo solo como "
                              "un ingreso o una exclusión."),
            ))
        return incidentes
