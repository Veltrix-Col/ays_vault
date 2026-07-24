from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.template.loader import render_to_string

from .decorators import role_required
from .forms import CardEditForm, CardForm, CardSearchForm, OperationContextForm, ProtectedActionForm, ReauthenticationForm
from .models import AuditEvent, PaymentCard, PendingSensitiveOperation, SecurityAlert, UserProfile
from .security import audit, cached_chain_status, consume_copy_grant, create_reveal_grant
from .identity import create_alert, current_secure_session, has_recent_reauth, verify_totp
from .protected_operations import (
    clear_intent,
    close_operation_contexts,
    create_intent,
    create_operation_context,
    create_operation_window,
    current_operation_context,
    current_operation_window,
    get_intent,
    mark_identity_verified,
)
from django.urls import reverse
from .policies import evaluate_access_policy
from .sensitive_operations import (
    PendingOperationError,
    execute_operation,
    new_operation_id,
    prepare_operation,
    protected_payload,
    reauthentication_url,
)


def _active_profile(request):
    profile = getattr(request.user, "vault_profile", None)
    return profile if profile and profile.active and profile.role else None


def _enforce_schedule(request, operation):
    decision = evaluate_access_policy(request.user, request.user.vault_profile.role, operation)
    if decision.requires_block:
        audit(request, "CRITICAL_BLOCKED", reason=decision.reason, result="DENIED", risk_level=decision.severity, metadata={"operation": operation, "policy_id": decision.policy_identifier, "exception_id": decision.exception_applied})
        raise PermissionDenied("Operación bloqueada por la política de horario.")
    if decision.requires_reauthentication and operation == "VIEW" and not has_recent_reauth(request, "outside_hours"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=outside_hours&next={request.path}")
    return decision


@login_required
def dashboard(request):
    profile = _active_profile(request)
    if not profile:
        audit(request, "DENIED", result="DENIED", risk_level="HIGH", metadata={"reason": "profile_inactive_or_unassigned"})
        return render(request, "vault/access_denied.html", status=403)
    if profile.role == UserProfile.ADMIN:
        return redirect("vault:control_center")
    return redirect("vault:card_list")


def _cards_for_operator(request):
    cards = PaymentCard.objects.order_by("client_name", "pk")
    if request.user.vault_profile.role == UserProfile.ANALYST:
        cards = cards.filter(active=True)
    return cards


def _pending_form_payload(post):
    """Serialize POST fields for encrypted, short-lived pending storage."""
    excluded = {"csrfmiddlewaretoken", "operation_id"}
    return {key: post.getlist(key) for key in post.keys() if key not in excluded}


def _pending_form_data(operation):
    data = QueryDict("", mutable=True)
    for key, values in protected_payload(operation).items():
        if isinstance(values, list):
            data.setlist(key, [str(value) for value in values])
        else:
            data[key] = str(values)
    return data


def _scrub_protected_form_values(form):
    """Never echo protected card values back into HTML after validation."""
    if form.is_bound:
        safe_data = form.data.copy()
        for field_name in ("pan", "expiry", "company"):
            if field_name in safe_data:
                safe_data[field_name] = ""
        form.data = safe_data
    return form


def _protected_error(status, error_code, message):
    return JsonResponse(
        {"ok": False, "error_code": error_code, "message": message},
        status=status,
    )


@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def card_list(request):
    gate = _enforce_schedule(request, "VIEW")
    if hasattr(gate, "status_code"): return gate
    close_operation_contexts(request, "Usuario regresó al listado de la Bóveda")
    search_form = CardSearchForm(request.GET)
    cards = _cards_for_operator(request)
    query = ""
    if search_form.is_valid():
        query = search_form.cleaned_data["q"]
        if query:
            filters = Q(client_name__icontains=query) | Q(cardholder_name__icontains=query) | Q(purpose__icontains=query) | Q(brand__icontains=query) | Q(last4__icontains=query)
            if query.isdigit():
                filters |= Q(pk=int(query))
            if query.casefold() in {"activa", "activo"}:
                filters |= Q(active=True)
            elif query.casefold() in {"inactiva", "inactivo"}:
                filters |= Q(active=False)
            cards = cards.filter(filters)
    else:
        cards = cards.none()
    page = Paginator(cards, 25).get_page(request.GET.get("page"))
    context = {"cards": page.object_list, "page": page, "search_form": search_form, "query": query}
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render(request, "vault/_card_results.html", context)
    return render(request, "vault/card_list.html", context)


@role_required(UserProfile.LEADER)
def card_create(request):
    gate = _enforce_schedule(request, "CREATE")
    if hasattr(gate, "status_code"):
        return gate
    form = CardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            operation = prepare_operation(
                request,
                operation_id=request.POST.get("operation_id") or new_operation_id(),
                action="CARD_CREATE",
                purpose="cards_manage",
                target_type="card_new",
                target_id=0,
                reason="Creación de tarjeta",
                success_url=reverse("vault:card_list"),
                protected_payload=_pending_form_payload(request.POST),
            )
            if not has_recent_reauth(request, "cards_manage"):
                return redirect(reauthentication_url(operation))
            return execute_operation(request, operation.public_id)
        except PendingOperationError as exc:
            messages.error(request, str(exc))
            return redirect("vault:card_create")
    if request.method == "POST":
        _scrub_protected_form_values(form)
    return render(request, "vault/card_form.html", {
        "form": form,
        "title": "Nueva tarjeta",
        "operation_id": request.POST.get("operation_id") or new_operation_id(),
    })


@role_required(UserProfile.LEADER)
def card_edit(request, pk):
    gate = _enforce_schedule(request, "UPDATE")
    if hasattr(gate, "status_code"):
        return gate
    card = get_object_or_404(PaymentCard, pk=pk)
    form = CardEditForm(request.POST or None, instance=card)
    if request.method == "POST" and form.is_valid():
        try:
            operation = prepare_operation(
                request,
                operation_id=request.POST.get("operation_id") or new_operation_id(),
                action="CARD_EDIT",
                purpose="cards_manage",
                target_type="card",
                target_id=card.pk,
                reason="Actualización de tarjeta",
                success_url=reverse("vault:card_detail", args=[card.pk]),
                protected_payload=_pending_form_payload(request.POST),
            )
            if not has_recent_reauth(request, "cards_manage"):
                return redirect(reauthentication_url(operation))
            return execute_operation(request, operation.public_id)
        except PendingOperationError as exc:
            messages.error(request, str(exc))
            return redirect("vault:card_edit", pk=card.pk)
    if request.method == "POST":
        _scrub_protected_form_values(form)
    return render(request, "vault/card_form.html", {
        "form": form,
        "title": "Editar tarjeta",
        "operation_id": request.POST.get("operation_id") or new_operation_id(),
    })


def execute_pending_card_create(request, operation):
    if request.user.vault_profile.role != UserProfile.LEADER or operation.target_type != "card_new":
        raise PermissionDenied
    form = CardForm(_pending_form_data(operation))
    if not form.is_valid():
        raise PendingOperationError("Los datos pendientes ya no son válidos. Diligencie nuevamente el formulario.")
    card = form.save(user=request.user)
    audit(
        request,
        "CREATE",
        card,
        reason=operation.reason,
        metadata={
            "fields": ["client_name", "cardholder_name", "brand", "purpose", "pan", "expiry", "company"],
            "operation_id": str(operation.public_id),
        },
    )
    operation.success_url = reverse("vault:card_detail", args=[card.pk])
    operation.save(update_fields=["success_url"])
    messages.success(request, f"Tarjeta •••• {card.last4} registrada y cifrada.")
    return redirect(operation.success_url)


def execute_pending_card_edit(request, operation):
    if request.user.vault_profile.role != UserProfile.LEADER or operation.target_type != "card":
        raise PermissionDenied
    card = get_object_or_404(PaymentCard.objects.select_for_update(), pk=operation.target_id)
    form = CardEditForm(_pending_form_data(operation), instance=card)
    if not form.is_valid():
        raise PendingOperationError("Los datos pendientes ya no son válidos. Diligencie nuevamente el formulario.")
    changed_fields = list(form.changed_data)
    card = form.save(user=request.user)
    audit(
        request,
        "UPDATE",
        card,
        reason=operation.reason,
        metadata={"changed_fields": changed_fields, "operation_id": str(operation.public_id)},
    )
    messages.success(request, f"Tarjeta •••• {card.last4} actualizada.")
    return redirect(operation.success_url)


@require_POST
@role_required(UserProfile.LEADER)
def card_deactivate(request, pk):
    _enforce_schedule(request, "DEACTIVATE")
    card = get_object_or_404(PaymentCard, pk=pk)
    try:
        operation = prepare_operation(
            request,
            operation_id=request.POST.get("operation_id") or new_operation_id(),
            action="CARD_DEACTIVATE",
            purpose="cards_manage",
            target_type="card",
            target_id=card.pk,
            reason="Desactivación lógica",
            success_url=reverse("vault:card_list"),
        )
    except PendingOperationError as exc:
        messages.error(request, str(exc))
        return redirect("vault:card_detail", pk=card.pk)
    if operation.status == PendingSensitiveOperation.COMPLETED:
        messages.info(request, "La tarjeta ya había sido desactivada; no se ejecutó nuevamente.")
        return redirect(operation.success_url)
    if not has_recent_reauth(request, "cards_manage"):
        return redirect(reauthentication_url(operation))
    return execute_operation(request, operation.public_id)


def execute_pending_card_deactivate(request, operation):
    if request.user.vault_profile.role != UserProfile.LEADER or operation.target_type != "card":
        raise PermissionDenied
    with transaction.atomic():
        card = get_object_or_404(PaymentCard.objects.select_for_update(), pk=operation.target_id)
        if not card.active:
            messages.info(request, "La tarjeta ya se encontraba inactiva.")
            return redirect(operation.success_url)
        card.active = False
        card.updated_by = request.user
        card.save(update_fields=["active", "updated_by", "updated_at"])
        audit(
            request,
            "DEACTIVATE",
            card,
            reason=operation.reason,
            metadata={"operation_id": str(operation.public_id)},
        )
    messages.success(request, f"Tarjeta •••• {card.last4} desactivada.")
    return redirect(operation.success_url)


@never_cache
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def card_detail(request, pk):
    gate = _enforce_schedule(request, "VIEW")
    if hasattr(gate, "status_code"): return gate
    cards = PaymentCard.objects.all()
    if request.user.vault_profile.role == UserProfile.ANALYST:
        cards = cards.filter(active=True)
    card = get_object_or_404(cards, pk=pk)
    audit(request, "VIEW", card)
    return render(request, "vault/card_detail.html", {
        "card": card,
        "identity_window_active": bool(current_operation_window(request)),
        "operation_context_active": bool(current_operation_context(request, card)),
        "deactivate_operation_id": new_operation_id(),
    })


@never_cache
@require_POST
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def reveal(request, pk):
    _enforce_schedule(request, "REVEAL")
    card = PaymentCard.objects.filter(pk=pk, active=True).first()
    if not card:
        return _protected_error(
            404,
            "CARD_NOT_AVAILABLE",
            "La tarjeta ya no está disponible para esta operación.",
        )
    form = ProtectedActionForm(request.POST)
    if not form.is_valid():
        return _protected_error(
            422,
            "INVALID_PROTECTED_ACTION",
            "La solicitud de revelado o copia no es válida.",
        )
    field = form.cleaned_data["field"]
    action = form.cleaned_data["action"]
    context = current_operation_context(request, card)
    if not context:
        window = current_operation_window(request)
        intent = create_intent(request, card, field, action, identity_verified=bool(window))
        if window:
            form_html = render_to_string("vault/security/_operation_context.html", {"form": OperationContextForm(), "intent": intent}, request=request)
            stage = "context"
        else:
            form_html = render_to_string("vault/security/_protected_identity.html", {"form": ReauthenticationForm(), "intent": intent}, request=request)
            stage = "identity"
        return JsonResponse({"ok": False, "authorization_required": True, "stage": stage, "intent": intent, "form_html": form_html}, status=428)
    try:
        value = {"company": card.get_company, "pan": card.get_pan, "expiry": card.get_expiry}[field]()
    except ValueError:
        audit(request, "REVEAL" if action == "reveal" else "COPY_ATTEMPT", card, field, result="FAILED", risk_level="HIGH", reason="No fue posible recuperar el dato protegido")
        return _protected_error(
            422,
            "PROTECTED_VALUE_UNAVAILABLE",
            "No fue posible recuperar el dato protegido.",
        )
    if field == "company" and not value:
        return _protected_error(
            404,
            "COMPANY_NOT_CONFIGURED",
            "La empresa no está configurada para esta tarjeta.",
        )
    token = create_reveal_grant(request, card, field, context)
    metadata = {
        "reference": context.internal_reference,
        "context_id": str(context.public_id),
        "window_id": str(context.identity_window.public_id),
    }
    if action == "reveal":
        audit(request, "REVEAL", card, field, context.reason, metadata=metadata)
    response = HttpResponse(value, content_type="text/plain; charset=utf-8")
    response["X-Vault-Field"] = field
    response["X-Vault-Action"] = action
    response["X-Vault-Copy-Token"] = token
    response["X-Vault-Expires-In"] = "20"
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@never_cache
@require_POST
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def protected_reauthenticate(request):
    intent_token = request.POST.get("intent", "")
    intent = get_intent(request, intent_token)
    if not intent or not _cards_for_operator(request).filter(pk=intent["card_id"], active=True).exists():
        return _protected_error(
            409,
            "PROTECTED_INTENT_EXPIRED",
            "La solicitud expiró. Inicie nuevamente la operación.",
        )
    form = ReauthenticationForm(request.POST)
    if form.is_valid() and request.user.check_password(form.cleaned_data["password"]) and verify_totp(request.user, form.cleaned_data["token"]):
        window = create_operation_window(request)
        if not window:
            raise PermissionDenied
        mark_identity_verified(request, intent_token)
        audit(request, "REAUTH_SUCCESS", reason="protected_data", metadata={"field": intent["field"], "action": intent["action"], "window_id": str(window.public_id)})
        return HttpResponse(render_to_string("vault/security/_operation_context.html", {"form": OperationContextForm(), "intent": intent_token}, request=request), content_type="text/html; charset=utf-8")
    event = audit(request, "REAUTH_FAILED", reason="protected_data", result="FAILED", risk_level="HIGH")
    secure_session = current_secure_session(request)
    create_alert(request, event, "REAUTH_FAILED", "HIGH", request.user, getattr(secure_session, "device", None), "Reautenticación fallida.")
    form.add_error(None, "No fue posible validar la identidad.")
    return HttpResponse(render_to_string("vault/security/_protected_identity.html", {"form": form, "intent": intent_token}, request=request), status=422, content_type="text/html; charset=utf-8")


@never_cache
@require_POST
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def protected_confirm(request):
    intent_token = request.POST.get("intent", "")
    intent = get_intent(request, intent_token, require_identity=True)
    card = _cards_for_operator(request).filter(pk=intent["card_id"], active=True).first() if intent else None
    if not intent or not card:
        return _protected_error(
            409,
            "PROTECTED_INTENT_EXPIRED",
            "La solicitud expiró. Inicie nuevamente la operación.",
        )
    form = OperationContextForm(request.POST)
    if form.is_valid() and card.has_company:
        company = card.get_company().strip().casefold()
        for field_name in ("reason", "reference"):
            supplied = form.cleaned_data[field_name].casefold()
            if company and company in supplied:
                form.add_error(field_name, "No incluya datos protegidos en este campo.")
    if not form.is_valid():
        return HttpResponse(render_to_string("vault/security/_operation_context.html", {"form": form, "intent": intent_token}, request=request), status=422, content_type="text/html; charset=utf-8")
    window = current_operation_window(request)
    if not window:
        return _protected_error(
            409,
            "SENSITIVE_WINDOW_EXPIRED",
            "La reautenticación expiró. Inicie nuevamente la operación.",
        )
    context = create_operation_context(request, window, card, form.cleaned_data["reason"], form.cleaned_data["reference"])
    if not context:
        raise PermissionDenied
    clear_intent(request)
    return JsonResponse({"ok": True, "identity_expires_at": window.expires_at.isoformat(), "context_id": str(context.public_id)})


@never_cache
@require_POST
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def copy_event(request, pk):
    _enforce_schedule(request, "COPY")
    card = PaymentCard.objects.filter(pk=pk, active=True).first()
    if not card:
        return _protected_error(
            404,
            "CARD_NOT_AVAILABLE",
            "La tarjeta ya no está disponible para esta operación.",
        )
    grant = consume_copy_grant(request, card, request.POST.get("copy_token", ""))
    if not grant:
        audit(request, "COPY_ATTEMPT", card, result="DENIED", risk_level="HIGH", reason="Autorización de copia inválida o expirada")
        return _protected_error(
            409,
            "REVEAL_GRANT_EXPIRED",
            "La autorización de copia ya fue utilizada o expiró.",
        )
    result = "SUCCESS" if request.POST.get("result") == "success" else "FAILED"
    context = grant.operation_context
    audit(request, "COPY", card, grant.field_name, context.reason, metadata={
        "reference": context.internal_reference,
        "context_id": str(context.public_id),
        "window_id": str(context.identity_window.public_id),
    }, result=result, risk_level="LOW" if result == "SUCCESS" else "HIGH")
    return JsonResponse({"ok": result == "SUCCESS"}, status=200 if result == "SUCCESS" else 400)


@role_required(UserProfile.ADMIN)
def audit_list(request):
    return redirect("vault:timeline")


@never_cache
def healthz(request):
    try:
        connection.ensure_connection()
        db_ok = connection.is_usable()
    except Exception:
        db_ok = False
    return JsonResponse({"status": "ok" if db_ok else "error"}, status=200 if db_ok else 503)
