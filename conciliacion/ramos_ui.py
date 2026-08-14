"""Metadata de presentación de los ramos para el Conciliador de Facturación.

Describe, por cada ramo habilitado, los "slots" de archivo que el usuario debe
subir (etiqueta, descripción, extensiones aceptadas, si es obligatorio y si es un
archivo "temporal" que en el futuro se reemplazará por conexión directa a Zoho).

No contiene lógica de conciliación: el motor real vive en el paquete `conciliador`.
Los códigos de ramo (`movilidad`, `salud`, `vg_voluntario`, `vg_deudores`) deben
coincidir con las claves registradas en `conciliador.ramos.RAMOS`.
"""

from __future__ import annotations

from conciliador.ramos import RAMOS

# Nota estándar de los archivos que hoy se cargan a mano y mañana llegarán por API.
NOTA_TEMPORAL_ZOHO = (
    "Temporal: por ahora se sube manualmente; se reemplazará por conexión "
    "directa por API a Zoho."
)

# Slots comunes a todos los ramos. El slot de cobro se define por ramo porque su
# formato cambia (CSV de Sharefile, Excel de Porchat, Excel de AVA…).
_SLOTS_COMUNES: dict[str, dict] = {
    "relacion": {
        "campo": "relacion",
        "label": "Relación de asegurados (Zoho)",
        "help": "Exportación desde Zoho de la relación de asegurados del ramo.",
        "accept": ".xlsx",
        "required": True,
        "temporal": True,
    },
    "personas": {
        "campo": "personas",
        "label": "Personas (Zoho)",
        "help": "Archivo Personas_Zoho con los documentos válidos del sistema.",
        "accept": ".xlsx",
        "required": True,
        "temporal": True,
    },
    "novedades": {
        "campo": "novedades",
        "label": "Novedades (Zoho)",
        "help": "Reporte de novedades (altas/bajas) del periodo. Opcional.",
        "accept": ".xlsx",
        "required": False,
        "temporal": True,
    },
    "cobro": {
        "campo": "cobro",
        "label": "Archivo de cobro",
        "help": "Archivo de cobro emitido por la aseguradora.",
        "accept": ".xlsx",
        "required": True,
        "temporal": False,
    },
    "recibo": {
        "campo": "recibo",
        "label": "PDF de cobro (recibo de la aseguradora)",
        "help": (
            "Recibo/factura en PDF de la aseguradora. La validación automática "
            "del recibo requiere Content Understanding configurado; sin él, esa "
            "verificación se marca como “N/D” sin afectar la conciliación."
        ),
        "accept": ".pdf",
        "required": True,
        "temporal": False,
    },
}

# Orden de presentación de los slots en el formulario.
_ORDEN_SLOTS = ["cobro", "recibo", "relacion", "personas", "novedades"]

# Sobrescrituras específicas por ramo (por ahora solo el slot de cobro y algún
# ajuste puntual de novedades).
_OVERRIDES: dict[str, dict[str, dict]] = {
    "movilidad": {
        "cobro": {
            "label": "Archivo de cobro (CSV de Sharefile)",
            "help": "CSV plano de Sharefile, separado por “;”. Cruce por placa.",
            "accept": ".csv",
        },
    },
    "salud": {
        "cobro": {
            "label": "Archivo de cobro (Excel de Porchat)",
            "help": "Exportación del portal Porchat en Excel. Cruce por documento.",
            "accept": ".xlsx",
        },
    },
    "vg_voluntario": {
        "cobro": {
            "label": "Archivo de cobro (Excel de AVA)",
            "help": "Excel de AVA con la hoja “Desglose de Coberturas”. Cruce por clave compuesta.",
            "accept": ".xls,.xlsx",
        },
    },
    "vg_deudores": {
        "cobro": {
            "label": "Archivo de cobro (Excel de AVA)",
            "help": "Excel de AVA con la hoja “Desglose de Coberturas”. Comparación estadística de valor.",
            "accept": ".xls,.xlsx",
        },
        "novedades": {
            "label": "Novedades de cartera (cliente)",
            "help": "Reporte de cartera del banco/financiera dueño de la obligación. Opcional.",
            "temporal": False,
        },
    },
}

# Ramos habilitados y su nombre visible (el orden define el del selector).
RAMOS_HABILITADOS: list[tuple[str, str]] = [
    ("movilidad", "Movilidad"),
    ("salud", "Salud"),
    ("vg_voluntario", "VG Voluntario"),
    ("vg_deudores", "VG Deudores"),
]

RAMO_CHOICES = list(RAMOS_HABILITADOS)
RAMO_CODIGOS = frozenset(codigo for codigo, _ in RAMOS_HABILITADOS)

# Campos de archivo del formulario (deben existir como FileField en el form).
CAMPOS_ARCHIVO = tuple(_ORDEN_SLOTS)


def _merge(base: dict, override: dict | None) -> dict:
    slot = dict(base)
    if override:
        slot.update(override)
    slot["nota_temporal"] = NOTA_TEMPORAL_ZOHO if slot.get("temporal") else ""
    return slot


def slots_de_ramo(codigo: str) -> list[dict]:
    """Lista ordenada de slots (dicts de presentación) para un ramo."""
    overrides = _OVERRIDES.get(codigo, {})
    return [_merge(_SLOTS_COMUNES[nombre], overrides.get(nombre)) for nombre in _ORDEN_SLOTS]


def catalogo_slots() -> dict[str, list[dict]]:
    """Mapa {codigo_ramo: [slots]} — se serializa a JSON para el frontend."""
    return {codigo: slots_de_ramo(codigo) for codigo in RAMO_CODIGOS}


def ramo_soporta_api(codigo: str) -> bool:
    """True si el ramo ya tiene loaders de Zoho API (ver `conciliador.ramos`),
    no solo el archivo Excel. Se deriva de `RAMOS` en vez de mantenerse a mano
    para que nunca quede desincronizado con lo que el motor realmente soporta."""
    ramo = RAMOS.get(codigo)
    return bool(ramo and ramo.cargar_relacion_api and ramo.cargar_personas_api)


def ramo_soporta_novedades_api(codigo: str) -> bool:
    """True si, ademas de asegurados/personas, el ramo tambien resuelve
    novedades por API (no todos: vg_deudores la recibe del banco, no de
    Zoho, y sigue exigiendo el archivo aunque el resto venga de la API)."""
    ramo = RAMOS.get(codigo)
    return bool(ramo and ramo.cargar_novedades_api)


def catalogo_fuentes() -> dict[str, dict[str, bool]]:
    """Mapa {codigo_ramo: {"asegurados": bool, "novedades": bool}} — se
    serializa a JSON para el frontend."""
    return {
        codigo: {
            "asegurados": ramo_soporta_api(codigo),
            "novedades": ramo_soporta_novedades_api(codigo),
        }
        for codigo in RAMO_CODIGOS
    }
