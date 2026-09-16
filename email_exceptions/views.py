import hmac, json, logging
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .forms import CreateTaskForm, IgnoreExceptionForm
from .models import CaseMessage, EmailException, ExceptionCase
from .permissions import can_operate, can_view
from .services import ingest_payload, record_audit
from .zoho import create_exception_task, responsible_options, task_creation_available

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
    qs = ExceptionCase.objects.annotate(
        message_count=Count("messages", distinct=True),
        exception_count=Count("exceptions", distinct=True),
    )
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
    qs = qs.distinct().order_by("-last_activity_at", "-pk")
    page_obj = Paginator(qs, 50).get_page(request.GET.get("page"))
    filters = request.GET.copy()
    filters.pop("page", None)
    return render(request, "email_exceptions/list.html", {
        "page_obj": page_obj,
        "cases": page_obj.object_list,
        "filters_query": filters.urlencode(),
        "counts": {status: EmailException.objects.filter(status=status).count() for status, _ in EmailException.STATUS_CHOICES},
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
    exception_qs = EmailException.objects.select_related("policy_profile").prefetch_related("audit_events", "zoho_task")
    case = get_object_or_404(
        ExceptionCase.objects.prefetch_related(
            Prefetch("messages", queryset=messages_qs),
            Prefetch("exceptions", queryset=exception_qs),
        ),
        pk=pk,
    )
    task_actions = {
        item.pk: _task_action_available(request, item)
        for item in case.exceptions.all()
    }
    case_messages = list(case.messages.all())
    timeline_email_ids = {link.email_id for link in case_messages}
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
        "exception_items": exception_items,
    })

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
