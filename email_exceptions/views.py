import hmac, json, logging
from datetime import datetime, time, timedelta
from statistics import mean, median
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Case, CharField, Count, IntegerField, Prefetch, Q, Value, When
from django.http import JsonResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .forms import CaseFollowUpForm, CaseNoteForm, CreateCaseTaskForm, CreateTaskForm, IgnoreExceptionForm
from .models import CaseActivity, CaseMessage, EmailAuditEvent, EmailException, ExceptionCase, ZohoTaskCreation
from .case_workflow import CaseOperationError, CaseTransitionError, add_case_note, assign_case, get_assignable_operators, take_case, transition_case, unassign_case, update_case_follow_up
from .permissions import can_operate, can_view
from .services import ingest_payload, record_audit
from .zoho import area_options, create_case_task, create_exception_task, responsible_options, task_creation_available

logger = logging.getLogger("email_exceptions")


def _task_form(*, data=None, initial=None, prefix=None):
    form = CreateTaskForm(data=data, initial=initial, prefix=prefix)
    try:
        options = responsible_options()
        form.fields["responsible"].choices = [
            (option.actual_value, option.display_value) for option in options
        ]
    except Exception:
        form.fields["responsible"].choices = ()
    return form


def _task_action_available(request, item):
    return bool(
        can_operate(getattr(request, "user", None))
        and item.status == EmailException.PENDING
        and task_creation_available()
    )

