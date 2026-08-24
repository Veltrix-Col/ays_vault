from __future__ import annotations

import re

from django import forms
from django.core.validators import validate_email
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import SolicitudColectivo
from .adjustments import allowed_adjustments


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


class ClientSearchForm(BaseEntitySearchForm):
    query = forms.CharField(
        label="Buscar por nombre o identificación",
        max_length=100,
        strip=True,
        error_messages={"required": "Ingrese un criterio de búsqueda."},
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "search",
                "placeholder": "Nombre o identificación",
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
        public_access = kwargs.pop("public_access", False)
        super().__init__(*args, **kwargs)
        if public_access:
            self.fields.pop("assigned_to")
        else:
            self.fields["assigned_to"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value < timezone.localdate():
            raise forms.ValidationError("La fecha límite no puede estar en el pasado.")
        return value


class MultiPolicyRequestForm(forms.Form):
    request_type = forms.ChoiceField(
        label="Objetivo general",
        choices=(
            (SolicitudColectivo.RequestType.UPDATE, "Actualización"),
            (SolicitudColectivo.RequestType.RENEWAL, "Renovación"),
        ),
    )
    deadline = forms.DateField(label="Fecha límite", widget=forms.DateInput(attrs={"type": "date"}))
    internal_notes = forms.CharField(
        label="Observaciones o instrucciones",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    is_test = forms.BooleanField(label="Expediente de prueba", required=False, initial=True)
    confirm_snapshot = forms.BooleanField(label="Confirmo la selección y la creación de snapshots de solo lectura")

    def __init__(self, *args, policies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.policy_options = []
        for index, policy in enumerate(policies):
            adjustments = allowed_adjustments(policy.branch_code)
            policy_field = f"policy_{index}"
            adjustment_field = f"adjustments_{index}"
            self.fields[policy_field] = forms.BooleanField(required=False)
            self.fields[adjustment_field] = forms.MultipleChoiceField(
                required=False,
                choices=tuple((item.code, item.label) for item in adjustments),
                initial=tuple(item.code for item in adjustments),
                widget=forms.CheckboxSelectMultiple,
            )
            self.policy_options.append({
                "index": index,
                "policy": policy,
                "policy_field": policy_field,
                "adjustment_field": adjustment_field,
                "adjustments": adjustments,
            })

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value < timezone.localdate():
            raise forms.ValidationError("La fecha límite no puede estar en el pasado.")
        return value

    def clean(self):
        cleaned = super().clean()
        selected = []
        for option in self.policy_options:
            if not cleaned.get(option["policy_field"]):
                continue
            adjustments = cleaned.get(option["adjustment_field"]) or []
            if not adjustments:
                self.add_error(option["adjustment_field"], "Seleccione al menos un ajuste para esta póliza.")
            selected.append({"token": option["policy"].detail_token, "adjustments": adjustments})
        if not selected:
            raise forms.ValidationError("Seleccione al menos una póliza colectiva.")
        cleaned["selections"] = selected
        return cleaned


class RequestFilterForm(forms.Form):
    query = forms.CharField(label="Cliente, solicitud o póliza", required=False, max_length=100)
    status = forms.ChoiceField(label="Estado", required=False, choices=(("", "Todos"), *SolicitudColectivo.Status.choices))
    source_kind = forms.ChoiceField(label="Entidad", required=False, choices=(("", "Todas"), ("company", "Empresa"), ("person", "Individuo")))
    branch = forms.CharField(label="Ramo", required=False, max_length=8)
    request_type = forms.ChoiceField(label="Tipo", required=False, choices=(("", "Todos"), *SolicitudColectivo.RequestType.choices))
    assigned_to = forms.ModelChoiceField(label="Asignado internamente", required=False, queryset=None)
    task_responsible = forms.ChoiceField(label="Responsable", required=False, choices=(("", "Todos"),))
    created_from = forms.DateField(label="Creada desde", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    created_to = forms.DateField(label="Creada hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    deadline_from = forms.DateField(label="Vence desde", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    deadline_to = forms.DateField(label="Vence hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    assigned_to_me = forms.BooleanField(label="Asignadas a mí", required=False)
    warning = forms.BooleanField(label="Con advertencias", required=False)

    def __init__(self, *args, **kwargs):
        public_access = kwargs.pop("public_access", False)
        super().__init__(*args, **kwargs)
        if public_access:
            self.fields.pop("assigned_to")
            self.fields.pop("assigned_to_me")
        else:
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

    def __init__(self, *args, current_status=None, allowed_targets=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = REQUEST_TRANSITION_CHOICES
        if current_status is not None:
            domain_targets = SolicitudColectivo.TRANSITIONS.get(current_status, set())
            choices = tuple(choice for choice in choices if choice[0] in domain_targets)
        if allowed_targets is not None:
            choices = tuple(choice for choice in choices if choice[0] in allowed_targets)
        self.fields["target"].choices = choices


class RequestEditForm(forms.Form):
    assigned_to = forms.ModelChoiceField(label="Responsable", queryset=None)
    deadline = forms.DateField(label="Fecha límite", widget=forms.DateInput(attrs={"type": "date"}))
    internal_notes = forms.CharField(label="Notas internas", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, **kwargs):
        public_access = kwargs.pop("public_access", False)
        super().__init__(*args, **kwargs)
        if public_access:
            self.fields.pop("assigned_to")
        else:
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
    send_now = forms.BooleanField(label="Enviar invitación por correo ahora", required=False, initial=True)

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value <= timezone.localdate():
            raise forms.ValidationError("La fecha límite debe ser futura.")
        return value


class OptionalAccessEmailForm(forms.Form):
    recipient = forms.EmailField(
        label="Correo opcional para enviar el enlace",
        max_length=254,
        required=True,
    )


class IndividualAccessPrepareForm(OptionalAccessEmailForm):
    recipient = forms.CharField(
        label="Correo para código de verificación", max_length=254, required=False,
    )
    otp_required = forms.BooleanField(
        label="Solicitar código de verificación por correo", required=False,
    )
    responsible = forms.ChoiceField(
        label="Responsable de la solicitud",
        required=False,
        choices=(),
    )

    def clean(self):
        cleaned = super().clean()
        recipient = str(cleaned.get("recipient") or "").strip()
        if cleaned.get("otp_required"):
            if not recipient:
                self.add_error("recipient", "Indique un correo válido para solicitar el código de verificación.")
            else:
                try:
                    validate_email(recipient)
                except forms.ValidationError:
                    self.add_error("recipient", "Indique un correo válido para solicitar el código de verificación.")
        cleaned["recipient"] = recipient
        return cleaned


class PersonCompletionForm(forms.Form):
    """Datos estructurados que faltan para preparar un Contact.

    Se mantiene separado de la respuesta cifrada del cliente: completar datos
    es una corrección operativa interna y auditable.
    """

    first_name = forms.CharField(label="Nombres", required=False, max_length=120)
    last_name = forms.CharField(label="Apellidos", required=False, max_length=120)
    id_type = forms.ChoiceField(
        label="Tipo de identificación", required=False,
        choices=(('', "Seleccione"), ("CC", "CC"), ("CE", "CE"), ("EX", "EX"),
                 ("NIT", "NIT"), ("NUIP", "NUIP"), ("PAS", "PAS"),
                 ("PEP", "PEP"), ("PP", "PP"), ("PPT", "PPT"),
                 ("RC", "RC"), ("TI", "TI")),
    )
    document = forms.CharField(label="Número de identificación", required=False, max_length=40)
    birth_date = forms.DateField(label="Fecha de nacimiento", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    email = forms.EmailField(label="Correo", required=False, max_length=254)
    mobile = forms.CharField(label="Móvil", required=False, max_length=24)
    phone = forms.CharField(label="Teléfono", required=False, max_length=24)
    consent = forms.ChoiceField(label="Tratamiento de datos", required=False, choices=(('', "No modificar"), ("Si", "Sí"), ("No", "No")))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("last_name"):
            self.add_error("last_name", "Indique los apellidos para crear la persona.")
        if not cleaned.get("id_type"):
            self.add_error("id_type", "Indique el tipo de identificación.")
        if not cleaned.get("document"):
            self.add_error("document", "Indique el número de identificación.")
        return cleaned


class ExternalOTPForm(forms.Form):
    code = forms.RegexField(label="Código de verificación", regex=r"^\d{6}$", max_length=6, min_length=6, widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


class ExternalSubmitForm(forms.Form):
    declaration = forms.BooleanField(
        label="Confirmo que revisé la información y deseo enviar mi respuesta."
    )


class AttachmentUploadForm(forms.Form):
    attachment = forms.FileField(label="Archivo de soporte")
