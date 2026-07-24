from urllib.parse import urlparse

from django.conf import settings
from django.urls import reverse


def _safe_external_url(value):
    candidate = (value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def application_catalog():
    """Catálogo declarativo del portal; no contiene datos operativos."""
    soat_url = _safe_external_url(getattr(settings, "SOAT_APP_URL", ""))
    return [
        {
            "name": "A&S Vault",
            "description": "Gestión segura, controlada y auditable de tarjetas y operaciones protegidas.",
            "icon_path": "M12 3 5 6v5c0 4.6 2.9 8.4 7 10 4.1-1.6 7-5.4 7-10V6l-7-3Zm-3 9 2 2 4-4",
            "url": reverse("vault:dashboard"),
            "active": True,
            "external": False,
        },
        {
            "name": "Gestión SOAT",
            "description": "Validación, procesamiento y gestión de información asociada al proceso SOAT.",
            "icon_path": "M7 3h7l4 4v14H7V3Zm7 0v5h5M10 12h5M10 16h5",
            "url": soat_url,
            "active": bool(soat_url),
            "external": True,
        },
    ]
