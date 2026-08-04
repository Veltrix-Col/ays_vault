from __future__ import annotations

import re

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import SolicitudColectivo


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


class RequestCreateForm(forms.Form):
    source_kind = forms.ChoiceField(
        choices=(("company", "Empresa"), ("person", "Individuo")),
        widget=forms.HiddenInput,
        initial="company",
    )
    request_type = forms.ChoiceField(
        label="Tipo de solicitud",
        choices=(
            (SolicitudColectivo.RequestType.UPDATE, "Actualización de datos"),
            (SolicitudColectivo.RequestType.RENEWAL, "Renovación"),
        ),
    )
    assigned_to = forms.ModelChoiceField(label="Responsable", queryset=None)
    deadline = forms.DateField(label="Fecha límite", widget=forms.DateInput(attrs={"type": "date"}))
    internal_notes = forms.CharField(label="Observaciones internas", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))
    is_test = forms.BooleanField(label="Expediente de prueba", required=False)
    confirm_snapshot = forms.BooleanField(label="Confirmo la creación del snapshot de solo lectura")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value < timezone.localdate():
            raise forms.ValidationError("La fecha límite no puede estar en el pasado.")
        return value


class RequestFilterForm(forms.Form):
    query = forms.CharField(label="Cliente, solicitud o póliza", required=False, max_length=100)
    status = forms.ChoiceField(label="Estado", required=False, choices=(("", "Todos"), *SolicitudColectivo.Status.choices))
    source_kind = forms.ChoiceField(label="Entidad", required=False, choices=(("", "Todas"), ("company", "Empresa"), ("person", "Individuo")))
    branch = forms.CharField(label="Ramo", required=False, max_length=8)
    request_type = forms.ChoiceField(label="Tipo", required=False, choices=(("", "Todos"), *SolicitudColectivo.RequestType.choices))
    assigned_to = forms.ModelChoiceField(label="Responsable", required=False, queryset=None)
    created_from = forms.DateField(label="Creada desde", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    created_to = forms.DateField(label="Creada hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    deadline_from = forms.DateField(label="Vence desde", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    deadline_to = forms.DateField(label="Vence hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    assigned_to_me = forms.BooleanField(label="Asignadas a mí", required=False)
    warning = forms.BooleanField(label="Con advertencias", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")


REQUEST_TRANSITION_CHOICES = tuple(
    (value, label) for value, label in SolicitudColectivo.Status.choices
    if value in {
        SolicitudColectivo.Status.READY,
        SolicitudColectivo.Status.REVIEW,
        SolicitudColectivo.Status.APPROVED,
        SolicitudColectivo.Status.CLOSED,
        SolicitudColectivo.Status.CANCELLED,
    }
)


class RequestTransitionForm(forms.Form):
    target = forms.ChoiceField(choices=REQUEST_TRANSITION_CHOICES)


class RequestEditForm(forms.Form):
    assigned_to = forms.ModelChoiceField(label="Responsable", queryset=None)
    deadline = forms.DateField(label="Fecha límite", widget=forms.DateInput(attrs={"type": "date"}))
    internal_notes = forms.CharField(label="Notas internas", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value < timezone.localdate():
            raise forms.ValidationError("La fecha límite no puede estar en el pasado.")
        return value


class SnapshotRegenerateForm(forms.Form):
    confirm = forms.BooleanField(label="Confirmo que deseo reemplazar el snapshot del borrador")


class ExternalAccessPrepareForm(forms.Form):
    recipient = forms.EmailField(label="Correo destinatario", max_length=254)
    contact_name = forms.CharField(label="Nombre de contacto", required=False, max_length=120)
    deadline = forms.DateField(label="Fecha límite", widget=forms.DateInput(attrs={"type": "date"}))
    intro = forms.CharField(label="Mensaje introductorio", required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))
    instructions = forms.CharField(label="Instrucciones", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))
    confirm_records = forms.BooleanField(label="Confirmo la cantidad de registros")
    confirm_visible_fields = forms.BooleanField(label="Confirmo los campos visibles")
    confirm_economic = forms.BooleanField(label="Confirmo la información económica mostrada")
    confirm_snapshot = forms.BooleanField(label="Confirmo que el snapshot está disponible")
    confirm_privacy = forms.BooleanField(label="Confirmo el tratamiento de datos")

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value <= timezone.localdate():
            raise forms.ValidationError("La fecha límite debe ser futura.")
        return value


class ExternalOTPForm(forms.Form):
    code = forms.RegexField(label="Código de verificación", regex=r"^\d{6}$", max_length=6, min_length=6, widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


class ExternalSubmitForm(forms.Form):
    declaration = forms.BooleanField(label="Declaro que la información suministrada es veraz")


class AttachmentUploadForm(forms.Form):
    attachment = forms.FileField(label="Archivo de soporte")
