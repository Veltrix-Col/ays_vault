from __future__ import annotations

import re

from django import forms


SAFE_NAME = re.compile(r"^[^\W_][^\W_\s.&'’-]*(?:[\s.&'’-]+[^\W_\s.&'’-]+)*$", re.UNICODE)


class BaseEntitySearchForm(forms.Form):
    query = forms.CharField(max_length=100, strip=True)

    document_label = "Documento"

    def clean_query(self):
        value = self.cleaned_data["query"].strip()
        if not value:
            raise forms.ValidationError("Ingrese un criterio de búsqueda.")
        if value.isdigit():
            if len(value) < 3:
                raise forms.ValidationError("Ingrese al menos 3 dígitos para buscar por documento.")
            return value
        if len(value) < 3:
            raise forms.ValidationError("Ingrese al menos 3 caracteres para buscar por nombre.")
        if not SAFE_NAME.fullmatch(value):
            raise forms.ValidationError("El criterio contiene caracteres no permitidos.")
        return value


class CompanySearchForm(BaseEntitySearchForm):
    query = forms.CharField(
        label="NIT o nombre de empresa",
        max_length=100,
        strip=True,
        error_messages={"required": "Ingrese un criterio de búsqueda."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "search",
                "placeholder": "NIT o nombre de empresa",
            }
        ),
    )


class PersonSearchForm(BaseEntitySearchForm):
    query = forms.CharField(
        label="Cédula o nombre del individuo",
        max_length=100,
        strip=True,
        error_messages={"required": "Ingrese un criterio de búsqueda."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "search",
                "placeholder": "Cédula o nombre",
            }
        ),
    )
