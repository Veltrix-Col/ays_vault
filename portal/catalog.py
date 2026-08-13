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
    external_conciliacion_url = _safe_external_url(getattr(settings, "CONCILIACION_APP_URL", ""))
    conciliacion_url = external_conciliacion_url or reverse("conciliacion:index")
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
        {
            "name": "Conciliador de Facturación",
            "description": "Conciliación de relaciones de asegurados contra el cobro de la aseguradora, por ramo, con reporte de incidentes.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M4 4h16v4H4V4Zm0 6h16v10H4V10Zm3 3h4M7 16h7",
            "url": conciliacion_url,
            "active": True,
            "external": bool(external_conciliacion_url),
        },
        {
            "name": "Solicitudes y Renovaciones",
            "description": "Gestión digital de solicitudes, novedades y renovaciones de pólizas colectivas mediante enlace al cliente.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M4 5h16v14H4V5Zm4 4h8M8 13h5M6 9h.01M6 13h.01",
            "url": reverse("cotizacion_colectivos:requests_index"),
            "active": True,
            "external": False,
        },
        {
            "name": "Invitaciones a Aseguradoras",
            "description": "Generación automática de formatos de cotización e invitación para aseguradoras según el ramo de la póliza.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M3 6h18v12H3V6Zm3 3h12M6 12h8M6 15h5",
            "url": reverse("cotizacion_colectivos:invitations_index"),
            "active": True,
            "external": False,
        },
        {
            "name": "Cotización Individual",
            "description": "Captura estructurada de información para cotizar personas, asegurados y riesgos según el ramo.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M12 3v18M3 12h18M5 5l14 14M19 5 5 19",
            "url": reverse("cotizacion_colectivos:individual_index"),
            "active": True,
            "external": False,
        },
    ]
