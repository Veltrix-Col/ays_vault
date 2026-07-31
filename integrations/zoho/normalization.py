from __future__ import annotations

import re

from .exceptions import ZohoInvalidResponseError

ORGANIZATION_ENVIRONMENTS = frozenset(
    {"production", "sandbox", "developer", "bigin"}
)
SAFE_CLASS_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def normalize_choice_text(
    value: object,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    """Extract text only from supported SDK/REST scalar representations."""
    current = value
    for _ in range(3):
        if isinstance(current, str):
            normalized = current.strip()
            if normalized or allow_empty:
                return normalized
            break
        if current is None:
            if allow_empty:
                return ""
            break
        getter = getattr(current, "get_value", None)
        if callable(getter):
            try:
                candidate = getter()
            except Exception:
                break
        elif hasattr(current, "value"):
            try:
                candidate = getattr(current, "value")
            except Exception:
                break
        else:
            break
        if candidate is current:
            break
        current = candidate
    raise ZohoInvalidResponseError(
        f"Zoho no informo un valor valido para {field}."
    )


def normalize_organization_environment(value: object) -> str:
    normalized = normalize_choice_text(value, field="organization.type").lower()
    if normalized not in ORGANIZATION_ENVIRONMENTS:
        raise ZohoInvalidResponseError(
            "Zoho informo un tipo de entorno de organizacion desconocido."
        )
    return normalized


def safe_value_class(value: object) -> str:
    name = value.__class__.__name__ if value is not None else "NoneType"
    return SAFE_CLASS_NAME.sub("", name)[:80] or "unknown"
