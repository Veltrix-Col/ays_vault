import base64
import json
import logging
import re
from urllib.parse import quote

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import ConciliacionUploadForm
from .ramos_ui import CAMPOS_ARCHIVO, RAMO_CHOICES, catalogo_novedades_api, catalogo_slots, slots_de_ramo
from .services import (
    CobroNotFound,
    CobroPrefillDisabled,
    CobroPrefillError,
    CobroPrefillNoData,
    ConciliacionProcessingError,
    procesar_conciliacion,
)
from .services import prellenar_cobro as _prellenar_cobro

logger = logging.getLogger("conciliacion")
_MAX_PREFILL_BODY_BYTES = 4096


def _contexto_base():
    ramo_inicial = RAMO_CHOICES[0][0]
    slots_iniciales = slots_de_ramo(ramo_inicial)
    return {
        "ramos": RAMO_CHOICES,
        "ramo_inicial": ramo_inicial,
        "slots_iniciales": slots_iniciales,
        "slots_iniciales_map": {slot["campo"]: slot for slot in slots_iniciales},
        "slots_catalog_json": json.dumps(catalogo_slots(), ensure_ascii=False),
        "novedades_api_catalog_json": json.dumps(catalogo_novedades_api(), ensure_ascii=False),
    }


@never_cache
@require_http_methods(["GET", "POST"])
def upload(request):
    form = ConciliacionUploadForm(request.POST or None, request.FILES or None)
    contexto = {**_contexto_base(), "form": form}

    if request.method == "POST" and form.is_valid():
        archivos = {campo: form.cleaned_data.get(campo) for campo in CAMPOS_ARCHIVO}
        try:
            resultado = procesar_conciliacion(
                ramo=form.cleaned_data["ramo"],
                poliza=form.cleaned_data["poliza"],
                archivos=archivos,
            )
        except ConciliacionProcessingError as exc:
            return render(request, "conciliacion/upload.html",
                          {**contexto, "processing_error": str(exc)}, status=422)
        except Exception:
            logger.exception("Fallo técnico durante la conciliación")
            return render(request, "conciliacion/upload.html", {
                **contexto,
                "processing_error": "No fue posible procesar la conciliación. "
                                    "Verifique los archivos e intente nuevamente.",
            }, status=500)

        response = HttpResponse(
            resultado.content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(resultado.filename)}"
        encoded = base64.urlsafe_b64encode(
            json.dumps(resultado.summary, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        response["X-Conciliacion-Summary"] = encoded
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    status = 422 if request.method == "POST" else 200
    return render(request, "conciliacion/upload.html", contexto, status=status)


_FECHA_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_str(value, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    texto = value.strip()
    return texto[:max_length] if texto else None


def _clean_fecha(value) -> str | None:
    texto = _clean_str(value, max_length=10)
    return texto if texto and _FECHA_ISO_RE.match(texto) else None


def _clean_monto(value) -> float | None:
    # bool es subclase de int en Python: se excluye explícitamente para que
    # un valor accidental true/false nunca se cuele como 1.0/0.0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@never_cache
@require_http_methods(["POST"])
def prellenar_cobro(request):
    """Prellena Certificado/Fecha expedición/Pago total cuota del Cobro en
    Zoho Producción antes de que "Facturar cobro" redirija ahí. Llamada desde
    JS (`fetch`) justo al hacer clic, con los valores del recibo (PDF) que ya
    llegaron al navegador en el summary de `/conciliador/`."""
    if len(request.body or b"") > _MAX_PREFILL_BODY_BYTES:
        return JsonResponse({"ok": False, "error": "Solicitud demasiado grande."}, status=400)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    poliza = _clean_str(payload.get("poliza"), max_length=60)
    cobro_id = _clean_str(payload.get("cobro_id"), max_length=40)
    if not poliza or not cobro_id:
        return JsonResponse({"ok": False, "error": "Se requiere poliza y cobro_id."}, status=400)

    try:
        resultado = _prellenar_cobro(
            poliza=poliza,
            cobro_id=cobro_id,
            certificado=_clean_str(payload.get("certificado"), max_length=255),
            fecha_expedicion=_clean_fecha(payload.get("fecha_expedicion")),
            pago_total_cuota=_clean_monto(payload.get("pago_total_cuota")),
        )
    except CobroPrefillDisabled as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=409)
    except CobroNotFound as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=404)
    except CobroPrefillNoData as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except CobroPrefillError as exc:
        logger.warning("No fue posible prellenar el cobro %s: %s", cobro_id, exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    except Exception:
        logger.exception("Fallo técnico al prellenar el cobro %s", cobro_id)
        return JsonResponse({"ok": False, "error": "No fue posible prellenar el cobro."}, status=500)

    response = JsonResponse({"ok": True, **resultado})
    response["Cache-Control"] = "no-store"
    return response
