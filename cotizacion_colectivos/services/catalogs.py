from __future__ import annotations

from dataclasses import dataclass

from integrations.zoho.exceptions import ZohoError

from ..zoho import cached_metadata_fields, get_colectivos_zoho


class CatalogUnavailable(RuntimeError):
    """The authoritative Zoho catalog could not be loaded safely."""


@dataclass(frozen=True)
class CatalogChoice:
    value: str
    label: str
    sequence: int


def get_identification_type_choices(*, facade=None) -> tuple[CatalogChoice, ...]:
    """Return active Contacts.Tipo_ID options using Zoho API values."""

    try:
        facade = facade or get_colectivos_zoho()
        fields = cached_metadata_fields(facade, "Contacts")
    except ZohoError as exc:
        raise CatalogUnavailable("El catálogo de identificación no está disponible.") from exc
    field = next((item for item in fields if getattr(item, "api_name", "") == "Tipo_ID"), None)
    if field is None:
        raise CatalogUnavailable("Zoho no expone el catálogo de tipos de identificación.")

    choices = []
    for index, option in enumerate(getattr(field, "pick_list_values", ()) or ()):
        if isinstance(option, dict):
            value = str(option.get("actual_value") or "").strip()
            label = str(option.get("display_value") or value).strip()
            active = option.get("active", True)
            sequence = option.get("sequence_number", index)
        else:
            value = str(getattr(option, "actual_value", "") or "").strip()
            label = str(getattr(option, "display_value", "") or value).strip()
            active = getattr(option, "active", True)
            sequence = getattr(option, "sequence_number", index)
        if not value or active is False:
            continue
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            sequence = index
        choices.append(CatalogChoice(value=value, label=label or value, sequence=sequence))
    if not choices:
        raise CatalogUnavailable("Zoho no tiene opciones activas para Tipo_ID.")
    return tuple(sorted(choices, key=lambda item: (item.sequence, item.value.casefold())))


def identification_choice_pairs(*, facade=None) -> tuple[tuple[str, str], ...]:
    return tuple((item.value, item.label) for item in get_identification_type_choices(facade=facade))


def identification_type_values(*, facade=None) -> frozenset[str]:
    return frozenset(item.value for item in get_identification_type_choices(facade=facade))
