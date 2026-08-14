"""Puente entre los archivos subidos (Django) y el motor `conciliador`.

Mismo patrón que `soat/services/processor.py`: se recibe el contenido ya validado
por el formulario, se materializa en un directorio temporal (preservando el
nombre original del archivo de cobro, del que algunos ramos infieren el periodo),
se ejecuta la conciliación y se devuelve el Excel + un resumen serializable. El
Excel se endurece (neutralización anti fórmula y sin hipervínculos) antes de salir.
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from ays_zoho_sdk.exceptions import ZohoError
from openpyxl import load_workbook

from conciliador.service import (
    ConciliacionArchivos,
    ConciliacionService,
    ConciliacionServiceError,
)
from conciliador.sources.zoho_api import resolver_id_poliza
from integrations.zoho import get_zoho

logger = logging.getLogger("conciliacion")

# El boton "Facturar" enlaza directo al registro de la poliza en la interfaz
# web de Zoho CRM. Facturar es una accion real de negocio: el enlace siempre
# apunta a Produccion, sin importar en que perfil se corrio la conciliacion
# (conciliar en Sandbox no debe llevar a facturar contra datos de prueba).
_ZOHO_CRM_WEB_ORG = "753703967"
_ZOHO_CRM_POLIZAS_TAB = "CustomModule4"


def _url_facturar(poliza_id: str) -> str:
    return f"https://crm.zoho.com/crm/org{_ZOHO_CRM_WEB_ORG}/tab/{_ZOHO_CRM_POLIZAS_TAB}/{poliza_id}"


def _resolver_facturar_url(poliza: str) -> str | None:
    try:
        zoho_produccion = get_zoho(profile="production")
        poliza_id = resolver_id_poliza(zoho_produccion, poliza=poliza)
    except ZohoError:
        logger.warning(
            "No fue posible resolver el enlace de facturar para la póliza %s", poliza, exc_info=True
        )
        return None
    if not poliza_id:
        return None
    return _url_facturar(poliza_id)


class ConciliacionProcessingError(ValueError):
    """Error de negocio al conciliar; una vista puede mostrar el mensaje tal cual."""


@dataclass(frozen=True)
class ConciliacionOutput:
    content: bytes
    filename: str
    summary: dict[str, object]


_NOMBRE_SEGURO = re.compile(r"[^A-Za-z0-9 ._()\-À-ſ]")


def _nombre_seguro(nombre: str, respaldo: str) -> str:
    """Basename saneado que conserva tokens de mes/año para inferir el periodo."""
    base = os.path.basename((nombre or "").replace("\\", "/"))
    base = _NOMBRE_SEGURO.sub("_", base).strip().strip(".")
    return base or respaldo


def _volcar(archivo, destino: Path) -> Path:
    archivo.seek(0)
    with destino.open("wb") as stream:
        for chunk in archivo.chunks():
            stream.write(chunk)
    archivo.seek(0)
    return destino


def _neutralizar(valor):
    if isinstance(valor, str) and valor[:1] in {"=", "+", "-", "@"}:
        return "'" + valor
    return valor


def _endurecer_xlsx(contenido: bytes) -> bytes:
    """Neutraliza celdas de texto con prefijo de fórmula y quita hipervínculos."""
    workbook = load_workbook(io.BytesIO(contenido))
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    cell.hyperlink = None
                    if isinstance(cell.value, str):
                        cell.value = _neutralizar(cell.value)
        salida = io.BytesIO()
        workbook.save(salida)
        return salida.getvalue()
    finally:
        workbook.close()


def procesar_conciliacion(
    *, ramo: str, poliza: str, archivos: dict, fuente: str = "excel", perfil_zoho: str = "sandbox",
) -> ConciliacionOutput:
    """`archivos` es {slot: UploadedFile|None} con las claves relacion, personas,
    novedades, cobro, recibo (novedades/recibo pueden faltar). En modo
    `fuente="api"`, relacion/personas se ignoran aunque vengan pobladas: se
    consultan directo a Zoho, filtradas por `poliza`."""
    started = monotonic()

    with TemporaryDirectory(prefix="ays-conciliacion-") as directorio:
        raiz = Path(directorio)
        rutas: dict[str, Path] = {}
        respaldos = {
            "relacion": "relacion.xlsx", "personas": "personas.xlsx",
            "novedades": "novedades.xlsx", "cobro": "cobro", "recibo": "recibo.pdf",
        }
        for slot, archivo in archivos.items():
            if fuente == "api" and slot in ("relacion", "personas"):
                continue
            if archivo in (None, False):
                continue
            nombre = _nombre_seguro(getattr(archivo, "name", ""), respaldos.get(slot, slot))
            rutas[slot] = _volcar(archivo, raiz / nombre)

        zoho = None
        if fuente == "api":
            try:
                zoho = get_zoho(profile=perfil_zoho)
            except ZohoError as exc:
                raise ConciliacionProcessingError(
                    f"No fue posible conectar con Zoho (perfil {perfil_zoho}, {exc.category}). "
                    "Puede reintentar en modo Excel mientras se resuelve."
                ) from exc

        entrada = ConciliacionArchivos(
            cobro=rutas["cobro"],
            relacion=rutas.get("relacion"),
            personas=rutas.get("personas"),
            novedades=rutas.get("novedades"),
            recibo=rutas.get("recibo"),
            zoho=zoho,
            poliza=poliza,
        )
        try:
            resultado = ConciliacionService().ejecutar(ramo, entrada)
        except ConciliacionServiceError as exc:
            raise ConciliacionProcessingError(str(exc)) from exc
        except ZohoError as exc:
            raise ConciliacionProcessingError(
                f"Falló la consulta a Zoho (perfil {perfil_zoho}, {exc.category}). "
                "Puede reintentar en modo Excel mientras se resuelve."
            ) from exc

        contenido = _endurecer_xlsx(resultado.contenido_excel)
        reporte = resultado.reporte
        # Solo tiene sentido resolver el enlace de facturar cuando la conciliación
        # queda sin incidentes: es el único caso en que el frontend muestra el botón.
        facturar_url = _resolver_facturar_url(poliza) if reporte.esta_vacio else None
        summary = {
            "ramo": reporte.ramo,
            "periodo": reporte.periodo,
            "poliza": poliza,
            "fuente": fuente,
            "total_incidentes": int(reporte.total_incidentes),
            "sin_incidentes": bool(reporte.esta_vacio),
            "por_tipo": {str(k): int(v) for k, v in resultado.resumen.items()},
            "filename": resultado.nombre_archivo,
            "duration_seconds": round(monotonic() - started, 2),
            "facturar_url": facturar_url,
        }
        return ConciliacionOutput(
            content=contenido,
            filename=resultado.nombre_archivo,
            summary=summary,
        )
