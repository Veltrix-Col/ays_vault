from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from django.core.exceptions import ValidationError

from integrations.zoho.exceptions import ZohoError

from .common import colectivos_zoho, translate_zoho_error
from ..zoho import cached_metadata_fields


# Confirmed by the Sandbox Empleados metadata snapshot (text field labelled
# “Cargo”). Keep this explicit so the operational filter does not drift toward
# similarly named fields such as Cargo_ocupaci_n_u_oficio.
EMPLOYEE_CARGO_FIELD = "Cargo"


@dataclass(frozen=True)
class TaskResponsibleOption:
    actual_value: str
    display_value: str


def _value(item, key: str) -> str:
    if isinstance(item, dict):
        return str(item.get(key) or "").strip()
    return str(getattr(item, key, "") or "").strip()


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def task_responsible_options(*, zoho=None, collective_only=False) -> tuple[TaskResponsibleOption, ...]:
    """Return confirmed responsible options, optionally restricted to Colectivos cargo."""
    try:
        facade = zoho or colectivos_zoho()
        if collective_only:
            try:
                records = []
                for page_number in range(1, 4):
                    page = facade.records.list(
                        module="Empleados",
                        fields=("id", "Name", EMPLOYEE_CARGO_FIELD, "Estado"),
                        page=page_number,
                        limit=200,
                    )
                    records.extend(getattr(page, "records", ()) or ())
                    if not getattr(page, "more_records", False):
                        break
            except Exception as exc:
                if isinstance(exc, ZohoError):
                    raise translate_zoho_error(exc) from exc
                raise ValidationError("No fue posible cargar los responsables del área Colectivos.") from exc
            options = []
            seen = set()
            for record in records:
                cargo = _fold(_value(record, EMPLOYEE_CARGO_FIELD))
                actual = _value(record, "Name") or _value(record, "id")
                display = _value(record, "Name") or actual
                if "colectiv" not in cargo or not actual or actual in seen:
                    continue
                if _fold(_value(record, "Estado")) not in {"", "activo", "active"}:
                    continue
                seen.add(actual)
                options.append(TaskResponsibleOption(actual, display))
            if not options:
                raise ValidationError("No fue posible cargar los responsables del área Colectivos.")
            return tuple(options)
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
                fields=("id", "Name", "Email", "Estado"), page=1, limit=5,
            )
            records.extend(page.records)
    except ZohoError as exc:
        raise translate_zoho_error(exc) from exc
    unique = {str(record.get("id")): record for record in records if record.get("id")}
    wanted = {_fold(option.actual_value), _fold(option.display_value)} - {""}
    matches = [
        record for record in unique.values()
        if _fold(str(record.get("Name") or "")) in wanted
    ]
    active = [record for record in matches if _fold(str(record.get("Estado") or "")) == "activo"]
    if len(active) == 1:
        matches = active
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
