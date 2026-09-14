"""Herramientas de solo lectura para el asistente conversacional de Zoho.

Deliberadamente NO reutilizan los servicios de búsqueda/detalle de
`cotizacion_colectivos` (`UnifiedClientSearchService`, `EntityDetailService`,
`PolicyService`): esos están hechos para el flujo operativo de Colectivos
(exigen Tipo_de_persona/Tipo_ID exactos, arman tokens firmados propios de esa
app y -- en el caso de pólizas -- pasan por un caché de "Workspace" pensado
para su revisión humana, no para una consulta rápida de chat). Este asistente
es de alcance general: consulta Zoho directamente vía la fachada y linkea a
los registros en Zoho CRM, no a páginas internas de ninguna app del portal.

Solo se usan `records.get_by_id` y `search.by_field` / `search.by_criteria`
(nunca `records.create/update/upsert`).
"""
from __future__ import annotations

import re
from typing import Any

from cotizacion_colectivos.services.common import (
    ColectivosServiceError,
    colectivos_zoho,
    escape_criteria_value,
    get_colectivos_profile,
    mask_document,
    mask_reference,
    translate_zoho_error,
)
from integrations.zoho.exceptions import ZohoError

CONTACTS_MODULE = "Contacts"
POLICIES_MODULE = "Polizas"
INSURED_MODULE = "Riesgos1"
TASKS_MODULE = "Tasks"

CONTACT_SEARCH_FIELDS = (
    "id", "Tipo_de_persona", "Tipo_ID", "N_mero_de_ID", "Full_Name",
    "First_Name", "Last_Name", "Raz_n_social", "Nombre_comercial", "Estado",
    "Email", "Phone", "Mobile",
)
INSURED_ROLE_FIELDS = ("Asegurado", "Contacto_facturaci_n_dividida_colectivas", "Beneficiario")
POLICY_SEARCH_FIELDS = (
    "id", "Name", "Tomador_principal1", "Estado_de_la_p_liza", "Ramo",
    "Aseguradora1", "P_liza_Fecha_de_inicio_vigencia", "P_liza_Fecha_fin_de_la_vigencia",
    "Modo_de_pago", "Frecuencia",
)
TASK_SEARCH_FIELDS = (
    "id", "Subject", "Responsable", "Correo_responsable", "Estado",
    "tipo_de_solicitud", "rea", "Fecha_de_solicitud_del_cliente",
)

MAX_QUERY_LENGTH = 160
SEARCH_LIMIT = 10
_DOCUMENT_PATTERN = re.compile(r"\d{5,}")
_ID_LABEL_PATTERN = re.compile(
    r"\b(cc|c\.c\.?|nit|ti|t\.i\.?|ce|c\.e\.?|pasaporte|documento|c[eé]dula|n[uú]mero|no\.?)\b",
    re.IGNORECASE,
)


def _text(value: object, default: str = "") -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("display_label") or value.get("value")
    clean = str(value or "").strip()
    return clean or default


def _clean_query(value: str) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if not clean or len(clean) > MAX_QUERY_LENGTH:
        raise ColectivosServiceError("invalid_query", "El criterio de búsqueda no es válido.")
    return clean


def _extract_document(text: str) -> str:
    match = _DOCUMENT_PATTERN.search(text)
    return match.group(0) if match else ""


def _extract_name(text: str, document: str) -> str:
    """Deja solo lo que parece nombre: quita el número de documento (si lo
    hay) y etiquetas comunes de tipo de documento ("cc", "nit", ...), para que
    una frase natural como "Juan Pérez cc 123" busque por "Juan Pérez"."""
    sin_documento = text.replace(document, "") if document else text
    sin_etiquetas = _ID_LABEL_PATTERN.sub("", sin_documento)
    sin_simbolos = re.sub(r"[^\w\sÁÉÍÓÚáéíóúÑñ.&'’-]", " ", sin_etiquetas, flags=re.UNICODE)
    return re.sub(r"\s+", " ", sin_simbolos).strip(" .-")


def _zoho_record_link(zoho, *, module: str, record_id: str) -> str:
    """Link directo al registro en Zoho CRM (no a ninguna página del portal),
    para que el alcance no quede atado a lo que una app en particular decida
    exponer. Requiere el organization_id del perfil activo (ya validado al
    construir la fachada)."""
    org_id = str(getattr(getattr(zoho, "config", None), "expected_org_id", "") or "").strip()
    record = str(record_id or "").strip()
    if not org_id or not record:
        return ""
    return f"https://crm.zoho.com/crm/org{org_id}/tab/{module}/{record}"