def _token_ok(request):
    expected = str(getattr(settings, "EMAIL_EXCEPTIONS_INBOUND_TOKEN", "") or "")
    supplied = request.headers.get("X-Email-Exceptions-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


_ACTIVE_CASE_STATUSES = (
    ExceptionCase.Status.OPEN,
    ExceptionCase.Status.PENDING,
    ExceptionCase.Status.IN_PROGRESS,
    ExceptionCase.Status.WAITING,
)


def _today_window():
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    return start, start + timedelta(days=1)


def _quick_view_filter(qs, quick, *, user, now, today_start, tomorrow_start):
    active = Q(status__in=_ACTIVE_CASE_STATUSES)
    if quick == "mine":
        return qs.filter(assigned_to_id=user.pk)
    if quick == "unassigned":
        return qs.filter(assigned_to__isnull=True)
    if quick == "overdue":
        return qs.filter(active, follow_up_at__lt=now)
    if quick == "today":
        return qs.filter(active, follow_up_at__gte=today_start, follow_up_at__lt=tomorrow_start)
    if quick == "attention":
        return qs.filter(
            active,
        ).filter(
            Q(follow_up_at__lt=now)
            | Q(assigned_to__isnull=True)
            | Q(opened_at__lte=now - timedelta(days=4))
        )
    return qs


def _age_bucket(opened_at, *, now):
    age = now - opened_at
    days = age.total_seconds() / 86400
    if days <= 1:
        return "0–1 días"
    if days <= 3:
        return "2–3 días"
    if days <= 7:
        return "4–7 días"
    return "8+ días"


def _supervision_snapshot(*, now, today_start, tomorrow_start):
    """Build read-only supervision metrics from the current case population."""
    cases = ExceptionCase.objects.all()
    active_filter = Q(status__in=_ACTIVE_CASE_STATUSES)
    overdue_filter = active_filter & Q(follow_up_at__lt=now)
    today_filter = active_filter & Q(follow_up_at__gte=today_start, follow_up_at__lt=tomorrow_start)
    attention_filter = active_filter & (
        Q(follow_up_at__lt=now)
        | Q(assigned_to__isnull=True)
        | Q(opened_at__lte=now - timedelta(days=4))
    )
    summary = cases.aggregate(
        active=Count("pk", filter=active_filter),
        unassigned=Count("pk", filter=active_filter & Q(assigned_to__isnull=True)),
        overdue=Count("pk", filter=overdue_filter),
        today=Count("pk", filter=today_filter),
        resolved=Count("pk", filter=Q(status=ExceptionCase.Status.RESOLVED)),
        attention=Count("pk", filter=attention_filter),
    )

    status_rows = cases.values("status").annotate(total=Count("pk")).order_by("status")
    status_counts = {row["status"]: row["total"] for row in status_rows}
    status_distribution = [
        {"value": value, "label": label, "count": status_counts.get(value, 0)}
        for value, label in ExceptionCase.Status.choices
        if status_counts.get(value, 0) or value != ExceptionCase.Status.PENDING
    ]

    # Each aggregate/grouped query below is set-based: no per-assignee or
    # per-case query is issued while rendering the supervision layer.
    workload = []
    for row in cases.filter(active_filter).values(
        "assigned_to_id", "assigned_to__username", "assigned_to__first_name",
        "assigned_to__last_name",
    ).annotate(
        active=Count("pk"), overdue=Count("pk", filter=Q(follow_up_at__lt=now)),
        today=Count("pk", filter=Q(follow_up_at__gte=today_start, follow_up_at__lt=tomorrow_start)),
    ):
        assignee_id = row["assigned_to_id"]
        workload.append({
            "name": "Sin asignar" if not assignee_id else (
                " ".join(part for part in (
                    row["assigned_to__first_name"], row["assigned_to__last_name"],
                ) if part).strip() or row["assigned_to__username"]
            ),
            "active": row["active"], "overdue": row["overdue"], "today": row["today"],
        })

    age_rows = cases.filter(active_filter).annotate(
        age_bucket=Case(
            When(opened_at__gte=now - timedelta(days=1), then=Value("0–1 días")),
            When(opened_at__gte=now - timedelta(days=3), then=Value("2–3 días")),
            When(opened_at__gte=now - timedelta(days=7), then=Value("4–7 días")),
            default=Value("8+ días"), output_field=CharField(),
        )
    ).values("age_bucket").annotate(total=Count("pk"))
    age_counts = {label: 0 for label in ("0–1 días", "2–3 días", "4–7 días", "8+ días")}
    age_counts.update({row["age_bucket"]: row["total"] for row in age_rows})

    follow_up_counts = {
        "SIN_SEGUIMIENTO": cases.filter(active_filter, follow_up_at__isnull=True).count(),
        "PROGRAMADO": cases.filter(active_filter, follow_up_at__gte=tomorrow_start).count(),
        "HOY": summary["today"],
        "VENCIDO": summary["overdue"],
    }
    resolution_values = list(
        cases.filter(
            status__in=(ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED),
            resolved_at__isnull=False,
        ).values_list("created_at", "resolved_at")
    )
    resolution_seconds = [
        (resolved_at - created_at).total_seconds()
        for created_at, resolved_at in resolution_values
        if resolved_at >= created_at
    ]
    resolution = {
        "count": len(resolution_seconds),
        "average_days": (mean(resolution_seconds) / 86400) if resolution_seconds else None,
        "median_days": (median(resolution_seconds) / 86400) if resolution_seconds else None,
    }
    return {
        "header": {
            "active": summary["active"],
            "unassigned": summary["unassigned"],
            "overdue": summary["overdue"],
            "today": summary["today"],
            "resolved": summary["resolved"],
        },
        "status_distribution": status_distribution,
        "workload": sorted(workload, key=lambda item: (item["name"] != "Sin asignar", item["name"].lower())),
        "age_distribution": [{"label": label, "count": count} for label, count in age_counts.items()],
        "follow_up_distribution": [
            {"label": "Sin seguimiento", "count": follow_up_counts["SIN_SEGUIMIENTO"]},
            {"label": "Programado", "count": follow_up_counts["PROGRAMADO"]},
            {"label": "Hoy", "count": follow_up_counts["HOY"]},
            {"label": "Vencido", "count": follow_up_counts["VENCIDO"]},
        ],
        "resolution": resolution,
        "attention": summary["attention"],
    }

@csrf_exempt
@require_POST
def inbound(request):
    if not getattr(settings, "EMAIL_EXCEPTIONS_ENABLED", False) or not getattr(settings, "EMAIL_EXCEPTIONS_INBOUND_ENABLED", False): return JsonResponse({"ok": False, "error": "inbound_disabled"}, status=404)
    if not _token_ok(request): return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)
    if request.content_type.split(";", 1)[0].strip().lower() != "application/json": return JsonResponse({"ok": False, "error": "content_type_required"}, status=415)
    if len(request.body) > int(getattr(settings, "EMAIL_EXCEPTIONS_MAX_PAYLOAD_BYTES", 5 * 1024 * 1024)): return JsonResponse({"ok": False, "error": "payload_too_large"}, status=413)
    try: payload = json.loads(request.body)
    except (TypeError, ValueError): return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    required = ("source_mailbox", "message_id", "subject")
    if not isinstance(payload, dict) or any(not str(payload.get(key) or "").strip() for key in required): return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    if str(payload["source_mailbox"]).strip().lower() not in getattr(settings, "EMAIL_EXCEPTIONS_ALLOWED_MAILBOXES", ()): return JsonResponse({"ok": False, "error": "mailbox_not_allowed"}, status=403)
    try: email, exception, created = ingest_payload(payload)
    except Exception:
        logger.exception("inbound_email_failed")
        return JsonResponse({"ok": False, "error": "processing_failed"}, status=500)
    return JsonResponse({"ok": True, "created": created, "email_id": email.pk, "exception_id": exception.pk if exception else None, "status": exception.status if exception else "IGNORED"}, status=201 if created else 200)

