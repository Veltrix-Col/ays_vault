from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from integrations.zoho.exceptions import ZohoError

from .common import colectivos_zoho, translate_zoho_error
from ..zoho import cached_metadata_fields


@dataclass(frozen=True)
class TaskResponsibleOption:
    actual_value: str
    display_value: str


def _value(item, key: str) -> str:
    if isinstance(item, dict):
        return str(item.get(key) or "").strip()
    return str(getattr(item, key, "") or "").strip()


def task_responsible_options(*, zoho=None) -> tuple[TaskResponsibleOption, ...]:
    """Return the confirmed Tasks.Responsable picklist, never a local list."""
    try:
        facade = zoho or colectivos_zoho()
        field = next(
            (item for item in cached_metadata_fields(facade, "Tasks")
             if str(getattr(item, "api_name", "")) == "Responsable"),
            None,
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc) from exc
    if field is None:
        raise ValidationError("No fue posible cargar los responsables confirmados de Zoho.")
    options = []
    seen = set()
    for item in getattr(field, "pick_list_values", ()) or ():
        actual = _value(item, "actual_value") or _value(item, "display_value")
        display = _value(item, "display_value") or actual
        if (
            actual
            and display
            and actual not in {"-None-", "None"}
            and display not in {"-None-", "None"}
            and actual not in seen
        ):
            seen.add(actual)
            options.append(TaskResponsibleOption(actual, display))
    if not options:
        raise ValidationError("No hay responsables disponibles para seleccionar.")
    return tuple(options)


def resolve_task_responsible_email(option: TaskResponsibleOption, *, zoho=None) -> str:
    """Resolve one exact Empleados record; ambiguity or missing email blocks safely."""
    try:
        facade = zoho or colectivos_zoho()
        records = []
        for candidate in (option.actual_value, option.display_value):
            if not candidate:
                continue
            page = facade.search.by_field(
                module="Empleados", field="Name", value=candidate,
                fields=("id", "Name", "Email"), page=1, limit=2,
            )
            records.extend(page.records)
    except ZohoError as exc:
        raise translate_zoho_error(exc) from exc
    unique = {str(record.get("id")): record for record in records if record.get("id")}
    matches = [
        record for record in unique.values()
        if str(record.get("Name") or "").strip() in {option.actual_value, option.display_value}
    ]
    if len(matches) != 1:
        raise ValidationError(
            "No fue posible asociar el responsable seleccionado con un correo corporativo en Zoho."
        )
    email = str(matches[0].get("Email") or "").strip()
    if "@" not in email or len(email) > 254:
        raise ValidationError(
            "No fue posible asociar el responsable seleccionado con un correo corporativo en Zoho."
        )
    return email