def buscar_cliente(query: str) -> dict[str, Any]:
    """Busca clientes en Contacts por nombre y/o número de documento, en
    cualquier combinación y sin asumir un tipo de persona o de documento fijo
    (sirve igual para personas y empresas, y para cualquier tipo de ID)."""
    clean = _clean_query(query)
    documento = _extract_document(clean)
    nombre = _extract_name(clean, documento)
    criterios = []
    if documento:
        criterios.append(f"(N_mero_de_ID:equals:{escape_criteria_value(documento)})")
    if len(nombre) >= 3:
        for field in ("Full_Name", "Raz_n_social", "Nombre_comercial"):
            criterios.append(f"({field}:starts_with:{escape_criteria_value(nombre)})")
    if not criterios:
        raise ColectivosServiceError(
            "invalid_query", "Escribe al menos un nombre (3+ letras) o un número de documento.",
        )
    zoho = colectivos_zoho()
    profile = get_colectivos_profile()
    # Un único criterio combinado con "or" en vez de una búsqueda por cada
    # campo: mismo resultado, una sola llamada a Zoho en vez de hasta 4
    # secuenciales.
    criterio_combinado = "or".join(criterios)
    registros: dict[str, dict] = {}
    try:
        pagina = zoho.search.by_criteria(
            module=CONTACTS_MODULE, criteria=criterio_combinado,
            fields=CONTACT_SEARCH_FIELDS, page=1, limit=SEARCH_LIMIT,
        )
        for record in pagina.records:
            record_id = str(record.get("id") or "")
            if record_id:
                registros.setdefault(record_id, record)
    except ZohoError as exc:
        raise translate_zoho_error(exc, profile) from exc
    clientes = []
    for record in list(registros.values())[:SEARCH_LIMIT]:
        clientes.append({
            "id": str(record.get("id")),
            "nombre": _text(
                record.get("Full_Name") or record.get("Nombre_comercial") or record.get("Raz_n_social"),
                "Sin nombre",
            ),
            "tipo": _text(record.get("Tipo_de_persona")),
            "documento": f"{_text(record.get('Tipo_ID'))} {mask_document(record.get('N_mero_de_ID'))}".strip(),
            "estado": _text(record.get("Estado"), "Sin estado"),
            "link": _zoho_record_link(zoho, module=CONTACTS_MODULE, record_id=str(record.get("id") or "")),
        })
    return {"total": len(clientes), "clientes": clientes}


def detalle_cliente(record_id: str) -> dict[str, Any]:
    """Trae el detalle de un cliente por su id de Zoho (el `id` que devolvió
    `buscar_cliente`), incluidas las pólizas en las que aparece con algún rol
    (asegurado, afiliado o beneficiario)."""
    value = str(record_id or "").strip()
    if not value.isdigit():
        raise ColectivosServiceError("invalid_record", "El identificador de cliente no es válido.")
    zoho = colectivos_zoho()
    profile = get_colectivos_profile()
    try:
        contacto = _get_by_id(
            zoho, module=CONTACTS_MODULE, record_id=value,
            fields=CONTACT_SEARCH_FIELDS + ("Direcci_n", "Ciudad_de_direcci_n_principal", "Empresa"),
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc, profile) from exc
    if not contacto:
        return {"encontrado": False}
    poliza_ids: set[str] = set()
    for campo in INSURED_ROLE_FIELDS:
        try:
            pagina = zoho.search.by_criteria(
                module=INSURED_MODULE, criteria=f"({campo}:equals:{escape_criteria_value(value)})",
                fields=("id", "P_liza"), page=1, limit=SEARCH_LIMIT,
            )
        except ZohoError:
            # Un rol que falla no debe tirar los pólizas ya encontradas por
            # los otros roles -- se degrada, no se vacía todo el resultado.
            continue
        for relacion in pagina.records:
            poliza_id = _lookup_id(relacion.get("P_liza"))
            if poliza_id:
                poliza_ids.add(poliza_id)
    polizas_por_id = _batch_policies(zoho, poliza_ids)
    polizas = [
        {
            "referencia": mask_reference(registro.get("Name")),
            "ramo": _text(registro.get("Ramo")),
            "aseguradora": _text(registro.get("Aseguradora1")),
            "estado": _text(registro.get("Estado_de_la_p_liza"), "Sin estado"),
            "link": _zoho_record_link(zoho, module=POLICIES_MODULE, record_id=poliza_id),
        }
        for poliza_id, registro in polizas_por_id.items()
    ]
    return {
        "encontrado": True,
        "nombre": _text(
            contacto.get("Full_Name") or contacto.get("Nombre_comercial") or contacto.get("Raz_n_social"),
            "Sin nombre",
        ),
        "documento": f"{_text(contacto.get('Tipo_ID'))} {mask_document(contacto.get('N_mero_de_ID'))}".strip(),
        "estado": _text(contacto.get("Estado"), "Sin estado"),
        "correo": _text(contacto.get("Email")),
        "telefono": _text(contacto.get("Phone") or contacto.get("Mobile")),
        "polizas": polizas,
        "link": _zoho_record_link(zoho, module=CONTACTS_MODULE, record_id=value),
    }


