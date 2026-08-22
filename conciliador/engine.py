"""Motor de conciliacion: agnostico de ramo. Solo sabe recorrer una lista de
reglas (`IncidentRule`) sobre un contexto -- toda la logica especifica de
"que es un incidente" vive en `conciliador.rules`, no aqui.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

from conciliador.domain.models import Incidente, ReporteConciliacion
from conciliador.parsing.normalizadores import strip_accents
from conciliador.parsing.periodo import etiqueta_periodo
from conciliador.rules.base import IncidentRule, RuleContext


def esperado_en_cobro(fila, mes: int, anio: int) -> bool:
    """Politica de negocio comun a todos los ramos: cuando un registro de
    la relacion de Zoho deberia aparecer facturado este periodo."""
    estado = strip_accents(fila["estado_asegurado"]).lower()
    if estado == "activo":
        return True
    if estado.startswith("excluido con cobro"):
        fecha_retiro = fila["fecha_retiro"]
        if pd.isna(fecha_retiro):
            return True  # dato incompleto: se asume que sigue facturando; DatoIncompletoExcluidoConCobroRule lo marca aparte
        return fecha_retiro.month == mes and fecha_retiro.year == anio
    return False  # Excluido normal, u otro estado no facturable


def con_columna_esperado(relacion: pd.DataFrame, mes: int, anio: int) -> pd.DataFrame:
    """Devuelve una copia de `relacion` con la columna booleana 'esperado'."""
    relacion = relacion.copy()
    if relacion.empty:
        # relacion.apply(..., axis=1) sobre un DataFrame vacio devuelve un
        # DataFrame (no una Series), lo que rompe la asignacion de columna.
        relacion["esperado"] = pd.Series(dtype=bool)
    else:
        relacion["esperado"] = relacion.apply(lambda fila: esperado_en_cobro(fila, mes, anio), axis=1)
    return relacion


class ReconciliationEngine:
    """Orquesta una lista de reglas sobre un `RuleContext`.

    `reloj` es inyectable para que los tests puedan fijar la fecha del
    reporte sin depender de `datetime.now()` (no determinismo).
    """

    def __init__(self, reglas: list[IncidentRule], reloj: Callable[[], date] = date.today):
        self.reglas = reglas
        self.reloj = reloj

    def ejecutar(
        self,
        *,
        relacion: pd.DataFrame,
        cobro: pd.DataFrame,
        novedades: pd.DataFrame,
        personas: set[str],
        mes: int,
        anio: int,
        ramo: str,
        clave_col: str,
        datos_extra: dict[str, object] | None = None,
        poliza: str | None = None,
    ) -> ReporteConciliacion:
        relacion = con_columna_esperado(relacion, mes, anio)

        contexto = RuleContext(
            relacion=relacion, cobro=cobro, novedades=novedades, personas=personas,
            mes=mes, anio=anio, ramo=ramo, clave_col=clave_col,
            periodo=etiqueta_periodo(mes, anio), hoy=self.reloj(), datos_extra=datos_extra or {},
            poliza=poliza or "",
        )

        incidentes: list[Incidente] = []
        for regla in self.reglas:
            incidentes.extend(regla.generar(contexto))

        return ReporteConciliacion(ramo=ramo, periodo=contexto.periodo, generado_en=contexto.hoy, incidentes=incidentes)
