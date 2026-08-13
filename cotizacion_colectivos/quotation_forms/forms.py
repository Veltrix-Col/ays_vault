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
        for definition in schema.fields:
            self.fields[definition.key] = forms.CharField(
                label=definition.label,
                required=definition.required,
                max_length=180,
                initial=self.context.get("label", "") if definition.key == "collective_context" else None,
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
                    required=definition.required,
                    choices=(("", "Seleccione"),) + tuple((item, item) for item in definition.choices),
                )

    @staticmethod
    def _clean_value(definition: FieldSchema, raw):
        value = str(raw or "").strip()
        if definition.required and not value:
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

    def clean(self):
        cleaned = super().clean()
        for definition in self.schema.fields:
            try:
                cleaned[definition.key] = self._clean_value(
                    definition, cleaned.get(definition.key)
                )
            except forms.ValidationError as exc:
                self.add_error(definition.key, exc)
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
            for position, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    self.add_error("items_payload", f"{group.singular.title()} {position}: información inválida.")
                    continue
                normalized_row = {}
                for definition in group.fields:
                    try:
                        normalized_row[definition.key] = self._clean_value(definition, row.get(definition.key))
                    except forms.ValidationError as exc:
                        self.add_error("items_payload", f"{group.singular.title()} {position} — {definition.label}: {exc.message}")
                normalized_rows.append(normalized_row)
            normalized[group.key] = normalized_rows
        cleaned["normalized_items"] = normalized
        return cleaned
