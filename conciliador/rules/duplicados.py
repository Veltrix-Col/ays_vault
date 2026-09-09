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
     documento de asegurado distinto al que tiene Zoho (caso A: p. ej. el
     afiliado aparece duplicado como su propio asegurado en un subriesgo que
     en Zoho es de otro integrante del grupo familiar); o el mismo asegurado
     aparece con un subriesgo distinto a cada lado (caso B: p. ej. paso de un
     subriesgo a otro y solo un lado quedo actualizado). Las reglas de
     presencia si detectan las dos mitades de esto (un "huerfano" en el
     cobro y un "ausente" en Zoho), pero como incidentes separados que no
     dejan claro que se trata de la misma persona con una identidad o un
     subriesgo distinto a cada lado (`IdentidadInconsistenteRule`).

`detectar_inconsistencias_identidad()` es el punto de entrada unico para
ambos casos: lo usa `IdentidadInconsistenteRule` para generar el incidente
consolidado, y lo usan tambien `HuerfanoEnCobroRule`/`ActivoAusenteEnCobroRule`
(en `conciliador.rules.presencia`) para excluir esas mismas filas y no
reportar el mismo problema 2-3 veces. Si `IdentidadInconsistenteRule` se
llegara a quitar de `RamoConfig.reglas` de un ramo, esas filas dejarian de
reportarse en cualquier lado -- mantener ambos usos en sincronia.
"""

from __future__ import annotations

import pandas as pd

from conciliador.domain.models import Incidente
from conciliador.parsing.normalizadores import strip_accents
from conciliador.rules.base import RuleContext, incidente_base

_PATRONES_EXCLUSION = ("exclu", "retir", "baja", "cancel")
_PATRONES_INGRESO = ("ingres", "alta", "afiliacion", "inclusion")

_COLUMNAS_REQUERIDAS = ("documento_titular", "subriesgo")


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


def _tiene_columnas(*dataframes: pd.DataFrame) -> bool:
    return all(columna in df.columns for df in dataframes for columna in _COLUMNAS_REQUERIDAS)


def _casilla_documento_distinto(
    ctx: RuleContext, cobro: pd.DataFrame, relacion: pd.DataFrame,
) -> tuple[list[Incidente], set[str], set[str]]:
    """Caso A: mismo afiliado+subriesgo, documento de asegurado distinto entre
    el cobro de la aseguradora y la relacion vigente de Zoho."""
    if cobro.empty or relacion.empty:
        return [], set(), set()

    casillas_cobro = cobro.groupby(["documento_titular", "subriesgo"])["documento"].apply(set)
    casillas_zoho = relacion.groupby(["documento_titular", "subriesgo"])["documento"].apply(set)

    incidentes: list[Incidente] = []
    claves_cobro_excl: set[str] = set()
    claves_relacion_excl: set[str] = set()
    for llave in casillas_cobro.index.intersection(casillas_zoho.index):
        documentos_cobro = casillas_cobro.loc[llave]
        documentos_zoho = casillas_zoho.loc[llave]
        if documentos_cobro == documentos_zoho:
            continue
        afiliado, subriesgo = llave
        filas_cobro = cobro[(cobro["documento_titular"] == afiliado) & (cobro["subriesgo"] == subriesgo)]
        filas_relacion = relacion[(relacion["documento_titular"] == afiliado) & (relacion["subriesgo"] == subriesgo)]
        fila = filas_relacion.iloc[0]
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
        claves_cobro_excl |= set(filas_cobro["clave"])
        claves_relacion_excl |= set(filas_relacion["clave"])
    return incidentes, claves_cobro_excl, claves_relacion_excl


def _subriesgo_distinto_mismo_asegurado(
    ctx: RuleContext, cobro: pd.DataFrame, relacion: pd.DataFrame,
) -> tuple[list[Incidente], set[str], set[str]]:
    """Caso B: mismo afiliado+asegurado, subriesgo distinto entre el cobro y
    Zoho -- p. ej. el asegurado paso de un subriesgo a otro y solo un lado
    quedo actualizado. Solo mira filas que de otro modo `HuerfanoEnCobroRule`/
    `ActivoAusenteEnCobroRule` reportarian por separado (si el asegurado tiene
    el mismo subriesgo en ambos lados no hay nada que consolidar aqui)."""
    if cobro.empty or relacion.empty:
        return [], set(), set()

    huerfanas = cobro[~cobro["clave"].isin(set(relacion["clave"]))]
    faltantes = relacion[~relacion["clave"].isin(set(cobro["clave"]))]
    if huerfanas.empty or faltantes.empty:
        return [], set(), set()

    subriesgos_cobro = huerfanas.groupby(["documento_titular", "documento"])["subriesgo"].apply(set)
    subriesgos_zoho = faltantes.groupby(["documento_titular", "documento"])["subriesgo"].apply(set)

    incidentes: list[Incidente] = []
    claves_cobro_excl: set[str] = set()
    claves_relacion_excl: set[str] = set()
    for llave in subriesgos_cobro.index.intersection(subriesgos_zoho.index):
        afiliado, documento = llave
        solo_cobro = subriesgos_cobro.loc[llave]
        solo_zoho = subriesgos_zoho.loc[llave]
        filas_cobro = huerfanas[(huerfanas["documento_titular"] == afiliado) & (huerfanas["documento"] == documento)]
        filas_relacion = faltantes[(faltantes["documento_titular"] == afiliado) & (faltantes["documento"] == documento)]
        fila = filas_relacion.iloc[0]
        incidentes.append(incidente_base(
            ctx, fila,
            "Posible subriesgo mal reportado: mismo afiliado y asegurado con subriesgo distinto entre el cobro y Zoho",
            observacion=(
                f"Para el afiliado {afiliado}, el asegurado {documento} aparece en el cobro con el/los "
                f"subriesgo(s) {', '.join(sorted(solo_cobro))}, que no tiene registrados en Zoho, mientras que en "
                f"Zoho tiene el/los subriesgo(s) {', '.join(sorted(solo_zoho))} que no aparecen en el cobro. "
                "Revisar si es el mismo riesgo con un subriesgo mal digitado antes de tratarlo como un ingreso y "
                "una ausencia por separado."
            ),
        ))
        claves_cobro_excl |= set(filas_cobro["clave"])
        claves_relacion_excl |= set(filas_relacion["clave"])
    return incidentes, claves_cobro_excl, claves_relacion_excl


def detectar_inconsistencias_identidad(ctx: RuleContext) -> tuple[list[Incidente], set[str], set[str]]:
    """Devuelve (incidentes, claves_cobro_a_excluir, claves_relacion_a_excluir).

    `[]`, `set()`, `set()` si el ramo no trae las columnas de VG
    (`documento_titular`/`subriesgo`) -- movilidad y salud no tienen este
    concepto y quedan intactos."""
    if not _tiene_columnas(ctx.cobro, ctx.relacion):
        return [], set(), set()

    cobro = ctx.cobro[ctx.cobro["documento_titular"] != ""]
    relacion = ctx.relacion[ctx.relacion["esperado"] & (ctx.relacion["documento_titular"] != "")]

    incidentes_a, claves_c_a, claves_r_a = _casilla_documento_distinto(ctx, cobro, relacion)
    incidentes_b, claves_c_b, claves_r_b = _subriesgo_distinto_mismo_asegurado(ctx, cobro, relacion)

    return incidentes_a + incidentes_b, claves_c_a | claves_c_b, claves_r_a | claves_r_b


class IdentidadInconsistenteRule:
    """Mismo afiliado + subriesgo con documento de asegurado distinto (caso A),
    o mismo afiliado + asegurado con subriesgo distinto (caso B) entre el
    cobro de la aseguradora y la relacion vigente de Zoho. Ver
    `detectar_inconsistencias_identidad` para el detalle de ambos casos y para
    como se excluyen estas mismas filas de `HuerfanoEnCobroRule`/
    `ActivoAusenteEnCobroRule`."""

    def generar(self, ctx: RuleContext) -> list[Incidente]:
        incidentes, _, _ = detectar_inconsistencias_identidad(ctx)
        return incidentes
