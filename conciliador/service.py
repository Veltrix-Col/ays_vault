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
from typing import TYPE_CHECKING

from conciliador.domain.exceptions import ConciliadorError
from conciliador.domain.models import ReporteConciliacion
from conciliador.engine import ReconciliationEngine
from conciliador.ramos import obtener_ramo
from conciliador.reporting.excel_writer import reporte_a_bytes
from conciliador.sources.content_understanding import analizar_recibo

if TYPE_CHECKING:
    from ays_zoho_sdk import ZohoFacade


class ConciliacionServiceError(ValueError):
    """Error de negocio al conciliar. Una vista puede capturar esto y
    devolver un 400 con el mensaje tal cual, sin exponer trazas internas."""


@dataclass(frozen=True)
class ConciliacionArchivos:
    """`relacion` y `personas` son requeridos solo en modo legado (Excel). En
    modo API se dejan en None y se pasa `zoho` + `poliza` en su lugar: el
    servicio consulta directamente Contacts/Riesgos1 y nunca exige esos dos
    archivos. `novedades` no tiene equivalente por API todavia (ver
    `conciliador.sources.zoho_api`) y siempre viene de archivo, en ambos modos."""

    cobro: str | Path
    relacion: str | Path | None = None
    personas: str | Path | None = None
    novedades: str | Path | None = None
    recibo: str | Path | None = None  # PDF de la aseguradora para validar via Content Understanding
    zoho: "ZohoFacade | None" = None
    poliza: str | None = None


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
            cobro = ramo.cargar_cobro(archivos.cobro)

            if archivos.zoho is not None:
                if not ramo.cargar_personas_api or not ramo.cargar_relacion_api:
                    raise ConciliadorError(
                        f"El ramo '{ramo_codigo}' todavia no soporta consulta directa a Zoho; "
                        "use los archivos Excel (relacion/personas)."
                    )
                if not archivos.poliza:
                    raise ConciliadorError("Se requiere el numero de poliza para consultar Zoho por API.")
                relacion = ramo.cargar_relacion_api(archivos.zoho, poliza=archivos.poliza)
                # Personas se busca dirigido (no se trae Contacts completo): la union
                # de documentos que aparecen en la relacion (incluido el titular) y
                # en el cobro es lo unico que IngresoNuevoSinPersonaRule necesita.
                documentos = (
                    set(relacion["documento"]) | set(relacion["documento_titular"])
                    | set(cobro.get("documento", []))
                )
                personas = ramo.cargar_personas_api(archivos.zoho, documentos=documentos)
                # Algunos ramos (vg_deudores) no tienen equivalente de novedades por
                # API porque su novedad la genera el banco, no Zoho: para esos se
                # sigue leyendo el archivo aunque el resto venga de la API.
                novedades = (
                    ramo.cargar_novedades_api(archivos.zoho, poliza=archivos.poliza)
                    if ramo.cargar_novedades_api
                    else ramo.cargar_novedades(archivos.novedades)
                )
            else:
                if not archivos.personas or not archivos.relacion:
                    raise ConciliadorError(
                        "Se requieren los archivos 'relacion' y 'personas' cuando no se usa Zoho por API."
                    )
                personas = ramo.cargar_personas(archivos.personas)
                relacion = ramo.cargar_relacion(archivos.relacion)
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
