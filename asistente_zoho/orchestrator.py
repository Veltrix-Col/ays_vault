"""Orquesta un turno de la conversación corta del asistente: arma el prompt, expone
las tools de solo lectura al modelo y ejecuta el loop de tool-calling de
`integrations.llm.LLM.conversar`. No persiste nada -- ver `views.py`."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from cotizacion_colectivos.services.common import ColectivosServiceError
from integrations.llm import LLM

from . import tools

logger = logging.getLogger("asistente_zoho")

SYSTEM_PROMPT = (
    "Eres el asistente de consulta de AyS Vault. Respondes en español, de forma breve, "
    "clara y ordenada, sólo con datos que hayas obtenido de las herramientas -- nunca "
    "inventes nombres, números de póliza, fechas ni estados. Si una herramienta no "
    "encuentra nada o falla, dilo con honestidad y sugiere reformular la búsqueda. "
    "Cuando una herramienta te dé un \"link\", inclúyelo como enlace Markdown "
    "(`[texto](link)`) para que la persona pueda abrir el registro directamente en el "
    "portal; nunca inventes un link que no venga de una herramienta. No hagas ni "
    "sugieras cambios en Zoho: sólo puedes consultar información, nunca crearla, "
    "editarla ni eliminarla."
)

MAX_MESSAGE_LENGTH = 500
MAX_HISTORY_MESSAGES = 8

TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "buscar_cliente",
        "description": "Busca clientes (empresas o personas) en Zoho por nombre o número de documento.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Nombre o número de documento a buscar."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "detalle_cliente",
        "description": (
            "Trae el detalle completo (pólizas incluidas) de un cliente ya localizado "
            "con buscar_cliente. Requiere el entity_kind y token que devolvió esa búsqueda."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_kind": {"type": "string", "enum": ["company", "person"]},
                "token": {"type": "string"},
            },
            "required": ["entity_kind", "token"],
        },
    },
    {
        "name": "obtener_poliza",
        "description": (
            "Busca una póliza por su número exacto y devuelve su resumen "
            "(vigencia, aseguradora, estado, conteo de asegurados)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero_poliza": {"type": "string", "description": "Número de póliza (campo Name en Zoho)."},
            },
            "required": ["numero_poliza"],
        },
    },
    {
        "name": "mis_tareas",
        "description": "Lista las tareas (Tasks) asignadas a la persona que está usando el chat en este momento.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "tareas_por_responsable",
        "description": "Lista las tareas (Tasks) asignadas a otra persona, buscando por su nombre.",
        "parameters": {
            "type": "object",
            "properties": {
                "nombre_responsable": {
                    "type": "string",
                    "description": "Nombre del responsable tal como aparece en Zoho.",
                },
            },
            "required": ["nombre_responsable"],
        },
    },
)


def _tool_error(exc: Exception) -> str:
    if isinstance(exc, ColectivosServiceError):
        return json.dumps({"error": exc.message}, ensure_ascii=False)
    logger.exception("asistente_zoho_tool_error")
    return json.dumps({"error": "No fue posible completar esa consulta en este momento."}, ensure_ascii=False)


def build_tool_executor(*, user_email: str) -> Callable[[str, dict[str, Any]], str]:
    """Ata cada tool a su función real. `mis_tareas` siempre usa `user_email` del
    request autenticado -- nunca un valor que el modelo pudiera inventar en
    `argumentos` -- para que "mis tareas" no pueda usarse para leer las de otra
    persona con solo pedírselo al modelo."""

    def ejecutar(nombre: str, argumentos: dict[str, Any]) -> str:
        try:
            if nombre == "buscar_cliente":
                resultado = tools.buscar_cliente(str(argumentos.get("query", "")))
            elif nombre == "detalle_cliente":
                resultado = tools.detalle_cliente(
                    str(argumentos.get("entity_kind", "")), str(argumentos.get("token", "")),
                )
            elif nombre == "obtener_poliza":
                resultado = tools.obtener_poliza(str(argumentos.get("numero_poliza", "")))
            elif nombre == "mis_tareas":
                resultado = tools.mis_tareas(user_email)
            elif nombre == "tareas_por_responsable":
                resultado = tools.tareas_por_responsable(str(argumentos.get("nombre_responsable", "")))
            else:
                return json.dumps({"error": "Herramienta desconocida."}, ensure_ascii=False)
        except Exception as exc:  # nunca debe propagar: ver contrato de LLM.conversar
            return _tool_error(exc)
        return json.dumps(resultado, ensure_ascii=False)

    return ejecutar


def responder(*, mensaje: str, historial: list[dict[str, str]], user_email: str) -> str:
    """Resuelve un turno de la conversación corta. `historial` es lo que ya se dijo en
    esta ventana de chat (recortado por quien llama); esta función no persiste nada
    -- ni el mensaje ni la respuesta se guardan en el servidor."""
    mensaje_limpio = str(mensaje or "").strip()[:MAX_MESSAGE_LENGTH]
    if not mensaje_limpio:
        return "Escribe una pregunta para poder ayudarte."
    mensajes = [
        {"role": item.get("role"), "content": str(item.get("content", ""))[:MAX_MESSAGE_LENGTH]}
        for item in historial[-MAX_HISTORY_MESSAGES:]
        if item.get("role") in {"user", "assistant"}
    ]
    mensajes.append({"role": "user", "content": mensaje_limpio})
    llm = LLM()
    try:
        respuesta = llm.conversar(
            system=SYSTEM_PROMPT, mensajes=mensajes,
            herramientas=[dict(spec) for spec in TOOL_SPECS],
            ejecutar_tool=build_tool_executor(user_email=user_email),
        )
    except NotImplementedError:
        raise
    except Exception:
        logger.exception("asistente_zoho_conversar_error")
        return "No fue posible consultar el asistente en este momento. Intenta de nuevo en unos minutos."
    return respuesta or "No tengo una respuesta para eso todavía."
