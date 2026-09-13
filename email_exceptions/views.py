import hmac, json, logging
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .forms import CreateTaskForm, IgnoreExceptionForm
from .models import EmailException
from .services import ingest_payload, record_audit
from .zoho import create_exception_task, responsible_options
from vault.models import UserProfile

logger = logging.getLogger("email_exceptions")
OPERATORS = ("ADMIN", "LEADER", "ANALYST")


def _operator_allowed(request):
    delegated = getattr(request, "delegated_access", None)
    if (
        getattr(delegated, "allowed", False)
        and getattr(request, "inherited_tool_application", "") == "email_exceptions"
    ):
        return True
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_active:
        return False
    try:
        profile = user.vault_profile
    except UserProfile.DoesNotExist:
        return False
    return profile.active and profile.role in OPERATORS


def _task_form(*, data=None, initial=None):
    form = CreateTaskForm(data=data, initial=initial)
    try:
        options = responsible_options()
        form.fields["responsible"].choices = [
            (option.actual_value, option.display_value) for option in options
        ]
    except Exception:
        form.fields["responsible"].choices = ()
    return form

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
    qs = EmailException.objects.select_related("primary_email", "policy_profile")
    for key in ("status", "organization"):
        if request.GET.get(key): qs = qs.filter(**{key: request.GET[key]})
    if request.GET.get("q"): qs = qs.filter(Q(last_subject__icontains=request.GET["q"]) | Q(exception_reason__icontains=request.GET["q"]) | Q(primary_email__from_email__icontains=request.GET["q"]))
    return render(request, "email_exceptions/list.html", {"exceptions": qs[:200], "counts": {status: EmailException.objects.filter(status=status).count() for status in ("PENDING", "MANAGED", "IGNORED", "ERROR")}})

def exception_detail(request, pk):
    item = get_object_or_404(EmailException.objects.select_related("primary_email", "policy_profile").prefetch_related("messages__email", "audit_events"), pk=pk)
    task_form = _task_form(initial={"subject": item.last_subject, "description": item.exception_reason})
    return render(request, "email_exceptions/detail.html", {"item": item, "ignore_form": IgnoreExceptionForm(), "task_form": task_form, "email_task_write_enabled": getattr(settings, "EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED", False)})

@require_POST
def ignore_exception(request, pk):
    if not _operator_allowed(request):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    item = get_object_or_404(EmailException, pk=pk)
    form = IgnoreExceptionForm(request.POST)
    if form.is_valid() and item.status == EmailException.PENDING:
        item.status, item.ignored_reason, item.ignored_comment, item.handled_by, item.handled_at = EmailException.IGNORED, form.cleaned_data["reason"], form.cleaned_data["comment"], request.user, timezone.now(); item.save(update_fields=("status", "ignored_reason", "ignored_comment", "handled_by", "handled_at")); record_audit(item, "IGNORED", request.user, {"reason": item.ignored_reason}); messages.success(request, "La excepción fue ignorada y permanece auditable.")
    else: messages.error(request, "No fue posible ignorar la excepción.")
    return redirect("email_exceptions:detail", pk=pk)

@require_POST
def create_task(request, pk):
    if not _operator_allowed(request):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    item = get_object_or_404(EmailException, pk=pk)
    form = _task_form(data=request.POST)
    if not form.is_valid() or item.status != EmailException.PENDING: messages.error(request, "La solicitud no es válida o ya fue gestionada."); return redirect("email_exceptions:detail", pk=pk)
    try:
        create_exception_task(exception=item, actor=request.user, **form.cleaned_data)
    except Exception:
        messages.error(request, "No fue posible crear la Task Zoho. La excepción conserva su estado y el intento quedó auditado.")
    else:
        messages.success(request, "Task Zoho creada y excepción marcada como gestionada.")
    return redirect("email_exceptions:detail", pk=pk)