def exception_list(request):
    if not can_view(getattr(request, "user", None)):
        return HttpResponseForbidden("No tiene permiso para consultar Excepciones de Correo.")
    now = timezone.now()
    today_start, tomorrow_start = _today_window()
    population = ExceptionCase.objects.all()
    quick = request.GET.get("quick", "all").strip()
    if quick not in {"all", "mine", "unassigned", "overdue", "today", "attention"}:
        quick = "all"
    qs = ExceptionCase.objects.select_related("assigned_to").annotate(
        message_count=Count("messages", distinct=True),
        exception_count=Count("exceptions", distinct=True),
        follow_up_bucket=Case(
            When(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__lt=now, then=Value("overdue")),
            When(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__gte=today_start, follow_up_at__lt=tomorrow_start, then=Value("today")),
            When(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__isnull=False, then=Value("future")),
            default=Value("none"), output_field=CharField(),
        ),
        operational_order=Case(
            When(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__lt=now, then=Value(0)),
            When(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__gte=today_start, follow_up_at__lt=tomorrow_start, then=Value(1)),
            When(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__isnull=False, then=Value(2)),
            When(status__in=_ACTIVE_CASE_STATUSES, then=Value(3)),
            default=Value(4), output_field=IntegerField(),
        ),
        age_bucket=Case(
            When(opened_at__gte=now - timedelta(days=1), then=Value("0_1")),
            When(opened_at__gte=now - timedelta(days=3), then=Value("2_3")),
            When(opened_at__gte=now - timedelta(days=7), then=Value("4_7")),
            default=Value("8_plus"), output_field=CharField(),
        ),
    )
    qs = _quick_view_filter(qs, quick, user=request.user, now=now, today_start=today_start, tomorrow_start=tomorrow_start)
    case_status = request.GET.get("case_status", "").strip()
    if case_status in ExceptionCase.Status.values:
        qs = qs.filter(status=case_status)
    # Keep the former status=... query parameter working for bookmarked inbox URLs.
    exception_status = request.GET.get("exception_status", request.GET.get("status", "")).strip()
    if exception_status in {value for value, _label in EmailException.STATUS_CHOICES}:
        qs = qs.filter(exceptions__status=exception_status)
    organization = request.GET.get("organization", "").strip()
    if organization:
        qs = qs.filter(organization__icontains=organization)
    action_type = request.GET.get("action_type", "").strip()
    if action_type:
        qs = qs.filter(action_type=action_type)
    assigned_to = request.GET.get("assigned_to", "").strip()
    if assigned_to.isdigit():
        qs = qs.filter(assigned_to_id=int(assigned_to))
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(
            Q(case_key__icontains=query)
            | Q(organization__icontains=query)
            | Q(family__icontains=query)
            | Q(event_type__icontains=query)
            | Q(action_type__icontains=query)
            | Q(messages__email__subject__icontains=query)
            | Q(messages__email__from_email__icontains=query)
            | Q(exceptions__last_subject__icontains=query)
            | Q(exceptions__exception_reason__icontains=query)
        )
    qs = qs.distinct().order_by("operational_order", "follow_up_at", "-last_activity_at", "-pk")
    page_obj = Paginator(qs, 50).get_page(request.GET.get("page"))
    filters = request.GET.copy()
    filters.pop("page", None)
    quick_counts = {
        "all": population.count(),
        "mine": population.filter(assigned_to_id=request.user.pk).count(),
        "unassigned": population.filter(assigned_to__isnull=True).count(),
        "overdue": population.filter(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__lt=now).count(),
        "today": population.filter(status__in=_ACTIVE_CASE_STATUSES, follow_up_at__gte=today_start, follow_up_at__lt=tomorrow_start).count(),
    }
    supervision = _supervision_snapshot(now=now, today_start=today_start, tomorrow_start=tomorrow_start)
    quick_counts["attention"] = supervision["attention"]
    quick_views = (
        {"key": "all", "label": "Todos", "count": quick_counts["all"]},
        {"key": "mine", "label": "Mis casos", "count": quick_counts["mine"]},
        {"key": "unassigned", "label": "Sin asignar", "count": quick_counts["unassigned"]},
        {"key": "overdue", "label": "Seguimiento vencido", "count": quick_counts["overdue"]},
        {"key": "today", "label": "Seguimiento hoy", "count": quick_counts["today"]},
        {"key": "attention", "label": "Requiere atención", "count": quick_counts["attention"]},
    )
    return render(request, "email_exceptions/list.html", {
        "page_obj": page_obj,
        "cases": page_obj.object_list,
        "filters_query": filters.urlencode(),
        "quick": quick,
        "quick_counts": quick_counts,
        "quick_views": quick_views,
        "supervision": supervision,
        "assignable_operators": get_assignable_operators(),
        "case_status_choices": ExceptionCase.Status.choices,
        "exception_status_choices": EmailException.STATUS_CHOICES,
        "action_types": ExceptionCase.objects.exclude(action_type="").values_list("action_type", flat=True).distinct().order_by("action_type"),
    })

