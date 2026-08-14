"""CLI unificado para correr conciliaciones a mano.

Reemplaza los scripts sueltos conciliador_movilidad.py, conciliador_salud.py,
conciliador_vg.py y conciliador_vg_deudor.py: agregar un ramo nuevo ya no es
escribir un script, es una entrada en `conciliador.ramos.RAMOS`.

Uso:
    python -m conciliador.cli movilidad --carpeta Movilidad
    python -m conciliador.cli salud --carpeta Salud
    python -m conciliador.cli vg_voluntario --carpeta "VG Voluntaria"
    python -m conciliador.cli vg_deudores --carpeta "VG Deudor" --zscore 3

Para probar la fuente Zoho API (en desarrollo, junto al modo Excel legado):
    python -m conciliador.cli movilidad --carpeta Movilidad \
        --fuente-asegurados api --poliza 1080893 --perfil-zoho sandbox
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from conciliador.domain.exceptions import ArchivoNoEncontradoError, ConciliadorError
from conciliador.engine import ReconciliationEngine
from conciliador.parsing.periodo import etiqueta_periodo, nombre_mes
from conciliador.ramos import RAMOS, obtener_ramo
from conciliador.reporting.excel_writer import escribir_reporte_excel
from conciliador.rules.valor import ComparacionEstadisticaRule
from conciliador.sources.content_understanding import analizar_recibo

# Carpeta por defecto donde cada ramo suele dejar sus archivos (ver README de cada carpeta).
_CARPETAS_POR_DEFECTO = {
    "movilidad": "Movilidad",
    "salud": "Salud",
    "vg_voluntario": "VG Voluntaria",
    "vg_deudores": "VG Deudor",
}


def _encontrar_archivo(carpeta: str, patron: str) -> str | None:
    coincidencias = glob.glob(os.path.join(carpeta, patron))
    return coincidencias[0] if coincidencias else None


def _resolver_archivos(
    ramo, carpeta: str, overrides: dict[str, str | None], *, fuente_asegurados: str
) -> dict[str, str | None]:
    resueltos = {
        rol: overrides.get(rol) or _encontrar_archivo(carpeta, patron)
        for rol, patron in ramo.patrones_archivo.items()
    }
    # En modo api, personas/relacion no vienen de archivo: se consultan a Zoho.
    obligatorios = ("cobro",) if fuente_asegurados == "api" else ("personas", "cobro", "relacion")
    for rol in obligatorios:
        if not resueltos.get(rol):
            patron = ramo.patrones_archivo[rol]
            raise ArchivoNoEncontradoError(
                f"No se encontro archivo de '{rol}' en '{carpeta}' (patron esperado: '{patron}')"
            )
    return resueltos


def _zoho_facade(perfil: str):
    """Construye un ZohoFacade fuera de Django, leyendo `.env` del cwd si existe
    (misma convencion de python-dotenv que usa `config.settings`)."""
    from dotenv import load_dotenv

    from ays_zoho_sdk import ZohoSettings
    from ays_zoho_sdk.factory import get_zoho as _get_zoho_sdk

    load_dotenv()
    return _get_zoho_sdk(config=ZohoSettings.from_env(profile=perfil))


def _ejecutar(codigo_ramo: str, args: argparse.Namespace) -> None:
    ramo = obtener_ramo(codigo_ramo)
    fuente_asegurados = args.fuente_asegurados
    if fuente_asegurados == "api" and (not ramo.cargar_personas_api or not ramo.cargar_relacion_api):
        raise ConciliadorError(f"El ramo '{codigo_ramo}' todavia no soporta --fuente-asegurados api.")
    if fuente_asegurados == "api" and not args.poliza:
        raise ConciliadorError("--fuente-asegurados api requiere --poliza.")

    overrides = {"personas": args.personas, "cobro": args.cobro, "relacion": args.relacion,
                 "novedades": args.novedades, "recibo": args.recibo}
    archivos = _resolver_archivos(ramo, args.carpeta, overrides, fuente_asegurados=fuente_asegurados)

    mes, anio = ramo.inferir_periodo(archivos["cobro"], args.mes, args.anio)
    print(f"Periodo detectado: {etiqueta_periodo(mes, anio)}")
    print(f"Fuente asegurados/personas: {fuente_asegurados}" + (f" (poliza {args.poliza})" if fuente_asegurados == "api" else ""))
    for rol, ruta in archivos.items():
        if fuente_asegurados == "api" and rol in ("personas", "relacion"):
            continue
        print(f"  {rol:10s}: {ruta or 'NO DISPONIBLE (placeholder)'}")

    if getattr(args, "zscore", None) is not None:
        for regla in ramo.reglas:
            if isinstance(regla, ComparacionEstadisticaRule):
                regla.zscore_umbral = args.zscore
                print(f"  umbral z-score sobreescrito a {args.zscore}")

    cobro = ramo.cargar_cobro(archivos["cobro"])
    if fuente_asegurados == "api":
        zoho = _zoho_facade(args.perfil_zoho)
        relacion = ramo.cargar_relacion_api(zoho, poliza=args.poliza)
        documentos = set(relacion["documento"]) | set(relacion["documento_titular"]) | set(cobro.get("documento", []))
        personas = ramo.cargar_personas_api(zoho, documentos=documentos)
        novedades = (
            ramo.cargar_novedades_api(zoho, poliza=args.poliza)
            if ramo.cargar_novedades_api
            else ramo.cargar_novedades(archivos["novedades"])
        )
    else:
        personas = ramo.cargar_personas(archivos["personas"])
        relacion = ramo.cargar_relacion(archivos["relacion"])
        novedades = ramo.cargar_novedades(archivos["novedades"])
    datos_extra = ramo.construir_datos_extra(archivos["cobro"]) if ramo.construir_datos_extra else {}
    if ramo.servicio_cu:
        datos_extra = {**datos_extra, "recibo_cu": analizar_recibo(archivos.get("recibo"), ramo.servicio_cu)}

    motor = ReconciliationEngine(ramo.reglas)
    reporte = motor.ejecutar(
        relacion=relacion, cobro=cobro, novedades=novedades, personas=personas,
        mes=mes, anio=anio, ramo=ramo.nombre, clave_col=ramo.clave_col, datos_extra=datos_extra,
    )

    etiqueta_archivo = f"{nombre_mes(mes)}_{anio}"
    salida = args.salida or os.path.join(
        args.carpeta, f"Reporte_Conciliacion_{ramo.nombre.replace(' ', '_')}_{etiqueta_archivo}.xlsx"
    )
    escribir_reporte_excel(reporte, salida)

    print(f"\nTotal de incidentes encontrados: {reporte.total_incidentes}")
    for tipo, cantidad in reporte.resumen_por_tipo().items():
        print(f"  {tipo}: {cantidad}")
    print(f"\nReporte guardado en: {salida}")


def _armar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="ramo", required=True)

    for codigo, ramo in RAMOS.items():
        sub = subparsers.add_parser(codigo, help=f"Conciliar {ramo.nombre}")
        sub.add_argument("--carpeta", default=_CARPETAS_POR_DEFECTO.get(codigo, ramo.nombre))
        sub.add_argument("--personas", default=None)
        sub.add_argument("--cobro", default=None)
        sub.add_argument("--relacion", default=None)
        sub.add_argument("--novedades", default=None)
        sub.add_argument("--recibo", default=None, help="PDF del recibo de la aseguradora (validacion Content Understanding)")
        sub.add_argument("--mes", type=int, default=None)
        sub.add_argument("--anio", type=int, default=None)
        sub.add_argument("--salida", default=None)
        sub.add_argument("--fuente-asegurados", choices=("excel", "api"), default="excel",
                          help="excel (legado, por defecto) o api (consulta directa a Zoho).")
        sub.add_argument("--poliza", default=None, help="Numero de poliza; requerido con --fuente-asegurados api.")
        sub.add_argument("--perfil-zoho", choices=("sandbox", "production"), default="sandbox")
        if any(isinstance(r, ComparacionEstadisticaRule) for r in ramo.reglas):
            sub.add_argument("--zscore", type=float, default=None,
                              help="Desviaciones estandar fuera del promedio de variacion %% para marcar incidente")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _armar_parser()
    args = parser.parse_args(argv)
    try:
        _ejecutar(args.ramo, args)
    except ConciliadorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
