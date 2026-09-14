"""Herramientas de solo lectura para el asistente conversacional de Zoho.

Cada función devuelve un dict JSON-serializable pensado para que el modelo lo
resuma en lenguaje natural. Ninguna función escribe en Zoho: sólo se usan
`records.get_by_id`, `search.by_field` / `search.by_criteria` de la fachada
(nunca `records.create/update/upsert`). Se reutilizan a propósito los mismos
servicios que ya usa el resto de Colectivos (masking de documentos, tokens
firmados para los links) en vez de duplicar consultas o criterios de Zoho.
"""
from __future__ import annotations

from typing import Any

from django.urls import reverse

from cotizacion_colectivos.services.common import (
    ColectivosServiceError,
    colectivos_zoho,
    get_colectivos_profile,
    sign_record_id,
    translate_zoho_error,
)
from cotizacion_colectivos.services.entity_detail import EntityDetailService
from cotizacion_colectivos.services.mappings import POLICIES_MODULE
from cotizacion_colectivos.services.policies import PolicyService
from cotizacion_colectivos.services.search import UnifiedClientSearchService
from integrations.zoho.exceptions import ZohoError

TASKS_MODULE = "Tasks"
TASK_SEARCH_FIELDS = (
    "id", "Subject", "Responsable", "Correo_responsable", "Estado",
    "tipo_de_solicitud", "rea", "Fecha_de_solicitud_del_cliente",
)
TASK_SEARCH_LIMIT = 20
MAX_QUERY_LENGTH = 120


def _clean_query(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > MAX_QUERY_LENGTH:
        raise ColectivosServiceError("invalid_query", "El criterio de búsqueda no es válido.")
    return clean


def buscar_cliente(query: str) -> dict[str, Any]:
    """Busca clientes (empresas o personas) por nombre o número de documento."""
    clean = _clean_query(query)
    resultados = UnifiedClientSearchService().search(clean)
    return {
        "total": len(resultados),
        "clientes": [
            {
                "tipo": item.entity_label,
                "nombre": item.display_name,
                "documento": f"{item.document_label} {item.masked_document}",
                "estado": item.state,
                "entity_kind": item.source_kind,
                "token": item.detail_token,
                "link": reverse(
                    "cotizacion_colectivos:client_detail",
                    kwargs={"entity_kind": item.source_kind, "token": item.detail_token},
                ),
            }
            for item in resultados
        ],
    }


def _entity_service() -> EntityDetailService:
    try:
        return EntityDetailService()
    except ZohoError as exc:
        raise translate_zoho_error(exc, get_colectivos_profile()) from exc


def detalle_cliente(entity_kind: str, token: str) -> dict[str, Any]:
    """Trae el detalle de un cliente ya localizado por `buscar_cliente` (recibe
    su `token` de link, no un ID de Zoho crudo)."""
    servicio = _entity_service()
    if entity_kind == "company":
        detalle = servicio.company(token)
        nombre = detalle.display_name
    elif entity_kind == "person":
        detalle = servicio.person(token)
        nombre = detalle.full_name
    else:
        raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.")
    return {
        "nombre": nombre,
        "documento": f"{detalle.id_type} {detalle.masked_document}",
        "estado": detalle.state,
        "polizas": [
            {
                "referencia": policy.masked_reference,
                "ramo": policy.branch,
                "aseguradora": policy.insurer,
                "estado": policy.state,
                "link": reverse("cotizacion_colectivos:policy_detail", kwargs={"token": policy.detail_token}),
            }
            for policy in (*detalle.policies, *detalle.direct_policies)
        ],
    }


def obtener_poliza(numero_poliza: str) -> dict[str, Any]:
    """Busca una póliza por su número (campo `Name` en Zoho) y devuelve su resumen."""
    clean = _clean_query(numero_poliza)
    zoho = colectivos_zoho()
    profile = get_colectivos_profile()
    try:
        pagina = zoho.search.by_field(
            module=POLICIES_MODULE, field="Name", value=clean,
            fields=("id", "Name"), page=1, limit=5,
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc, profile) from exc
    registros = [record for record in pagina.records if str(record.get("id") or "").strip()]
    if not registros:
        return {"encontrada": False}
    if len(registros) > 1:
        return {"encontrada": False, "motivo": "ambiguo", "coincidencias": len(registros)}
    policy_id = str(registros[0]["id"])
    token = sign_record_id(policy_id, "policy")
    try:
        detalle = PolicyService(zoho=zoho).detail(token)
    except ZohoError as exc:
        raise translate_zoho_error(exc, profile) from exc
    return {
        "encontrada": True,
        "referencia": detalle.masked_reference,
        "ramo": detalle.branch_name,
        "aseguradora": detalle.insurer,
        "estado": detalle.state,
        "tomador": detalle.holder,
        "vigencia_inicio": detalle.start_date,
        "vigencia_fin": detalle.end_date,
        "modo_de_pago": detalle.payment_mode,
        "frecuencia_de_pago": detalle.frequency,
        "asegurados_activos": detalle.active_count,
        "asegurados_excluidos": detalle.excluded_count,
        "asegurados_retirados": detalle.retired_count,
        "link": reverse("cotizacion_colectivos:policy_detail", kwargs={"token": detalle.detail_token}),
    }


def _buscar_tareas(campo: str, valor: str) -> dict[str, Any]:
    clean = _clean_query(valor)
    zoho = colectivos_zoho()
    try:
        pagina = zoho.search.by_field(
            module=TASKS_MODULE, field=campo, value=clean,
            fields=TASK_SEARCH_FIELDS, page=1, limit=TASK_SEARCH_LIMIT,
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc, get_colectivos_profile()) from exc
    tareas = [
        {
            "asunto": str(record.get("Subject") or "").strip(),
            "tipo": str(record.get("tipo_de_solicitud") or "").strip(),
            "area": str(record.get("rea") or "").strip(),
            "estado": str(record.get("Estado") or "Sin estado").strip(),
            "responsable": str(record.get("Responsable") or "").strip(),
            "fecha_solicitud": str(record.get("Fecha_de_solicitud_del_cliente") or "").strip(),
        }
        for record in pagina.records[:TASK_SEARCH_LIMIT]
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