def exception_detail(request, pk):
    if not can_view(getattr(request, "user", None)):
        return HttpResponseForbidden("No tiene permiso para consultar Excepciones de Correo.")
    item = get_object_or_404(EmailException.objects.select_related("primary_email", "policy_profile").prefetch_related("messages__email", "audit_events"), pk=pk)
    task_enabled = _task_action_available(request, item)
    task_form = _task_form(initial={"subject": item.last_subject, "description": item.exception_reason}) if task_enabled else CreateTaskForm()
    return render(request, "email_exceptions/detail.html", {"item": item, "ignore_form": IgnoreExceptionForm(), "task_form": task_form, "email_task_write_enabled": task_enabled})


def case_detail(request, pk):
    if not can_view(getattr(request, "user", None)):
        return HttpResponseForbidden("No tiene permiso para consultar Excepciones de Correo.")
    messages_qs = CaseMessage.objects.select_related("email", "linked_by").order_by("email__received_at", "pk")
    exception_qs = EmailException.objects.select_related("policy_profile").prefetch_related(
        Prefetch("audit_events", queryset=EmailAuditEvent.objects.select_related("actor").order_by("timestamp", "pk")),
        "zoho_task",
    )
    case = get_object_or_404(
        ExceptionCase.objects.select_related("assigned_to").annotate(
            message_count=Count("messages", distinct=True),
            exception_count=Count("exceptions", distinct=True),
        ).prefetch_related(
            Prefetch("messages", queryset=messages_qs),
            Prefetch("exceptions", queryset=exception_qs),
            "activities__actor",
        ),
        pk=pk,
    )
    case_task = ZohoTaskCreation.objects.filter(case=case).first() or ZohoTaskCreation.objects.filter(exception__case=case).first()
    case_task_form = CreateCaseTaskForm()
    case_task_available = bool(
        can_operate(getattr(request, "user", None))
        and task_creation_available()
        and (not case_task or case_task.technical_status == "FAILED")
    )
    if case_task_available:
        try:
            case_task_form.fields["responsible"].choices = [(item.actual_value, item.display_value) for item in responsible_options()]
            case_task_form.fields["area"].choices = area_options()
        except Exception:
            case_task_form.fields["responsible"].choices = ()
            case_task_form.fields["area"].choices = ()
    task_actions = {
        item.pk: _task_action_available(request, item)
        for item in case.exceptions.all()
    }
    case_messages = list(case.messages.all())
    timeline_email_ids = {link.email_id for link in case_messages}
    status_labels = dict(ExceptionCase.Status.choices)
    timeline = [
        {
            "kind": "message",
            "timestamp": link.email.received_at,
            "sequence": (0, link.pk),
            "message": link,
        }
        for link in case_messages
    ]
    activity_labels = {
        CaseActivity.EventType.CASE_CREATED: "Caso creado.",
        CaseActivity.EventType.STATUS_CHANGED: "Estado: {from_status} → {to_status}.",
        CaseActivity.EventType.RESOLVED: "Caso resuelto.",
        CaseActivity.EventType.CLOSED: "Caso cerrado.",
        CaseActivity.EventType.REOPENED: "Caso reabierto.",
        CaseActivity.EventType.ASSIGNED: "Responsable asignado.",
        CaseActivity.EventType.REASSIGNED: "Responsable reasignado.",
        CaseActivity.EventType.UNASSIGNED: "Responsable retirado.",
        CaseActivity.EventType.FOLLOW_UP_UPDATED: "Seguimiento actualizado.",
        CaseActivity.EventType.NOTE_ADDED: "Nota interna.",
    }
    for activity in case.activities.all():
        if activity.event_type == CaseActivity.EventType.STATUS_CHANGED:
            summary = activity_labels[activity.event_type].format(
                from_status=status_labels.get(activity.from_status, activity.from_status or "—"),
                to_status=status_labels.get(activity.to_status, activity.to_status or "—"),
            )
        else:
            summary = activity_labels.get(activity.event_type, "Actividad operativa registrada.")
        timeline.append({
            "kind": "activity",
            "timestamp": activity.created_at,
            "sequence": (1, activity.pk),
            "activity": activity,
            "summary": summary,
        })
    audit_labels = {
        "EMAIL_RECEIVED": "Correo recibido",
        "CLASSIFIED": "Clasificación registrada",
        "EXCEPTION_CREATED": "Excepción creada",
        "EMAIL_LINKED": "Correo vinculado",
        "IGNORED": "Excepción ignorada",
        "TASK_CREATED": "Tarea Zoho registrada",
    }
    for item in case.exceptions.all():
        for event in item.audit_events.all():
            timeline.append({
                "kind": "exception_event",
                "timestamp": event.timestamp,
                "sequence": (2, event.pk),
                "audit_event": event,
                "exception": item,
                "summary": audit_labels.get(event.event_type, event.event_type.replace("_", " ").title()),
            })
    timeline.sort(key=lambda event: (event["timestamp"], event["sequence"]))
    exception_items = []
    for item in case.exceptions.all():
        prefix = f"exception-{item.pk}"
        exception_items.append({
            "item": item,
            "primary_email_in_timeline": item.primary_email_id in timeline_email_ids,
            "task_enabled": task_actions[item.pk],
            "task_form": _task_form(
                initial={"subject": item.last_subject, "description": item.exception_reason},
                prefix=prefix,
            ) if task_actions[item.pk] else CreateTaskForm(prefix=prefix),
            "ignore_form": IgnoreExceptionForm(prefix=prefix),
        })
    return render(request, "email_exceptions/case_detail.html", {
        "case": case,
        "case_messages": case_messages,
        "timeline": timeline,
        "exception_items": exception_items,
        "assignable_operators": get_assignable_operators() if can_operate(getattr(request, "user", None)) else (),
        "follow_up_form": CaseFollowUpForm(initial={"next_action": case.next_action, "follow_up_at": case.follow_up_at}),
        "note_form": CaseNoteForm(),
        "case_task": case_task,
        "case_task_form": case_task_form,
        "case_task_available": case_task_available,
    })


