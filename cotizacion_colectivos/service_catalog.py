"""Catálogo de capacidades sobre ramos confirmados; no contiene nombres API de Zoho."""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import reverse

from .invitation_templates.catalog import templates_for_branch


def _normalized(value: object) -> str:
    import unicodedata
    return "".join(
        character for character in unicodedata.normalize("NFKD", str(value or "").strip().casefold())
        if not unicodedata.combining(character)
    )


def _is_operable_policy(policy) -> bool:
    state = _normalized(policy.state)
    return state in {"vigente", "activa", "activo"} or (
        state in {"vencida", "vencido"}
        and _normalized(policy.renewable) in {"si", "true", "1", "renovable"}
    )


@dataclass(frozen=True)
class CollectiveService:
    code: str
    name: str
    description: str
    requires_policy: bool
    policy_route: str


CORE_SERVICES = (
    CollectiveService(
        "novelties", "Novedades",
        "Registrar un ingreso o retiro con el contexto confirmado de la póliza.",
        True, "cotizacion_colectivos:novelties_policy_detail",
    ),
    CollectiveService(
        "individual", "Cotización individual",
        "Solicitar únicamente la información adicional necesaria para cotizar el ramo.",
        True, "cotizacion_colectivos:individual_policy_detail",
    ),
)


def services_for_branch(branch_code: str) -> tuple[CollectiveService, ...]:
    services = list(CORE_SERVICES)
    if templates_for_branch(str(branch_code), active_only=True):
        services.append(CollectiveService(
            "invitations", "Invitaciones y formatos",
            "Revisar y descargar formatos activos del ramo desde el Workspace local.",
            True, "cotizacion_colectivos:invitations_policy_detail",
        ))
    return tuple(services)


def branch_workspaces(branches, *, service_code: str | None = None) -> tuple[dict[str, object], ...]:
    """Prepara navegación segura sin aceptar rutas o modos desde el navegador."""

    allowed_service = service_code if service_code in {"novelties", "individual", "invitations"} else None
    workspaces = []
    for branch in branches:
        services = []
        for service in services_for_branch(branch.code):
            if allowed_service and service.code != allowed_service:
                continue
            policies = tuple({
                "reference": policy.full_reference or policy.masked_reference,
                "state": policy.state,
                "insurer": policy.insurer,
                "start_date": policy.start_date,
                "end_date": policy.end_date,
                "operable": _is_operable_policy(policy),
                "url": reverse(service.policy_route, args=[policy.detail_token]),
            } for policy in branch.policies)
            services.append({
                "service": service,
                "policies": policies,
                "operable_policies": tuple(item for item in policies if item["operable"]),
                "secondary_policies": tuple(item for item in policies if not item["operable"]),
            })
        workspaces.append({"branch": branch, "services": tuple(services)})
    return tuple(workspaces)
