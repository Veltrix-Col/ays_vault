import base64
import json
import logging
from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import ConciliacionUploadForm
from .ramos_ui import CAMPOS_ARCHIVO, RAMO_CHOICES, catalogo_novedades_api, catalogo_slots, slots_de_ramo
from .services import ConciliacionProcessingError, procesar_conciliacion

logger = logging.getLogger("conciliacion")


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