@require_POST
def create_case_task_view(request, pk):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    case = get_object_or_404(ExceptionCase, pk=pk)
    if not task_creation_available():
        messages.error(request, "La creación de Tasks no está habilitada para este usuario o perfil.")
        return redirect("email_exceptions:case_detail", pk=pk)
    form = CreateCaseTaskForm(request.POST)
    try:
        form.fields["responsible"].choices = [(item.actual_value, item.display_value) for item in responsible_options()]
        form.fields["area"].choices = area_options()
    except Exception:
        form.add_error(None, "No fue posible cargar los catálogos autorizados de Zoho.")
    if not form.is_valid():
        messages.error(request, "Seleccione un Responsable y un Área válidos.")
        return redirect("email_exceptions:case_detail", pk=pk)
    try:
        task = create_case_task(case=case, actor=request.user, **form.cleaned_data)
    except Exception:
        messages.error(request, "No fue posible registrar la Task Zoho. Revise el estado del expediente.")
    else:
        messages.success(request, "Solicitud de Task registrada para el caso.")
    return redirect("email_exceptions:case_detail", pk=pk)


@require_POST
def transition_case_view(request, pk):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    try:
        transition_case(
            pk,
            request.POST.get("status"),
            actor=request.user,
            reason=request.POST.get("reason", ""),
            comment=request.POST.get("comment", ""),
        )
    except CaseTransitionError as exc:
        messages.error(request, str(exc))
        return redirect("email_exceptions:case_detail", pk=pk)
    messages.success(request, "Estado del caso actualizado.")
    return redirect("email_exceptions:case_detail", pk=pk)


