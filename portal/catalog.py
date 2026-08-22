from urllib.parse import urlparse

from django.conf import settings
from django.urls import reverse


AREA_DEFINITIONS = (
    {
        "slug": "cartera",
        "name": "Cartera",
        "description": "Herramientas para la gestión de cartera y tarjetas.",
        "icon_path": "M4 7h16v12H4V7Zm3-3h10v3H7V4Zm1 8h8M8 15h5",
        "search_terms": "cardmanager tarjetas vehículos cartera",
    },
    {
        "slug": "operaciones",
        "name": "Operaciones",
        "description": "Herramientas para la operación y procesamiento de información de negocio.",
        "icon_path": "M12 3 4 7v5c0 4.5 3.1 7.7 8 9 4.9-1.3 8-4.5 8-9V7l-8-4Zm0 5v5m-3-2h6",
        "search_terms": "operaciones soat procesamiento vehículos",
    },
    {
        "slug": "colectivos",
        "name": "Colectivos",
        "description": "Operación contextual de novedades, cotizaciones, invitaciones y conciliación.",
        "icon_path": "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8-1a2.5 2.5 0 1 0 0-5M3 20v-2a5 5 0 0 1 10 0v2m1-7a4 4 0 0 1 7 3v4",
        "search_terms": "novedades cotización individual invitaciones aseguradoras conciliador facturación",
    },
)


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
            "area": "Cartera",
            "description": "Gestión segura, controlada y auditable de tarjetas y operaciones protegidas.",
            "logo": "img/branding/cardmanager/Logo-CardManager-COLOR.png",
            "logo_class": "application-logo--cardmanager",
            "icon_path": "",
            "url": reverse("vault:dashboard"),
            "active": True,
            "external": False,
            "search_terms": "tarjetas seguridad control",
        },
        {
            "name": "SOAT",
            "area": "Operaciones",
            "description": "Validación, procesamiento y gestión de información asociada al proceso SOAT.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M7 3h7l4 4v14H7V3Zm7 0v5h5M10 12h5M10 16h5",
            "url": soat_url,
            "active": True,
            "external": bool(external_soat_url),
            "search_terms": "seguro obligatorio vehículos carga archivo",
        },
        {
            "name": "Novedades",
            "area": "Colectivos",
            "description": "Gestione ingresos y retiros desde el contexto confirmado del cliente, ramo y póliza.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M4 5h16v14H4V5Zm4 4h8M8 13h5M6 9h.01M6 13h.01",
            "url": reverse("cotizacion_colectivos:novelties_index"),
            "active": True,
            "external": False,
            "search_terms": "ingresos retiros pólizas clientes afiliados asegurados",
        },
        {
            "name": "Cotización Individual",
            "area": "Colectivos",
            "description": "Genere solicitudes individuales contextualizadas por cliente, ramo, póliza y asegurado.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M7 3h7l4 4v14H7V3Zm7 0v5h5M10 12h5M10 16h5",
            "url": reverse("cotizacion_colectivos:individual_index"),
            "active": True,
            "external": False,
            "search_terms": "cotizar enlace persona afiliado asegurado",
        },
        {
            "name": "Invitaciones a Aseguradoras",
            "area": "Colectivos",
            "description": "Revise y genere formatos de invitación según el cliente, ramo y contexto de póliza.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M3 6h18v12H3V6Zm1 1 8 6 8-6",
            "url": reverse("cotizacion_colectivos:invitations_index"),
            "active": True,
            "external": False,
            "search_terms": "formatos plantillas compañías invitación cotización",
        },
        {
            "name": "Conciliador de Facturación",
            "area": "Colectivos",
            "description": "Concilie relaciones de asegurados contra el cobro de la aseguradora, por ramo.",
            "logo": "",
            "logo_class": "",
            "icon_path": "M4 4h16v4H4V4Zm0 6h16v10H4V10Zm3 3h4M7 16h7",
            "url": conciliacion_url,
            "active": True,
            "external": bool(external_conciliacion_url),
            "search_terms": "conciliación facturas asegurados cobros",
        },
    ]


def application_areas():
    """Agrupa el catálogo sin cambiar las rutas ni el comportamiento de cada módulo."""

    grouped = {}
    for application in application_catalog():
        grouped.setdefault(application["area"], []).append(application)
    return tuple({"name": name, "applications": tuple(items)} for name, items in grouped.items())


def area_catalog():
    """Primer nivel del Banco: paquetes de área, nunca herramientas redundantes."""

    applications = application_catalog()
    areas = []
    for definition in AREA_DEFINITIONS:
        area = dict(definition)
        area["url"] = reverse("area_home", kwargs={"area_slug": area["slug"]})
        area["applications"] = tuple(
            app for app in applications if app["area"] == area["name"]
        )
        areas.append(area)
    return tuple(areas)


def get_area(area_slug):
    normalized = (area_slug or "").strip().lower()
    area = next((area for area in area_catalog() if area["slug"] == normalized), None)
    if area is not None and normalized == "colectivos":
        area = {
            **area,
            "operational_center": {
                "name": "Bandeja de solicitudes",
                "description": (
                    "Revise respuestas, enlaces y expedientes que requieren gestión "
                    "del equipo de Colectivos."
                ),
                "url": reverse("cotizacion_colectivos:request_list"),
            },
        }
    return area
