"""Registro declarativo de ramos x compañía.

`RAMOS` esta indexado primero por ramo y luego por compañía aseguradora
(`RAMOS[ramo_codigo][compania_codigo]`): el formato del archivo de cobro lo
define la aseguradora, no el ramo, asi que dos companias del mismo ramo
pueden necesitar loaders de cobro completamente distintos (ver
`conciliador.sources.salud`/`conciliador.sources.vg` para los dos formatos
que ya distingue Sura). La relacion/personas/novedades siguen viniendo
siempre de Zoho (mismo formato sin importar la aseguradora de la poliza), asi
que esos loaders normalmente se comparten entre companias de un mismo ramo.

Hoy todo lo que hay aqui es de Sura (unica compañía configurada). Agregar una
compañía nueva a un ramo existente es agregar una entrada bajo ese ramo con
su propio `cargar_cobro`/`patrones_archivo`/`reglas` (reutilizando lo que de
verdad se comparta, como los loaders de Zoho); agregar un ramo nuevo es
agregar una entrada de nivel superior con al menos una compañía."""

from __future__ import annotations

import pandas as pd

from conciliador.domain.exceptions import RamoNoRegistradoError
from conciliador.domain.models import ModoValor, RamoConfig
from conciliador.parsing.periodo import inferir_periodo_desde_nombre_archivo
from conciliador.rules.coberturas import SumaCoberturasRule
from conciliador.rules.duplicados import IdentidadInconsistenteRule, NovedadContradictoriaRule
from conciliador.rules.ingresos import IngresoNuevoSinPersonaRule
from conciliador.rules.presencia import (
    ActivoAusenteEnCobroRule,
    DatoIncompletoExcluidoConCobroRule,
    ExcluidoConCobroSinActualizarRule,
    ExcluidoIndebidoEnCobroRule,
    HuerfanoEnCobroRule,
)
from conciliador.rules.recibo import ReciboConciliacionRule
from conciliador.rules.valor import ComparacionExactaRule
from conciliador.sources.movilidad import cargar_cobro_movilidad
from conciliador.sources.salud import cargar_cobro_salud, inferir_periodo_salud
from conciliador.sources.vg import (
    cargar_cobro_vg,
    cargar_cobro_vg_deudores,
    cargar_novedades_cliente_deudor,
    cargar_relacion_vg,
    cargar_relacion_vg_api,
    datos_extra_vg,
)
from conciliador.sources.zoho import cargar_novedades_vacio, cargar_novedades_zoho, cargar_personas_zoho, cargar_relacion_zoho
from conciliador.sources.zoho_api import cargar_novedades_api, cargar_personas_api, cargar_relacion_api

_REGLAS_PRESENCIA = [
    DatoIncompletoExcluidoConCobroRule(),
    ExcluidoConCobroSinActualizarRule(),
    ActivoAusenteEnCobroRule(),
    ExcluidoIndebidoEnCobroRule(),
    HuerfanoEnCobroRule(),
]


def _novedades_zoho_o_vacio(ruta) -> pd.DataFrame:
    return cargar_novedades_zoho(ruta) if ruta else cargar_novedades_vacio()


def _novedades_cliente_o_vacio(ruta) -> pd.DataFrame:
    return cargar_novedades_cliente_deudor(ruta) if ruta else cargar_novedades_vacio()


# Compañía por defecto cuando quien llama no indica una (todo lo configurado
# hoy es de Sura): mantiene retrocompatibilidad con el codigo/CLI existente.
COMPANIA_DEFECTO = "sura"

