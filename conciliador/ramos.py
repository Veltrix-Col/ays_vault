"""Registro declarativo de ramos.

Agregar un ramo nuevo (o uno que reutiliza casi todo de otro, como VG
Patronal reutilizara VG Deudores) es agregar una entrada a `RAMOS`, no
escribir un script de 150 lineas copiado de otro.
"""

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
    ExcluidoIndebidoEnCobroRule,
    HuerfanoEnCobroRule,
)
from conciliador.rules.recibo import ReciboConciliacionRule
from conciliador.rules.valor import ComparacionExactaRule
from conciliador.sources.movilidad import cargar_cobro_movilidad
from conciliador.sources.salud import cargar_cobro_salud, periodo_desde_porchat
from conciliador.sources.vg import (
    cargar_cobro_vg,
    cargar_novedades_cliente_deudor,
    cargar_relacion_vg,
    cargar_relacion_vg_api,
    datos_extra_vg,
)
from conciliador.sources.zoho import cargar_novedades_vacio, cargar_novedades_zoho, cargar_personas_zoho, cargar_relacion_zoho
from conciliador.sources.zoho_api import cargar_novedades_api, cargar_personas_api, cargar_relacion_api

_REGLAS_PRESENCIA = [
    DatoIncompletoExcluidoConCobroRule(),
    ActivoAusenteEnCobroRule(),
    ExcluidoIndebidoEnCobroRule(),
    HuerfanoEnCobroRule(),
]


def _novedades_zoho_o_vacio(ruta) -> pd.DataFrame:
    return cargar_novedades_zoho(ruta) if ruta else cargar_novedades_vacio()


def _novedades_cliente_o_vacio(ruta) -> pd.DataFrame:
    return cargar_novedades_cliente_deudor(ruta) if ruta else cargar_novedades_vacio()


RAMOS: dict[str, RamoConfig] = {
    "movilidad": RamoConfig(
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
    "salud": RamoConfig(
        codigo="salud",
        nombre="Salud",
        clave_col="documento",
        reglas=[*_REGLAS_PRESENCIA, ComparacionExactaRule(), IngresoNuevoSinPersonaRule(),
                ReciboConciliacionRule()],
        cargar_relacion=cargar_relacion_zoho,
        cargar_cobro=cargar_cobro_salud,
        cargar_personas=cargar_personas_zoho,
        cargar_novedades=_novedades_zoho_o_vacio,
        inferir_periodo=periodo_desde_porchat,  # el periodo real viene del contenido del Porchat, no del nombre del archivo
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
    "vg_voluntario": RamoConfig(
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
    "vg_deudores": RamoConfig(
        codigo="vg_deudores",
        nombre="VG Deudores",
        clave_col="clave",
        # ComparacionEstadisticaRule (variacion % de valor vs. Zoho) desactivada
        # temporalmente: por ahora genera demasiado ruido para ser accionable.
        reglas=[*_REGLAS_PRESENCIA, IngresoNuevoSinPersonaRule(), SumaCoberturasRule(),
                NovedadContradictoriaRule(), IdentidadInconsistenteRule(), ReciboConciliacionRule()],
        cargar_relacion=cargar_relacion_vg,
        cargar_cobro=cargar_cobro_vg,
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
}


def obtener_ramo(codigo: str) -> RamoConfig:
    try:
        return RAMOS[codigo]
    except KeyError:
        disponibles = ", ".join(sorted(RAMOS))
        raise RamoNoRegistradoError(f"Ramo '{codigo}' no registrado. Disponibles: {disponibles}") from None


__all__ = ["RAMOS", "ModoValor", "obtener_ramo"]
