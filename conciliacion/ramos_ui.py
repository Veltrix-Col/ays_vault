"""Metadata de presentación de los ramos x compañía para el Conciliador de
Facturación.

Describe, por cada combinación (ramo, compañía) habilitada, los "slots" de
archivo que el usuario debe subir (etiqueta, descripción, extensiones
aceptadas, si es obligatorio y si es un archivo "temporal" que en el futuro
se reemplazará por conexión directa a Zoho): el formato del archivo de cobro
lo define la aseguradora, no el ramo, así que la misma información de
presentación también varía por compañía.

No contiene lógica de conciliación: el motor real vive en el paquete
`conciliador`. Los códigos de ramo (`movilidad`, `salud`, `vg_voluntario`,
`vg_deudores`) y de compañía (`sura`, ...) deben coincidir con las claves
registradas en `conciliador.ramos.RAMOS`.
"""

from __future__ import annotations

from conciliador.ramos import RAMOS, companias_de_ramo

# Nota estándar de los archivos que hoy se cargan a mano y mañana llegarán por API.
NOTA_TEMPORAL_ZOHO = (
    "Temporal: por ahora se sube manualmente; se reemplazará por conexión "
    "directa por API a Zoho."
)

# Nombre visible de cada compañía soportada por al menos un ramo. Las que un
# ramo en particular no soporta ni siquiera aparecen en su selector -- ver
# `companias_de_ramo` en `conciliador.ramos`, la fuente de verdad real.
NOMBRE_COMPANIA: dict[str, str] = {
    "sura": "Sura",
}

# Slots comunes a toda combinación ramo x compañía. El slot de cobro se
# sobrescribe por (ramo, compañía) porque su formato cambia (CSV de
# Sharefile, Excel de Porchat, Excel de AVA…). Relación de asegurados y
# Personas ya no son slots de archivo: se consultan siempre directo a Zoho
# (Full API) filtradas por la póliza indicada en el formulario, ver
# `conciliacion.services.processor`.
_SLOTS_COMUNES: dict[str, dict] = {
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
            "Recibo/factura en PDF de la aseguradora. Opcional: es una validación "
            "adicional hecha con IA (Azure AI Foundry) que solo genera una "
            "advertencia informativa si no cuadra o no se sube; nunca impide "
            "continuar ni conciliar en Zoho."
        ),
        "accept": ".pdf",
        "required": False,
        "temporal": False,
    },
}

# Orden de presentación de los slots en el formulario.
_ORDEN_SLOTS = ["cobro", "recibo", "novedades"]

# Sobrescrituras especificas por (ramo, compañía): hoy todo es Sura, unica
# compañía configurada en `conciliador.ramos.RAMOS`.
_OVERRIDES: dict[str, dict[str, dict[str, dict]]] = {
    "movilidad": {
        "sura": {
            "cobro": {
                "label": "Archivo de cobro (CSV de Sharefile)",
                "help": "CSV plano de Sharefile, separado por “;”. Cruce por placa.",
                "accept": ".csv",
            },
        },
    },
    "salud": {
        "sura": {
            "cobro": {
                "label": "Archivo de cobro (Excel de Porchat o de Sura directo)",
                "help": ("Exportación del portal Porchat, o si no está disponible, descarga directa del "
                         "portal de Sura. El sistema detecta cuál de los dos formatos es. Cruce por documento."),
                "accept": ".xls,.xlsx",
            },
        },
    },
    "vg_voluntario": {
        "sura": {
            "cobro": {
                "label": "Archivo de cobro (Excel de AVA)",
                "help": "Excel de AVA con la hoja “Desglose de Coberturas”. Cruce por clave compuesta.",
                "accept": ".xls,.xlsx",
            },
        },
    },
    "vg_deudores": {
        "sura": {
            "cobro": {
                "label": "Archivo de cobro (Excel de AVA o de Riesgos vigentes)",
                "help": ("Excel de AVA con la hoja “Desglose de Coberturas”, o si no está disponible, el export "
                         "alternativo de Riesgos vigentes de la cartera de créditos. El sistema detecta cuál de "
                         "los dos es."),
                "accept": ".xls,.xlsx",
            },
            "novedades": {
                "label": "Novedades de cartera (cliente)",
                "help": "Reporte de cartera del banco/financiera dueño de la obligación. Opcional.",
                "temporal": False,
            },
        },
    },
}

