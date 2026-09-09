"""Consultas locales para lista y detalle; nunca consulta Zoho."""

from __future__ import annotations

from django import forms
from django.db.models import Count, F, Prefetch, Q

from cotizacion_colectivos.models import BillingOperationalCase, BillingTechnicalException

from .domain import EXCEPTION_CATALOG


class BillingCaseFilterForm(forms.Form):
    QUICK_CHOICES = (
        ("", "Casos activos"), ("high", "Prioridad alta"),
        ("without_task", "Sin tarea"), ("with_task", "Con tarea"),
        ("missing_operation", "Operación faltante"),
        ("missing_charge", "Cobro faltante"), ("multiple", "Múltiples hallazgos"),
    )
    quick = forms.ChoiceField(required=False, choices=QUICK_CHOICES, widget=forms.HiddenInput)
    query = forms.CharField(required=False, label="Buscar", widget=forms.SearchInput(attrs={
        "placeholder": "Póliza, cliente u operación",
    }))
    detection = forms.ChoiceField(required=False, label="Estado", choices=(
        ("", "Todos"), ("DETECTED", "Detectadas"), ("NOT_DETECTED", "Ya no detectadas"),
    ))
    priority = forms.ChoiceField(required=False, label="Prioridad", choices=(
        ("", "Todas"), ("1", "Alta"), ("2", "Media"), ("3", "Normal"),
    ))
    finding = forms.ChoiceField(required=False, label="Hallazgo", choices=(
        ("", "Todos"), *((item.exception_type.value, item.functional_name) for item in EXCEPTION_CATALOG),
    ))
    branch = forms.ChoiceField(required=False, label="Ramo")
    insurer = forms.ChoiceField(required=False, label="Aseguradora")
    task = forms.ChoiceField(required=False, label="Tarea Zoho", choices=(
        ("", "Todas"), ("without", "Sin tarea"), ("with", "Con tarea"),
    ))
    responsible = forms.ChoiceField(required=False, label="Responsable")

    def __init__(self, *args, snapshot=None, **kwargs):
        super().__init__(*args, **kwargs)
        choice_cases = BillingOperationalCase.objects.all()
        if snapshot is not None:
            choice_cases = choice_cases.filter(last_run=snapshot)
        for name in ("branch", "insurer", "responsible"):
            field_name = "zoho_task_responsible" if name == "responsible" else name
            values = choice_cases.exclude(**{field_name: ""}).values_list(
                field_name, flat=True
            ).distinct().order_by(field_name)
            self.fields[name].choices = (("", "Todos"), *((value, value) for value in values))


def filtered_billing_cases(data=None, *, snapshot=None):
    """Return cases from one complete snapshot, never from an active run."""
    if data is None:
        bound_data = {"detection": "DETECTED"}
    else:
        bound_data = data.copy()
        if "detection" not in bound_data:
            bound_data["detection"] = "DETECTED"
    form = BillingCaseFilterForm(bound_data, snapshot=snapshot)
    queryset = BillingOperationalCase.objects.annotate(
        finding_count=Count("exceptions", filter=Q(exceptions__detection_status="DETECTED"), distinct=True)
    ).prefetch_related(Prefetch(
        "exceptions",
        queryset=BillingTechnicalException.objects.order_by("exception_type", "exception_key"),
        to_attr="all_findings",
    ))
    if snapshot is None:
        queryset = queryset.none()
    else:
        queryset = queryset.filter(last_run=snapshot)
    if not form.is_valid():
        return form, queryset.none()
    values = form.cleaned_data
    quick = values["quick"]
    if quick:
        queryset = queryset.filter(detection_status="DETECTED")
    if quick == "high":
        queryset = queryset.filter(local_priority=1)
    elif quick == "without_task":
        queryset = queryset.filter(zoho_task_id="")
    elif quick == "with_task":
        queryset = queryset.exclude(zoho_task_id="")
    elif quick == "missing_operation":
        queryset = queryset.filter(exceptions__exception_type="MISSING_OPERATION").distinct()
    elif quick == "missing_charge":
        queryset = queryset.filter(exceptions__exception_type="MISSING_CHARGE").distinct()
    elif quick == "multiple":
        queryset = queryset.filter(finding_count__gte=2)
    if values["query"]:
        query = values["query"].strip()
        queryset = queryset.filter(
            Q(policy_number__icontains=query)
            | Q(client_name__icontains=query)
            | Q(operation_name__icontains=query)
        )
    if values["detection"]:
        queryset = queryset.filter(detection_status=values["detection"])
    if values["priority"]:
        queryset = queryset.filter(local_priority=int(values["priority"]))
    if values["finding"]:
        queryset = queryset.filter(exceptions__exception_type=values["finding"]).distinct()
    if values["branch"]:
        queryset = queryset.filter(branch=values["branch"])
    if values["insurer"]:
        queryset = queryset.filter(insurer=values["insurer"])
    if values["task"] == "with":
        queryset = queryset.exclude(zoho_task_id="")
    elif values["task"] == "without":
        queryset = queryset.filter(zoho_task_id="")
    if values["responsible"]:
        queryset = queryset.filter(zoho_task_responsible=values["responsible"])
    return form, queryset.order_by(
        "local_priority", F("relevant_date").asc(nulls_last=True), "policy_number", "case_key"
    )
