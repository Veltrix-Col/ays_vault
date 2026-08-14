from __future__ import annotations

from dataclasses import dataclass

from django.http import Http404


NOVELTIES_MODE = "novelties"
# Alias técnico temporal para imports históricos. La experiencia vigente y la
# sesión usan exclusivamente ``novelties``.
REQUESTS_MODE = NOVELTIES_MODE
INVITATIONS_MODE = "invitations"
INDIVIDUAL_MODE = "individual"


@dataclass(frozen=True)
class ToolMode:
    code: str
    slug: str
    name: str
    short_name: str
    description: str
    primary_action: str


TOOL_MODES = {
    REQUESTS_MODE: ToolMode(
        code=REQUESTS_MODE,
        slug="novedades",
        name="Novedades",
        short_name="Novedades",
        description=(
            "Capture ingresos y retiros en pólizas colectivas mediante un "
            "enlace contextualizado para el cliente."
        ),
        primary_action="Generar enlace",
    ),
    INVITATIONS_MODE: ToolMode(
        code=INVITATIONS_MODE,
        slug="invitaciones-aseguradoras",
        name="Invitaciones a Aseguradoras",
        short_name="Invitaciones",
        description=(
            "Generación automática de formatos de cotización e invitación para "
            "aseguradoras según el ramo de la póliza."
        ),
        primary_action="Descargar plantillas de invitación",
    ),
    INDIVIDUAL_MODE: ToolMode(
        code=INDIVIDUAL_MODE,
        slug="cotizacion-individual",
        name="Cotización Individual",
        short_name="Cotización individual",
        description=(
            "Captura estructurada de información para cotizar personas, "
            "asegurados y riesgos según el ramo."
        ),
        primary_action="Crear cotización individual",
    ),
}
MODES_BY_SLUG = {item.slug: item for item in TOOL_MODES.values()}
LEGACY_MODES = {
    "requests": NOVELTIES_MODE,
    "solicitudes-renovaciones": NOVELTIES_MODE,
}
SESSION_KEY = "colectivos_tool_mode"


def resolve_tool_mode(request, value: str | None = None) -> ToolMode:
    """Resolve an allowlisted UX mode; it never changes data or CRM scope."""

    if value is not None:
        normalized = LEGACY_MODES.get(value, value)
        mode = TOOL_MODES.get(normalized) or MODES_BY_SLUG.get(normalized)
        if mode is None:
            raise Http404("Herramienta no disponible")
        request.session[SESSION_KEY] = mode.code
        return mode
    stored_value = request.session.get(SESSION_KEY, NOVELTIES_MODE)
    stored = LEGACY_MODES.get(stored_value, stored_value)
    return TOOL_MODES.get(stored, TOOL_MODES[REQUESTS_MODE])
