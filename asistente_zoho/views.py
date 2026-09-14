from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import orchestrator

logger = logging.getLogger("asistente_zoho")

MAX_HISTORY_ITEMS = 8
MAX_MESSAGE_LENGTH = 500


@login_required
@require_POST
@never_cache
def mensaje(request):
    """Un turno de la conversación corta. No hay estado en el servidor: el
    navegador manda el historial reciente en cada request y este endpoint no lo
    guarda en ningún lado (ni sesión, ni caché, ni base de datos)."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Solicitud inválida."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Solicitud inválida."}, status=400)

    mensaje_usuario = str(payload.get("mensaje", ""))[:MAX_MESSAGE_LENGTH]
    if not mensaje_usuario.strip():
        return JsonResponse({"error": "Escribe una pregunta."}, status=400)

    historial_bruto = payload.get("historial", [])
    if not isinstance(historial_bruto, list):
        historial_bruto = []
    historial = [
        {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
        for item in historial_bruto[-MAX_HISTORY_ITEMS:]
        if isinstance(item, dict)
    ]

    email_usuario = str(getattr(request.user, "email", "") or "")
    respuesta = orchestrator.responder(
        mensaje=mensaje_usuario, historial=historial, user_email=email_usuario,
    )
    return JsonResponse({"respuesta": respuesta})
