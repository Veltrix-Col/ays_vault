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
    "email_exceptions": "email_exceptions",
}

# Endpoints machine-to-machine are declared by resolved URL identity, not by
# a path prefix.  This keeps the exception explicit and prevents the rest of
# an inherited-access namespace from bypassing the intranet gate.
M2M_URLS = frozenset({("email_exceptions", "inbound")})


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


def is_m2m_path(path_info: str) -> bool:
    """Return whether a resolved URL is explicitly independent of human SSO."""

    try:
        match = resolve(path_info)
    except Resolver404:
        return False
    return (match.namespace, match.url_name) in M2M_URLS