def _case_operation_redirect(request, pk, operation, success_message):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    try:
        operation()
    except CaseOperationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, success_message)
    return redirect("email_exceptions:case_detail", pk=pk)


@require_POST
def assign_case_view(request, pk):
    return _case_operation_redirect(request, pk, lambda: assign_case(pk, actor=request.user, assignee_id=request.POST.get("assignee")), "Responsable actualizado.")


@require_POST
def take_case_view(request, pk):
    return _case_operation_redirect(request, pk, lambda: take_case(pk, actor=request.user), "Tomaste el caso.")


@require_POST
def unassign_case_view(request, pk):
    return _case_operation_redirect(request, pk, lambda: unassign_case(pk, actor=request.user), "Caso dejado sin responsable.")


@require_POST
def follow_up_view(request, pk):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    form = CaseFollowUpForm(request.POST)
    if not form.is_valid():
        messages.error(request, "La información de seguimiento no es válida.")
    else:
        try:
            update_case_follow_up(pk, actor=request.user, **form.cleaned_data)
        except CaseOperationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Seguimiento actualizado.")
    return redirect("email_exceptions:case_detail", pk=pk)


@require_POST
def note_view(request, pk):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    form = CaseNoteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "La nota interna no es válida.")
    else:
        try:
            add_case_note(pk, actor=request.user, **form.cleaned_data)
        except CaseOperationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Nota interna añadida.")
    return redirect("email_exceptions:case_detail", pk=pk)

@require_POST
def ignore_exception(request, pk):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    item = get_object_or_404(EmailException, pk=pk)
    prefix = f"exception-{pk}" if any(key.startswith(f"exception-{pk}-") for key in request.POST) else None
    form = IgnoreExceptionForm(request.POST, prefix=prefix)
    if form.is_valid() and item.status == EmailException.PENDING:
        item.status, item.ignored_reason, item.ignored_comment, item.handled_by, item.handled_at = EmailException.IGNORED, form.cleaned_data["reason"], form.cleaned_data["comment"], request.user, timezone.now(); item.save(update_fields=("status", "ignored_reason", "ignored_comment", "handled_by", "handled_at")); record_audit(item, "IGNORED", request.user, {"reason": item.ignored_reason}); messages.success(request, "La excepción fue ignorada y permanece auditable.")
    else: messages.error(request, "No fue posible ignorar la excepción.")
    return redirect("email_exceptions:detail", pk=pk)

@require_POST
def create_task(request, pk):
    if not can_operate(getattr(request, "user", None)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    item = get_object_or_404(EmailException, pk=pk)
    if not _task_action_available(request, item):
        messages.error(request, "La creación de Tasks no está habilitada para este usuario o perfil.")
        return redirect("email_exceptions:detail", pk=pk)
    prefix = f"exception-{pk}" if any(key.startswith(f"exception-{pk}-") for key in request.POST) else None
    form = _task_form(data=request.POST, prefix=prefix)
    if not form.is_valid() or item.status != EmailException.PENDING: messages.error(request, "La solicitud no es válida o ya fue gestionada."); return redirect("email_exceptions:detail", pk=pk)
    try:
        create_exception_task(exception=item, actor=request.user, **form.cleaned_data)
    except Exception:
        messages.error(request, "No fue posible crear la Task Zoho. La excepción conserva su estado y el intento quedó auditado.")
    else:
        messages.success(request, "Task Zoho creada y excepción marcada como gestionada.")
    return redirect("email_exceptions:detail", pk=pk)
