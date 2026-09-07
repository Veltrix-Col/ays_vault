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


# Presentation-only descriptions.  The active values themselves always come
# from Contacts.Tipo_ID in Zoho; unknown values intentionally remain visible
# as their raw code until A&S confirms a description.
DOCUMENT_TYPE_LABELS = {
    "CC": "Cédula de ciudadanía",
    "CE": "Cédula de extranjería",
    "NIT": "Número de Identificación Tributaria",
    "NUIP": "Número Único de Identificación Personal",
    "PAS": "Pasaporte",
    "PEP": "Permiso Especial de Permanencia",
    "RC": "Registro civil",
    "TI": "Tarjeta de identidad",
}


def identification_display_label(value: object) -> str:
    code = str(value or "").strip()
    if not code:
        return ""
    description = DOCUMENT_TYPE_LABELS.get(code.upper())
    return f"{code} - {description}" if description else code


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
        empty_tokens = {"", "none", "-none-", "null", "undefined", "-"}
        if not value or active is False or value.casefold() in empty_tokens or label.casefold() in empty_tokens:
            continue
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            sequence = index
        choices.append(CatalogChoice(value=value, label=identification_display_label(value), sequence=sequence))
    if not choices:
        raise CatalogUnavailable("Zoho no tiene opciones activas para Tipo_ID.")
    return tuple(sorted(choices, key=lambda item: (item.sequence, item.value.casefold())))


def identification_choice_pairs(*, facade=None) -> tuple[tuple[str, str], ...]:
    return tuple((item.value, item.label) for item in get_identification_type_choices(facade=facade))


def identification_type_values(*, facade=None) -> frozenset[str]:
    return frozenset(item.value for item in get_identification_type_choices(facade=facade))


def get_subrisk_relationship_choices(*, facade=None) -> tuple[CatalogChoice, ...]:
    """Return active Riesgos1.Parentesco values from Zoho metadata."""
    try:
        facade = facade or get_colectivos_zoho()
        fields = cached_metadata_fields(facade, "Riesgos1")
    except ZohoError as exc:
        raise CatalogUnavailable("El catálogo de parentescos no está disponible.") from exc
    field = next((item for item in fields if getattr(item, "api_name", "") == "Parentesco"), None)
    if field is None:
        raise CatalogUnavailable("Zoho no expone el catálogo de parentescos.")
    choices = []
    for index, option in enumerate(getattr(field, "pick_list_values", ()) or ()):
        if isinstance(option, dict):
            value = str(option.get("actual_value") or option.get("display_value") or "").strip()
            label = str(option.get("display_value") or value).strip()
            active = option.get("active", True)
            sequence = option.get("sequence_number", index)
        else:
            value = str(getattr(option, "actual_value", "") or getattr(option, "display_value", "") or "").strip()
            label = str(getattr(option, "display_value", "") or value).strip()
            active = getattr(option, "active", True)
            sequence = getattr(option, "sequence_number", index)
        empty_tokens = {"", "none", "null", "undefined", "-none-", "-"}
        if not value or active is False or value.casefold() in empty_tokens or label.casefold() in empty_tokens:
            continue
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            sequence = index
        choices.append(CatalogChoice(value=value, label=label, sequence=sequence))
    if not choices:
        raise CatalogUnavailable("Zoho no tiene opciones activas para Parentesco.")
    return tuple(sorted(choices, key=lambda item: (item.sequence, item.value.casefold())))


def subrisk_relationship_choice_pairs(*, facade=None) -> tuple[tuple[str, str], ...]:
    return tuple((item.value, item.label) for item in get_subrisk_relationship_choices(facade=facade))
