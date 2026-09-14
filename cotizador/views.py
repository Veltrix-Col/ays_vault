"""Vistas del Cotizador de Renovaciones."""
from __future__ import annotations

import base64
import json
import logging
from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import CotizadorUploadForm
from .services import CotizadorProcessingError, generar_consolidado

logger = logging.getLogger("cotizador")


@never_cache
@require_http_methods(["GET", "POST"])
def upload(request):
    form = CotizadorUploadForm(request.POST or None, request.FILES or None)
    contexto = {"form": form}

    if request.method == "POST" and form.is_valid():
        try:
            resultado = generar_consolidado(
                ramo=form.cleaned_data["ramo"],
                nit=form.cleaned_data["nit"],
                actual=form.cleaned_data["actual"],
                otra_aseguradora=form.cleaned_data["otra_aseguradora"],
                archivos=form.cleaned_data["archivos"],
            )
        except CotizadorProcessingError as exc:
            return render(request, "cotizador/upload.html",
                          {**contexto, "processing_error": str(exc)}, status=422)
        except Exception:
            logger.exception("Fallo técnico al generar el consolidado")
            return render(request, "cotizador/upload.html", {
                **contexto,
                "processing_error": "No fue posible generar el consolidado. "
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
        response["X-Cotizador-Summary"] = encoded
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    status = 422 if request.method == "POST" else 200
    return render(request, "cotizador/upload.html", contexto, status=status)