# Ramos habilitados y su nombre visible (el orden define el del selector).
# Sufijo "Colectivos" para distinguirlos de los mismos ramos en otros negocios.
RAMOS_HABILITADOS: list[tuple[str, str]] = [
    ("movilidad", "Movilidad Colectivos"),
    ("salud", "Salud Colectivos"),
    ("vg_voluntario", "VG Voluntario Colectivos"),
    ("vg_deudores", "VG Deudores Colectivos"),
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


def companias_de_ramo_ui(ramo_codigo: str) -> list[tuple[str, str]]:
    """[(codigo, nombre_visible)] de las compañías configuradas para un ramo,
    en el orden en que deben aparecer en el selector."""
    return [(codigo, NOMBRE_COMPANIA.get(codigo, codigo.capitalize())) for codigo in companias_de_ramo(ramo_codigo)]


def catalogo_companias() -> dict[str, list[tuple[str, str]]]:
    """Mapa {codigo_ramo: [(codigo_compania, nombre_visible)]} -- se serializa
    a JSON para que el frontend actualice el selector de compañía cuando
    cambia el ramo."""
    return {codigo: companias_de_ramo_ui(codigo) for codigo in RAMO_CODIGOS}


def slots_de_ramo(ramo_codigo: str, compania_codigo: str) -> list[dict]:
    """Lista ordenada de slots (dicts de presentación) para un (ramo, compañía)."""
    overrides = _OVERRIDES.get(ramo_codigo, {}).get(compania_codigo, {})
    return [_merge(_SLOTS_COMUNES[nombre], overrides.get(nombre)) for nombre in _ORDEN_SLOTS]


def catalogo_slots() -> dict[str, dict[str, list[dict]]]:
    """Mapa {codigo_ramo: {codigo_compania: [slots]}} — se serializa a JSON
    para el frontend."""
    return {
        ramo_codigo: {
            compania_codigo: slots_de_ramo(ramo_codigo, compania_codigo)
            for compania_codigo in companias_de_ramo(ramo_codigo)
        }
        for ramo_codigo in RAMO_CODIGOS
    }


def ramo_soporta_api(ramo_codigo: str, compania_codigo: str) -> bool:
    """True si el (ramo, compañía) ya tiene loaders de Zoho API (ver
    `conciliador.ramos`), no solo el archivo Excel. Se deriva de `RAMOS` en
    vez de mantenerse a mano para que nunca quede desincronizado con lo que
    el motor realmente soporta."""
    ramo = RAMOS.get(ramo_codigo, {}).get(compania_codigo)
    return bool(ramo and ramo.cargar_relacion_api and ramo.cargar_personas_api)


def ramo_soporta_novedades_api(ramo_codigo: str, compania_codigo: str) -> bool:
    """True si, ademas de relacion/personas, el (ramo, compañía) tambien
    resuelve novedades por API (no todos: vg_deudores/Sura la recibe del
    banco, no de Zoho, y sigue exigiendo el archivo aunque el resto venga de
    la API)."""
    ramo = RAMOS.get(ramo_codigo, {}).get(compania_codigo)
    return bool(ramo and ramo.cargar_novedades_api)


def catalogo_novedades_api() -> dict[str, dict[str, bool]]:
    """Mapa {codigo_ramo: {codigo_compania: bool}} — True si el slot de
    novedades se resuelve solo por Zoho API (se oculta el upload); se
    serializa a JSON para el frontend."""
    return {
        ramo_codigo: {
            compania_codigo: ramo_soporta_novedades_api(ramo_codigo, compania_codigo)
            for compania_codigo in companias_de_ramo(ramo_codigo)
        }
        for ramo_codigo in RAMO_CODIGOS
    }
