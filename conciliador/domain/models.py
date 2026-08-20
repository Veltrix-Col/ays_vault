"""Modelos de dominio: los tipos que viajan entre capas.

`domain` no depende de `sources`, `rules`, `engine` ni de pandas mas alla de
para construir un DataFrame de salida: es la capa mas estable del paquete,
la que un backend (Django u otro) deberia poder importar sin arrastrar el
resto de la logica de conciliacion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Callable

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Iterable
    from os import PathLike

    from ays_zoho_sdk import ZohoFacade

    from conciliador.rules.base import IncidentRule

# Orden y nombres de columna del reporte final (se mantiene identico al que
# ya usan los reportes de Excel entregados, para no romper lo que el equipo
# ya esta acostumbrado a leer).
REPORTE_COLUMNAS = [
    "Fecha_Reporte", "Ramo", "Periodo", "Tipo_Incidente", "Placa", "Documento", "Nombre",
    "Titular", "Parentesco", "Estado_Zoho", "Valor_Zoho", "Valor_Cobro", "Valor_IVA_Cobro",
    "Valor_Total_Cobro", "Diferencia_Valor", "Variacion_Pct", "Direccion", "Reportado_en_Novedades",
    "Detalle_Novedad", "Observacion",
]


class ModoValor(str, Enum):
    """Como se compara el valor cobrado contra el valor en Zoho.

    Reemplaza el antiguo parametro `modo_valor: str` (propenso a typos
    silenciosos): con un Enum, pasar un valor invalido falla de inmediato
    en vez de caer calladamente en la rama por defecto.
    """

    EXACTO = "exacto"
    ESTADISTICO = "estadistico"


@dataclass(frozen=True, slots=True)
class Incidente:
    """Una fila del reporte de conciliacion."""

    fecha_reporte: date
    ramo: str
    periodo: str
    tipo_incidente: str
    placa: str = ""
    documento: str = ""
    nombre: str = ""
    titular: str = ""
    parentesco: str = ""
    estado_zoho: str = ""
    valor_zoho: float | None = None
    valor_cobro: float | None = None
    valor_iva_cobro: float | None = None
    valor_total_cobro: float | None = None
    diferencia_valor: float | None = None
    variacion_pct: float | None = None
    direccion: str = ""
    reportado_en_novedades: str = ""
    detalle_novedad: str = ""
    observacion: str = ""
    # True (default) => cuenta como incidente real: bloquea "sin incidentes" y,
    # por lo tanto, el acceso a los cobros/enlace de facturación en Zoho.
    # False => fila informativa/advertencia (p. ej. ReciboConciliacionRule,
    # que por ahora es solo advertencia y nunca debe impedir conciliar en
    # Zoho): siempre se incluye en el Excel, pero no cuenta para el bloqueo.
    bloqueante: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "Fecha_Reporte": self.fecha_reporte, "Ramo": self.ramo, "Periodo": self.periodo,
            "Tipo_Incidente": self.tipo_incidente, "Placa": self.placa, "Documento": self.documento,
            "Nombre": self.nombre, "Titular": self.titular, "Parentesco": self.parentesco,
            "Estado_Zoho": self.estado_zoho, "Valor_Zoho": self.valor_zoho, "Valor_Cobro": self.valor_cobro,
            "Valor_IVA_Cobro": self.valor_iva_cobro, "Valor_Total_Cobro": self.valor_total_cobro,
            "Diferencia_Valor": self.diferencia_valor, "Variacion_Pct": self.variacion_pct,
            "Direccion": self.direccion, "Reportado_en_Novedades": self.reportado_en_novedades,
            "Detalle_Novedad": self.detalle_novedad, "Observacion": self.observacion,
        }


@dataclass(frozen=True, slots=True)
class ReporteConciliacion:
    """Resultado completo de conciliar un ramo para un periodo."""

    ramo: str
    periodo: str
    generado_en: date
    incidentes: list[Incidente]

    @property
    def incidentes_bloqueantes(self) -> list[Incidente]:
        return [i for i in self.incidentes if i.bloqueante]

    @property
    def incidentes_informativos(self) -> list[Incidente]:
        return [i for i in self.incidentes if not i.bloqueante]

    @property
    def total_incidentes(self) -> int:
        """Solo incidentes bloqueantes; las advertencias (p. ej. recibo/PDF)
        no cuentan aquí, aunque siguen apareciendo en el Excel."""
        return len(self.incidentes_bloqueantes)

    @property
    def total_advertencias(self) -> int:
        return len(self.incidentes_informativos)

    @property
    def esta_vacio(self) -> bool:
        """Sin incidentes bloqueantes: habilita el acceso a los cobros/enlace
        de facturación en Zoho, aunque haya advertencias informativas."""
        return not self.incidentes_bloqueantes

    def to_dataframe(self) -> pd.DataFrame:
        # Se conservan TODAS las filas (bloqueantes e informativas) en el
        # Excel: es el registro de auditoría completo.
        filas = [i.to_dict() for i in self.incidentes]
        return pd.DataFrame(filas, columns=REPORTE_COLUMNAS)

    def resumen_por_tipo(self) -> dict[str, int]:
        if not self.incidentes:
            return {}
        return self.to_dataframe()["Tipo_Incidente"].value_counts().to_dict()


@dataclass(frozen=True)
class RamoConfig:
    """Configuracion declarativa de un ramo: que loaders usar, con que llave
    de cruce y con que reglas. Agregar un ramo nuevo (o uno que reutiliza
    piezas de otro, como pasara con VG Patronal) es registrar una instancia
    de esta clase en `conciliador.ramos`, no copiar un script."""

    codigo: str
    nombre: str
    clave_col: str
    reglas: list["IncidentRule"]
    cargar_relacion: Callable[["PathLike | str"], pd.DataFrame]
    cargar_cobro: Callable[["PathLike | str"], pd.DataFrame]
    cargar_personas: Callable[["PathLike | str"], set[str]]
    cargar_novedades: Callable[["PathLike | str | None"], pd.DataFrame]
    inferir_periodo: Callable[["PathLike | str", "int | None", "int | None"], tuple[int, int]]
    patrones_archivo: dict[str, str] = field(default_factory=dict)
    construir_datos_extra: Callable[["PathLike | str"], dict[str, object]] | None = None
    # Servicio de Content Understanding a usar para validar el recibo (PDF):
    # "salud", "vida" o "movilidad". None => el ramo no valida recibo.
    servicio_cu: str | None = None
    # Equivalentes por API de cargar_relacion/cargar_personas (consulta directa a
    # Zoho en vez de leer el Excel exportado). None => el ramo aun no la soporta
    # y ConciliacionService debe exigir los archivos legado para el.
    # cargar_personas_api recibe `documentos` (union de relacion + cobro): busca
    # solo esos documentos en Contacts en vez de traer el modulo completo.
    cargar_relacion_api: Callable[["ZohoFacade", str], pd.DataFrame] | None = None
    cargar_personas_api: Callable[["ZohoFacade", "Iterable[str]"], set[str]] | None = None
    # None => el ramo no tiene equivalente de novedades por API (p.ej. vg_deudores,
    # cuyas novedades vienen del banco, no de Zoho) y ConciliacionService debe usar
    # cargar_novedades (archivo) incluso en modo API.
    cargar_novedades_api: Callable[["ZohoFacade", str], pd.DataFrame] | None = None
