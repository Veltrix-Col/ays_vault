"""Capa de servicio pensada para vivir como `services/` dentro de una app
Django (mismo patron que ya usan en soat/services/processor.py de AyS_Vault):
una funcion de entrada que recibe archivos, devuelve un resultado
serializable (bytes del Excel + resumen) y traduce cualquier error de
negocio a una excepcion tipo ValueError que una vista puede capturar.

No importa Django: recibe rutas de archivo ya materializadas en disco. La
vista que la use es quien baja el `UploadedFile` a un directorio temporal
(la misma tecnica de `soat/services/processor.py`) y le pasa las rutas aqui.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from conciliador.domain.exceptions import ConciliadorError
from conciliador.domain.models import ReporteConciliacion
from conciliador.engine import ReconciliationEngine
from conciliador.ramos import obtener_ramo
from conciliador.reporting.excel_writer import reporte_a_bytes
from conciliador.sources.content_understanding import analizar_recibo


class ConciliacionServiceError(ValueError):
    """Error de negocio al conciliar. Una vista puede capturar esto y
    devolver un 400 con el mensaje tal cual, sin exponer trazas internas."""


@dataclass(frozen=True)
class ConciliacionArchivos:
    relacion: str | Path
    cobro: str | Path
    personas: str | Path
    novedades: str | Path | None = None
    recibo: str | Path | None = None  # PDF de la aseguradora para validar via Content Understanding


@dataclass(frozen=True)
class ConciliacionResultado:
    reporte: ReporteConciliacion
    contenido_excel: bytes
    nombre_archivo: str
    resumen: dict[str, int]


class ConciliacionService:
    """Punto de entrada unico: `ejecutar(ramo, archivos)` -> `ConciliacionResultado`."""

    def ejecutar(
        self, ramo_codigo: str, archivos: ConciliacionArchivos,
        *, mes: int | None = None, anio: int | None = None,
    ) -> ConciliacionResultado:
        try:
            ramo = obtener_ramo(ramo_codigo)
            mes_final, anio_final = ramo.inferir_periodo(str(archivos.cobro), mes, anio)

            personas = ramo.cargar_personas(archivos.personas)
            relacion = ramo.cargar_relacion(archivos.relacion)
            cobro = ramo.cargar_cobro(archivos.cobro)
            novedades = ramo.cargar_novedades(archivos.novedades)
            datos_extra = ramo.construir_datos_extra(archivos.cobro) if ramo.construir_datos_extra else {}
            if ramo.servicio_cu:
                datos_extra = {**datos_extra, "recibo_cu": analizar_recibo(archivos.recibo, ramo.servicio_cu)}

            motor = ReconciliationEngine(ramo.reglas)
            reporte = motor.ejecutar(
                relacion=relacion, cobro=cobro, novedades=novedades, personas=personas,
                mes=mes_final, anio=anio_final, ramo=ramo.nombre, clave_col=ramo.clave_col,
                datos_extra=datos_extra,
            )
        except ConciliadorError as exc:
            raise ConciliacionServiceError(str(exc)) from exc
        except (KeyError, ValueError, OSError) as exc:
            raise ConciliacionServiceError(f"No se pudo conciliar el ramo '{ramo_codigo}': {exc}") from exc

        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        return ConciliacionResultado(
            reporte=reporte,
            contenido_excel=reporte_a_bytes(reporte),
            nombre_archivo=f"Reporte_Conciliacion_{ramo.nombre.replace(' ', '_')}_{marca}.xlsx",
            resumen=reporte.resumen_por_tipo(),
        )
