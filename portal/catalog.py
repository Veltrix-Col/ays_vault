from urllib.parse import urlparse

from django.conf import settings
from django.urls import reverse


def _safe_external_url(value):
    candidate = (value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def application_catalog():
    """Catálogo declarativo del portal; no contiene datos operativos."""
    external_soat_url = _safe_external_url(getattr(settings, "SOAT_APP_URL", ""))
    soat_url = external_soat_url or reverse("soat:upload")
    return [
        {
            "name": "CardManager",
            "description": "Gestión segura, controlada y auditable de tarjetas y operaciones protegidas.",
            "logo": "img/branding/cardmanager/Logo-CardManager-COLOR.png",
            "logo_class": "application-logo--cardmanager",
            "icon_path": "",
            "url": reverse("vault:dashboard"),
            "active": True,
            "external": False,
        },
        {
            "name": "Gestión SOAT",
            "description": "Validación, procesamiento y gestión de información asociada al proceso SOAT.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M7 3h7l4 4v14H7V3Zm7 0v5h5M10 12h5M10 16h5",
            "url": soat_url,
            "active": True,
            "external": bool(external_soat_url),
        },
    ]