def _get_by_id(zoho, *, module: str, record_id: str, fields: tuple[str, ...]) -> dict:
    """Trae un registro por id vía `search.by_criteria(id:equals:...)` en vez de
    `records.get_by_id`: el backend SDK de Zoho es poco confiable resolviendo
    detalle por id aunque Search sí funcione (mismo hallazgo ya documentado en
    `cotizacion_colectivos.services.entity_detail`)."""
    pagina = zoho.search.by_criteria(
        module=module, criteria=f"(id:equals:{escape_criteria_value(record_id)})",
        fields=fields, page=1, limit=1,
    )
    return dict(pagina.records[0]) if pagina.records else {}


def _batch_policies(zoho, poliza_ids: set[str]) -> dict[str, dict]:
    """Trae varias pólizas en una sola consulta COQL en vez de una llamada por
    id -- un cliente con varias pólizas ya no paga una ronda de red por cada
    una. `poliza_ids` ya viene validado como solo dígitos (ver `_lookup_id`)."""
    if not poliza_ids:
        return {}
    ids = sorted(poliza_ids)[:SEARCH_LIMIT]
    valores = ",".join(f"'{value}'" for value in ids)
    campos = ",".join(POLICY_SEARCH_FIELDS)
    query = f"select {campos} from {POLICIES_MODULE} where id in ({valores}) limit {SEARCH_LIMIT}"
    try:
        pagina = zoho.coql.execute(query)
    except ZohoError:
        return {}
    return {
        str(registro.get("id")): registro
        for registro in pagina.records
        if str(registro.get("id") or "").isdigit()
    }


def _lookup_id(value: object) -> str:
    if isinstance(value, dict):
        candidate = str(value.get("id") or "").strip()
        return candidate if candidate.isdigit() else ""
    return ""


def obtener_poliza(numero_poliza: str) -> dict[str, Any]:
    """Busca una póliza por su número (campo `Name` en Zoho) y devuelve su resumen."""
    clean = _clean_query(numero_poliza)
    zoho = colectivos_zoho()
    profile = get_colectivos_profile()
    try:
        pagina = zoho.search.by_field(
            module=POLICIES_MODULE, field="Name", value=clean,
            fields=POLICY_SEARCH_FIELDS, page=1, limit=5,
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc, profile) from exc
    registros = [record for record in pagina.records if str(record.get("id") or "").strip()]
    if not registros:
        return {"encontrada": False}
    if len(registros) > 1:
        return {"encontrada": False, "motivo": "ambiguo", "coincidencias": len(registros)}
    registro = registros[0]
    record_id = str(registro.get("id"))
    return {
        "encontrada": True,
        "referencia": mask_reference(registro.get("Name")),
        "tomador": _text(registro.get("Tomador_principal1")),
        "ramo": _text(registro.get("Ramo")),
        "aseguradora": _text(registro.get("Aseguradora1")),
        "estado": _text(registro.get("Estado_de_la_p_liza"), "Sin estado"),
        "vigencia_inicio": _text(registro.get("P_liza_Fecha_de_inicio_vigencia")),
        "vigencia_fin": _text(registro.get("P_liza_Fecha_fin_de_la_vigencia")),
        "modo_de_pago": _text(registro.get("Modo_de_pago")),
        "frecuencia_de_pago": _text(registro.get("Frecuencia")),
        "link": _zoho_record_link(zoho, module=POLICIES_MODULE, record_id=record_id),
    }


def _buscar_tareas(campo: str, valor: str) -> dict[str, Any]:
    clean = _clean_query(valor)
    zoho = colectivos_zoho()
    try:
        pagina = zoho.search.by_field(
            module=TASKS_MODULE, field=campo, value=clean,
            fields=TASK_SEARCH_FIELDS, page=1, limit=SEARCH_LIMIT,
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc, get_colectivos_profile()) from exc
    tareas = [
        {
            "asunto": _text(record.get("Subject")),
            "tipo": _text(record.get("tipo_de_solicitud")),
            "area": _text(record.get("rea")),
            "estado": _text(record.get("Estado"), "Sin estado"),
            "responsable": _text(record.get("Responsable")),
            "fecha_solicitud": _text(record.get("Fecha_de_solicitud_del_cliente")),
            "link": _zoho_record_link(zoho, module=TASKS_MODULE, record_id=str(record.get("id") or "")),
        }
        for record in pagina.records[:SEARCH_LIMIT]
    ]
    return {"total": len(tareas), "tareas": tareas}


def mis_tareas(correo_usuario: str) -> dict[str, Any]:
    """Tareas (Tasks) cuyo `Correo_responsable` coincide con el correo de quien
    inició sesión en el portal -- no acepta un correo arbitrario del mensaje del
    usuario, siempre viene del `request.user` autenticado."""
    return _buscar_tareas("Correo_responsable", correo_usuario)


def tareas_por_responsable(nombre_responsable: str) -> dict[str, Any]:
    """Tareas asignadas a un responsable, buscando por su nombre tal como
    aparece en el campo `Responsable` de Zoho."""
    return _buscar_tareas("Responsable", nombre_responsable)
