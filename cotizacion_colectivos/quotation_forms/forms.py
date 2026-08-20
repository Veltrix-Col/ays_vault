from __future__ import annotations

import json
import re

from django import forms
from django.core.validators import validate_email
from django.utils.dateparse import parse_date

from .catalog import BranchSchema, FieldSchema


DOCUMENT = re.compile(r"^[A-Za-z0-9.-]{3,30}$")
PHONE = re.compile(r"^[+0-9() .-]{7,24}$")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        return [single(item, initial) for item in (data or [])]


class IndividualQuotationForm(forms.Form):
    items_payload = forms.CharField(widget=forms.HiddenInput)
    attachments = MultipleFileField(required=False, label="Documentos de soporte")

    def __init__(self, *args, schema: BranchSchema, context=None, **kwargs):
        self.schema = schema
        self.context = context or {}
        super().__init__(*args, **kwargs)
        locked_fields = set(self.context.get("locked_fields") or ())
        existing_person = bool(self.context.get("affiliate_key"))
        for definition in schema.fields:
            required = definition.required and not existing_person
            field_class = forms.BooleanField if definition.kind == "checkbox" else forms.CharField
            self.fields[definition.key] = field_class(
                label=definition.label,
                required=required,
                max_length=180,
                initial=self.context.get(definition.key, ""),
                disabled=definition.key in locked_fields,
            )
            if definition.kind == "email":
                self.fields[definition.key].widget = forms.EmailInput()
            elif definition.kind == "date":
                self.fields[definition.key].widget = forms.DateInput(attrs={"type": "date"})
            elif definition.kind == "tel":
                self.fields[definition.key].widget = forms.TextInput(attrs={"inputmode": "tel"})
            elif definition.kind == "document":
                self.fields[definition.key].widget = forms.TextInput(attrs={"autocomplete": "off"})
            elif definition.kind == "choice":
                self.fields[definition.key] = forms.ChoiceField(
                    label=definition.label,
                    required=required,
                    choices=(("", "Seleccione"),) + tuple((item, item) for item in definition.choices),
                    initial=self.context.get(definition.key, ""),
                    disabled=definition.key in locked_fields,
                )
        if self.context.get("requires_declared_company"):
            self.fields["declared_company"] = forms.CharField(
                label="Empresa a la cual pertenece",
                required=True,
                max_length=180,
                help_text="Escriba la empresa declarada por la persona. No se valida contra una lista Zoho no demostrada.",
            )

    @staticmethod
    def _clean_value(definition: FieldSchema, raw, *, required=None):
        if definition.kind == "checkbox":
            return raw in {True, 1, "1", "true", "True", "sí", "Sí", "si", "Si"}
        required = definition.required if required is None else required
        value = str(raw or "").strip()
        if required and not value:
            raise forms.ValidationError("Este campo es obligatorio.")
        if len(value) > 180 or any(ord(character) < 32 for character in value):
            raise forms.ValidationError("El valor no es válido.")
        if value and definition.kind == "email":
            validate_email(value)
        if value and definition.kind == "document" and not DOCUMENT.fullmatch(value):
            raise forms.ValidationError("Ingrese una identificación válida.")
        if value and definition.kind == "tel" and not PHONE.fullmatch(value):
            raise forms.ValidationError("Ingrese un teléfono válido.")
        if value and definition.kind == "date" and parse_date(value) is None:
            raise forms.ValidationError("Ingrese una fecha válida.")
        if value and definition.choices and value not in definition.choices:
            raise forms.ValidationError("Seleccione una opción válida.")
        return value

    def clean_declared_company(self):
        value = str(self.cleaned_data.get("declared_company") or "").strip()
        if not value or len(value) > 180 or any(ord(character) < 32 for character in value):
            raise forms.ValidationError("Ingrese una empresa válida.")
        return value

    def clean(self):
        cleaned = super().clean()
        for definition in self.schema.fields:
            try:
                cleaned[definition.key] = self._clean_value(
                    definition, cleaned.get(definition.key),
                    required=definition.required and not bool(self.context.get("affiliate_key")),
                )
            except forms.ValidationError as exc:
                self.add_error(definition.key, exc)
        # Una persona nueva no puede llegar a Contacts sin identidad
        # estructurada.  Contextos antiguos con un afiliado ya identificado
        # pueden seguir siendo leídos aunque no tengan estos campos nuevos.
        if not self.context.get("affiliate_key"):
            for key, label in (("first_name", "Nombres"), ("last_name", "Apellidos")):
                if key in self.fields and not str(cleaned.get(key) or "").strip():
                    self.add_error(key, f"{label}: este campo es obligatorio para una persona nueva.")
        try:
            raw_groups = json.loads(cleaned.get("items_payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            raise forms.ValidationError("La información agregada no es válida.")
        if not isinstance(raw_groups, dict):
            raise forms.ValidationError("La información agregada no es válida.")
        normalized = {}
        for group in self.schema.repeatables:
            rows = raw_groups.get(group.key, [])
            if not isinstance(rows, list) or not group.minimum <= len(rows) <= group.maximum:
                self.add_error("items_payload", f"Agregue entre {group.minimum} y {group.maximum} {group.plural.lower()}.")
                continue
            normalized_rows = []
            if group.key == "people" and self.schema.slug == "salud":
                requester_flags = sum(
                    row.get("is_requester", row.get("use_requester")) in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"}
                    for row in rows if isinstance(row, dict)
                )
                if requester_flags > 1:
                    self.add_error("items_payload", "Salud: el solicitante sólo puede agregarse una vez.")
            for position, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    self.add_error("items_payload", f"{group.singular.title()} {position}: información inválida.")
                    continue
                source_row = dict(row)
                use_requester = (
                    group.key == "people" and self.schema.slug == "salud" and position == 1
                    and row.get("is_requester", row.get("use_requester")) in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"}
                )
                if use_requester:
                    source_row.update({
                        "first_name": cleaned.get("first_name", ""),
                        "last_name": cleaned.get("last_name", ""),
                        "id_type": cleaned.get("requester_id_type") or self.context.get("requester_id_type", ""),
                        "document": cleaned.get("requester_document") or self.context.get("requester_document", ""),
                        "birth_date": cleaned.get("requester_birth_date") or self.context.get("requester_birth_date", ""),
                        "email": cleaned.get("requester_email") or self.context.get("requester_email", ""),
                        "phone": cleaned.get("requester_phone") or self.context.get("requester_phone", ""),
                    })
                    source_row["is_requester"] = True
                if group.key == "vehicles" and "insured_same_as_requester" in row:
                    same_as_requester = row.get("insured_same_as_requester") in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"}
                    source_row["insured_same_as_requester"] = same_as_requester
                    if same_as_requester:
                        source_row.update({
                            "insured_name": " ".join(filter(None, (cleaned.get("first_name", ""), cleaned.get("last_name", "")))),
                            "insured_id_type": cleaned.get("requester_id_type", ""),
                            "insured_document": cleaned.get("requester_document", ""),
                            "insured_first_name": cleaned.get("first_name", ""),
                            "insured_last_name": cleaned.get("last_name", ""),
                            "insured_birth_date": cleaned.get("requester_birth_date", ""),
                            "insured_email": cleaned.get("requester_email", ""),
                            "insured_phone": cleaned.get("requester_phone", ""),
                        })
                normalized_row = {}
                for definition in group.fields:
                    try:
                        normalized_row[definition.key] = self._clean_value(definition, source_row.get(definition.key))
                    except forms.ValidationError as exc:
                        self.add_error("items_payload", f"{group.singular.title()} {position} — {definition.label}: {exc.message}")
                if group.key == "vehicles":
                    zero_km = normalized_row.get("zero_km")
                    plate = normalized_row.get("plate")
                    if zero_km == "Sí":
                        normalized_row["plate"] = ""
                    elif zero_km == "No" and not plate:
                        self.add_error(
                            "items_payload",
                            f"{group.singular.title()} {position} — Placa: es obligatoria cuando el vehículo no es 0 km.",
                        )
                if group.key == "people" and self.schema.slug == "salud":
                    normalized_row["is_requester"] = bool(use_requester)
                    if normalized_row.get("currently_health_insured") == "Sí":
                        if not normalized_row.get("current_health_insurer"):
                            self.add_error(
                                "items_payload",
                                f"{group.singular.title()} {position} — Aseguradora actual: este campo es obligatorio cuando existe cobertura vigente.",
                            )
                    else:
                        normalized_row["current_health_insurer"] = ""
                        normalized_row["current_health_policy_end"] = ""
                normalized_rows.append(normalized_row)
            if group.key == "people" and self.schema.slug == "salud":
                requester_count = sum(bool(item.get("is_requester")) for item in normalized_rows)
                if requester_count > 1:
                    self.add_error("items_payload", "Salud: el solicitante sólo puede agregarse una vez.")
            if group.key == "vehicles" and self.schema.slug == "movilidad":
                for position, item in enumerate(normalized_rows, start=1):
                    if "insured_same_as_requester" not in rows[position - 1]:
                        continue
                    if item.get("insured_same_as_requester"):
                        continue
                    required_insured = {
                        "insured_first_name": "Nombres del asegurado",
                        "insured_last_name": "Apellidos del asegurado",
                        "insured_id_type": "Tipo de identificación del asegurado",
                        "insured_document": "Identificación del asegurado",
                        "insured_birth_date": "Fecha de nacimiento del asegurado",
                        "insured_email": "Correo del asegurado",
                        "insured_phone": "Teléfono del asegurado",
                    }
                    for key, label in required_insured.items():
                        if not str(item.get(key) or "").strip():
                            self.add_error("items_payload", f"Vehículo {position} — {label}: este campo es obligatorio para un asegurado diferente.")
            normalized[group.key] = normalized_rows
        cleaned["normalized_items"] = normalized
        return cleaned
