"""Orquesta un turno de la conversación corta del asistente: arma el prompt, expone
las tools de solo lectura al modelo y ejecuta el loop de tool-calling de
`integrations.llm.LLM.conversar`. No persiste nada -- ver `views.py`."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from cotizacion_colectivos.services.common import ColectivosServiceError
from integrations.llm import LLM

from . import tools

logger = logging.getLogger("asistente_zoho")

# Deployment propio de este flujo -- no comparte AZURE_FOUNDRY_COTIZACIONES_MODEL
# (el de gestor_cotizaciones) para que repuntar/limitar ese deployment para
# extracción por lotes no rompa silenciosamente el chat. Si no está configurado,
# LLM() cae de vuelta a AZURE_FOUNDRY_COTIZACIONES_MODEL (mismo comportamiento
# que ya se probó en vivo) hasta que se configure uno dedicado.
_MODEL_ENV_VAR = "AZURE_FOUNDRY_ASISTENTE_MODEL"

SYSTEM_PROMPT = (
    "Eres el asistente general de consulta de Zoho del Banco de Aplicaciones de AyS "
    "Vault -- no perteneces a ninguna app en particular (ni Colectivos, ni SOAT, "
    "ni otra); cualquier persona que haya iniciado sesión en el portal puede "
    "preguntarte por clientes, pólizas o tareas de cualquier área del negocio. "
    "Respondes en español, de forma breve, clara y ordenada, sólo con datos que "
    "hayas obtenido de las herramientas -- nunca inventes nombres, números de "
    "póliza, fechas ni estados. Antes de llamar una herramienta, extrae del mensaje "
    "solo el dato relevante (nombre, número de documento o número de póliza): si la "
    "persona escribe una frase completa con saludo, cédula y nombre mezclados, igual "
    "puedes pasar esa frase tal cual a buscar_cliente porque ya sabe separar nombre "
    "de documento, pero nunca le pases una pregunta genérica sin ningún dato "
    "identificador. Si una herramienta no encuentra nada o falla, dilo con "
    "honestidad y sugiere reformular la búsqueda -- nunca asumas que un resultado "
    "vacío es un error. Cuando una herramienta te dé un \"link\", inclúyelo como "
    "enlace Markdown (`[texto](link)`) para que la persona pueda abrir el registro "
    "directamente en Zoho; nunca inventes un link que no venga de una herramienta ni "
    "muestres la URL cruda. No hagas ni sugieras cambios en Zoho: sólo puedes "
    "consultar información, nunca crearla, editarla ni eliminarla."
)

MAX_MESSAGE_LENGTH = 500
MAX_HISTORY_MESSAGES = 8

TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "buscar_cliente",
        "description": (
            "Busca clientes (personas o empresas, cualquier tipo de documento) en Zoho "
            "por nombre y/o número de documento. Acepta texto natural con ambos mezclados "
            "(p. ej. \"Juan Pérez cc 123456\" o un saludo con la pregunta incluida) -- "
            "separa nombre de documento internamente, no hace falta limpiarlo antes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Nombre y/o número de documento, en lenguaje natural."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "detalle_cliente",
        "description": (
            "Trae el detalle completo (pólizas incluidas) de un cliente ya localizado "
            "con buscar_cliente. Requiere el `id` que devolvió esa búsqueda."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "description": "El campo id devuelto por buscar_cliente."},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "obtener_poliza",
        "description": "Busca una póliza por su número exacto y devuelve su resumen (tomador, vigencia, aseguradora, estado).",
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
                resultado = tools.detalle_cliente(str(argumentos.get("record_id", "")))
            elif nombre == "obtener_poliza":
                resultado = tools.obtener_poliza(str(argumentos.get("numero_poliza", "")))
            elif nombre == "mis_tareas":
                if not user_email:
                    # Usuarios provisionados por intranet_sso pueden no tener
                    # correo (get_or_create_intranet_user solo lo llena si el
                    # subject tiene forma de correo) -- sin esto, tools.mis_tareas("")
                    # daría el genérico "criterio de búsqueda no válido", que no dice
                    # nada sobre la causa real.
                    resultado = {"error": "Tu sesión no tiene un correo asociado, así que no puedo buscar tus tareas."}
                else:
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
    llm = LLM(model=os.getenv(_MODEL_ENV_VAR, "").strip() or None)
    try:
        respuesta = llm.conversar(
            system=SYSTEM_PROMPT, mensajes=mensajes,
            herramientas=[dict(spec) for spec in TOOL_SPECS],
            ejecutar_tool=build_tool_executor(user_email=user_email),
        )
    except NotImplementedError:
        # Config compartida entre features (GESTOR_LLM_PROVIDER): que el cotizador
        # necesite otro proveedor no puede tumbar el chat en todo el portal con un
        # 500 sin explicación -- se degrada con un mensaje claro y se deja rastro
        # en logs para que operación lo note.
        logger.error("asistente_zoho_provider_no_soportado provider=%s", llm.provider)
        return "El asistente no está disponible con la configuración actual. Avisa a soporte."
    except Exception:
        logger.exception("asistente_zoho_conversar_error")
        return "No fue posible consultar el asistente en este momento. Intenta de nuevo en unos minutos."
    return respuesta or "No tengo una respuesta para eso todavía."
