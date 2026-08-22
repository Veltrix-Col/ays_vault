from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.urls import Resolver404, resolve


LOCAL_PUBLIC = "local_public"
TRUSTED_INTRANET = "trusted_intranet"
ALLOWED_ACCESS_MODES = frozenset({LOCAL_PUBLIC, TRUSTED_INTRANET})

INHERITED_NAMESPACES = {
    "soat": "soat",
    "conciliacion": "conciliacion",
    "cotizacion_colectivos": "cotizacion_colectivos",
}


@dataclass(frozen=True)
class DelegatedAccessResult:
    allowed: bool
    category: str
    challenge_redirect: str | None = None
    subject: str | None = None


class DelegatedAccessValidator(Protocol):
    def __call__(self, *, request, application: str) -> DelegatedAccessResult: ...


def inherited_application_for_path(path_info: str) -> str | None:
    """Classify only URL patterns explicitly owned by inherited-access apps."""

    try:
        match = resolve(path_info)
    except Resolver404:
        return None
    if match.namespace in INHERITED_NAMESPACES:
        return INHERITED_NAMESPACES[match.namespace]
    if not match.namespace and match.url_name == "public_home":
        return "portal"
    return None
