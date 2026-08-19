import base64
import json
import logging
from uuid import uuid4
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import SoatUploadForm
from .services import SoatProcessingError, process_soat

logger = logging.getLogger(__name__)


def _multipart_xlsx(parts: tuple[tuple[str, str, bytes], ...]) -> tuple[bytes, str]:
    boundary = f"----ays-soat-{uuid4().hex}"
    chunks: list[bytes] = []
    for field, filename, content in parts:
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n",
            b"\r\n",
            content,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def _safe_zoho_url():
    value = settings.SOAT_ZOHO_REPORT_URL
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


@never_cache
@require_http_methods(["GET", "POST"])
def upload(request):
    form = SoatUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = process_soat(form.cleaned_data["source_file"])
        except SoatProcessingError as exc:
            return render(request, "soat/upload.html", {
                "form": form,
                "zoho_url": _safe_zoho_url(),
                "processing_error": str(exc),
            }, status=422)
        except Exception:
            logger.exception("Fallo técnico seguro durante el procesamiento SOAT")
            return render(request, "soat/upload.html", {
                "form": form,
                "zoho_url": _safe_zoho_url(),
                "processing_error": "No fue posible procesar el archivo. Verifique su estructura e intente nuevamente.",
            }, status=500)

        body, boundary = _multipart_xlsx((
            ("report", result.report_filename, result.report_content),
            ("support", result.support_filename, result.support_content),
        ))
        response = HttpResponse(body, content_type=f"multipart/form-data; boundary={boundary}")
        encoded = base64.urlsafe_b64encode(
            json.dumps(result.summary, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        response["X-SOAT-Summary"] = encoded
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response
    return render(request, "soat/upload.html", {
        "form": form,
        "zoho_url": _safe_zoho_url(),
    }, status=422 if request.method == "POST" else 200)