RAMOS: dict[str, dict[str, RamoConfig]] = {
    "movilidad": {
        "sura": RamoConfig(
            codigo="movilidad",
            nombre="Movilidad",
            clave_col="placa",
            reglas=[*_REGLAS_PRESENCIA, ComparacionExactaRule(), IngresoNuevoSinPersonaRule(),
                    ReciboConciliacionRule()],
            cargar_relacion=cargar_relacion_zoho,
            cargar_cobro=cargar_cobro_movilidad,
            cargar_personas=cargar_personas_zoho,
            cargar_novedades=_novedades_zoho_o_vacio,
            inferir_periodo=inferir_periodo_desde_nombre_archivo,
            patrones_archivo={
                "personas": "Personas_Zoho*.xlsx",
                "cobro": "*Sharefile*Cobro*.CSV",
                "relacion": "*Zoho_Asegurados*.xlsx",
                "novedades": "*Novedades*.xlsx",
                "recibo": "*Recibo*Movilidad*.PDF",
            },
            valida_recibo_pdf=True,
            cargar_relacion_api=cargar_relacion_api,
            cargar_personas_api=cargar_personas_api,
            cargar_novedades_api=cargar_novedades_api,
        ),
    },
    "salud": {
        "sura": RamoConfig(
            codigo="salud",
            nombre="Salud",
            clave_col="documento",
            reglas=[*_REGLAS_PRESENCIA, ComparacionExactaRule(), IngresoNuevoSinPersonaRule(),
                    ReciboConciliacionRule()],
            cargar_relacion=cargar_relacion_zoho,
            cargar_cobro=cargar_cobro_salud,
            cargar_personas=cargar_personas_zoho,
            cargar_novedades=_novedades_zoho_o_vacio,
            inferir_periodo=inferir_periodo_salud,  # el periodo real viene del contenido del archivo, no del nombre
            patrones_archivo={
                "personas": "Personas_Zoho*.xlsx",
                "cobro": "*Porchat*.xlsx",
                "relacion": "*Zoho_Salud*.xlsx",
                "novedades": "*Novedades*.xlsx",
                "recibo": "*Recibo*Salud*.PDF",
            },
            valida_recibo_pdf=True,
            cargar_relacion_api=cargar_relacion_api,
            cargar_personas_api=cargar_personas_api,
            cargar_novedades_api=cargar_novedades_api,
        ),
    },
    "vg_voluntario": {
        "sura": RamoConfig(
            codigo="vg_voluntario",
            nombre="VG Voluntario",
            clave_col="clave",
            reglas=[*_REGLAS_PRESENCIA, ComparacionExactaRule(), IngresoNuevoSinPersonaRule(), SumaCoberturasRule(),
                    NovedadContradictoriaRule(), IdentidadInconsistenteRule(), ReciboConciliacionRule()],
            cargar_relacion=cargar_relacion_vg,
            cargar_cobro=cargar_cobro_vg,
            cargar_personas=cargar_personas_zoho,
            cargar_novedades=_novedades_zoho_o_vacio,
            inferir_periodo=inferir_periodo_desde_nombre_archivo,
            patrones_archivo={
                "personas": "Personas_Zoho*.xlsx",
                "cobro": "*AVA*Cobro*.xls*",
                "relacion": "*Zoho_Asegurados*VG*.xlsx",
                "novedades": "*Novedades*VG*.xlsx",
                "recibo": "*Recibo*VG*Voluntario*.PDF",
            },
            construir_datos_extra=datos_extra_vg,
            valida_recibo_pdf=True,
            cargar_relacion_api=cargar_relacion_vg_api,
            cargar_personas_api=cargar_personas_api,
            cargar_novedades_api=cargar_novedades_api,
        ),
    },
    "vg_deudores": {
        "sura": RamoConfig(
            codigo="vg_deudores",
            nombre="VG Deudores",
            clave_col="clave",
            # ComparacionEstadisticaRule (variacion % de valor vs. Zoho) desactivada
            # temporalmente: por ahora genera demasiado ruido para ser accionable.
            reglas=[*_REGLAS_PRESENCIA, IngresoNuevoSinPersonaRule(), SumaCoberturasRule(),
                    NovedadContradictoriaRule(), IdentidadInconsistenteRule(), ReciboConciliacionRule()],
            cargar_relacion=cargar_relacion_vg,
            cargar_cobro=cargar_cobro_vg_deudores,
            cargar_personas=cargar_personas_zoho,
            cargar_novedades=_novedades_cliente_o_vacio,
            inferir_periodo=inferir_periodo_desde_nombre_archivo,
            patrones_archivo={
                "personas": "Personas_Zoho*.xlsx",
                "cobro": "*AVA*Cobro*.xls*",
                "relacion": "*Zoho_Asegurados*Deudor*.xlsx",
                "novedades": "*Novedades*.xlsx",
                "recibo": "*Recibo*VG*Deudor*.PDF",
            },
            construir_datos_extra=datos_extra_vg,
            valida_recibo_pdf=True,
            cargar_relacion_api=cargar_relacion_vg_api,
            cargar_personas_api=cargar_personas_api,
        ),
    },
}


def obtener_ramo(ramo_codigo: str, compania_codigo: str = COMPANIA_DEFECTO) -> RamoConfig:
    companias = RAMOS.get(ramo_codigo)
    if companias is None:
        disponibles = ", ".join(sorted(RAMOS))
        raise RamoNoRegistradoError(f"Ramo '{ramo_codigo}' no registrado. Disponibles: {disponibles}") from None
    try:
        return companias[compania_codigo]
    except KeyError:
        disponibles = ", ".join(sorted(companias))
        raise RamoNoRegistradoError(
            f"El ramo '{ramo_codigo}' no tiene configuracion para la compañía '{compania_codigo}'. "
            f"Compañías disponibles para este ramo: {disponibles}."
        ) from None


def companias_de_ramo(ramo_codigo: str) -> list[str]:
    """Codigos de compañía configurados para un ramo (vacio si el ramo no
    existe). Se deriva de `RAMOS` para que nunca quede desincronizado con lo
    que el motor realmente soporta -- ver `conciliacion.ramos_ui`."""
    return sorted(RAMOS.get(ramo_codigo, {}))


__all__ = ["RAMOS", "COMPANIA_DEFECTO", "ModoValor", "obtener_ramo", "companias_de_ramo"]
