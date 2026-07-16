from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied

from .decorators import role_required
from .forms import CardEditForm, CardForm, RevealForm
from .models import AuditEvent, PaymentCard, SecurityAlert, UserProfile
from .security import audit, consume_copy_grant, create_reveal_grant, verify_audit_chain
from .identity import has_recent_reauth
from django.urls import reverse
from .policies import evaluate_access_policy


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
    chain_ok, chain_position = verify_audit_chain()
    operational_actions = ["VIEW", "REVEAL", "COPY", "COPY_ATTEMPT", "CREATE", "UPDATE", "DEACTIVATE", "DENIED"]
    if profile.role == UserProfile.LEADER:
        recent = AuditEvent.objects.select_related("user", "card").filter(action__in=operational_actions)
        visible_alerts = SecurityAlert.objects.filter(Q(affected_user=request.user) | Q(alert_type__in=["COPY", "REVEAL", "OUTSIDE_HOURS", "CRITICAL_BLOCKED"]))
    else:
        recent = AuditEvent.objects.select_related("user", "card").filter(user=request.user)
        visible_alerts = SecurityAlert.objects.filter(affected_user=request.user)
    context = {
        "cards": PaymentCard.objects.filter(active=True).count(),
        "alerts": visible_alerts.filter(status="NEW").count(),
        "recent": recent[:10],
        "chain_ok": chain_ok,
        "chain_position": chain_position,
        "usage_7": AuditEvent.objects.filter(user=request.user, action__in=["VIEW", "REVEAL", "COPY", "CREATE", "UPDATE"], created_at__gte=timezone.now()-timedelta(days=7)).count(),
        "usage_30": AuditEvent.objects.filter(user=request.user, action__in=["VIEW", "REVEAL", "COPY", "CREATE", "UPDATE"], created_at__gte=timezone.now()-timedelta(days=30)).count(),
        "usage_90": AuditEvent.objects.filter(user=request.user, action__in=["VIEW", "REVEAL", "COPY", "CREATE", "UPDATE"], created_at__gte=timezone.now()-timedelta(days=90)).count(),
        "last_reveal": AuditEvent.objects.filter(user=request.user, action="REVEAL").order_by("-created_at").first(),
        "last_copy": AuditEvent.objects.filter(user=request.user, action="COPY", result="SUCCESS").order_by("-created_at").first(),
        "outside_access": AuditEvent.objects.filter(user=request.user, action="LOGIN", outside_office_hours=True, created_at__gte=timezone.now()-timedelta(days=30)).count(),
    }
    return render(request, "vault/dashboard.html", context)


@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def card_list(request):
    gate = _enforce_schedule(request, "VIEW")
    if hasattr(gate, "status_code"): return gate
    cards = PaymentCard.objects.order_by("client_name")
    if request.user.vault_profile.role == UserProfile.ANALYST:
        cards = cards.filter(active=True)
    return render(request, "vault/card_list.html", {"cards": cards})


@role_required(UserProfile.LEADER)
def card_create(request):
    _enforce_schedule(request, "CREATE")
    if not has_recent_reauth(request, "cards_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=cards_manage&next={request.path}")
    form = CardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            card = form.save(user=request.user)
            audit(request, "CREATE", card, reason="Registro de tarjeta", metadata={"fields": ["client_name", "cardholder_name", "brand", "purpose", "pan", "expiry"]})
        messages.success(request, f"Tarjeta •••• {card.last4} registrada y cifrada.")
        return redirect("vault:card_detail", card.pk)
    return render(request, "vault/card_form.html", {"form": form, "title": "Nueva tarjeta"})


@role_required(UserProfile.LEADER)
def card_edit(request, pk):
    _enforce_schedule(request, "UPDATE")
    if not has_recent_reauth(request, "cards_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=cards_manage&next={request.path}")
    card = get_object_or_404(PaymentCard, pk=pk)
    form = CardEditForm(request.POST or None, instance=card)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            card = form.save(user=request.user)
            audit(request, "UPDATE", card, reason="Actualización de tarjeta", metadata={"changed_fields": list(form.changed_data)})
        messages.success(request, f"Tarjeta •••• {card.last4} actualizada.")
        return redirect("vault:card_detail", card.pk)
    return render(request, "vault/card_form.html", {"form": form, "title": "Editar tarjeta"})


@require_POST
@role_required(UserProfile.LEADER)
def card_deactivate(request, pk):
    _enforce_schedule(request, "DEACTIVATE")
    if not has_recent_reauth(request, "cards_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=cards_manage&next={reverse('vault:card_detail', args=[pk])}")
    with transaction.atomic():
        card = get_object_or_404(PaymentCard.objects.select_for_update(), pk=pk, active=True)
        card.active = False
        card.updated_by = request.user
        card.save(update_fields=["active", "updated_by", "updated_at"])
        audit(request, "DEACTIVATE", card, reason="Desactivación lógica")
    messages.success(request, f"Tarjeta •••• {card.last4} desactivada.")
    return redirect("vault:card_list")


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
    events = card.auditevent_set.select_related("user")[:15] if request.user.vault_profile.role == UserProfile.LEADER else []
    return render(request, "vault/card_detail.html", {"card": card, "form": RevealForm(user=request.user), "events": events})


@never_cache
@require_POST
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def reveal(request, pk):
    _enforce_schedule(request, "REVEAL")
    card = get_object_or_404(PaymentCard, pk=pk, active=True)
    if not has_recent_reauth(request, "reveal"):
        audit(request, "CRITICAL_BLOCKED", card, result="DENIED", risk_level="HIGH", reason="Reautenticación requerida")
        return JsonResponse({"ok": False, "reauth_required": True, "reauth_url": f"{reverse('vault:reauthenticate')}?purpose=reveal&next={reverse('vault:card_detail', args=[pk])}"}, status=428)
    form = RevealForm(request.POST, user=request.user)
    if not form.is_valid():
        audit(request, "REVEAL", card, reason="Validación de revelado fallida", result="DENIED", risk_level="HIGH")
        return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
    field = form.cleaned_data["field"]
    reason = form.cleaned_data["reason"]
    value = card.get_pan() if field == "pan" else card.get_expiry()
    token = create_reveal_grant(request, card, field, reason)
    audit(request, "REVEAL", card, field, reason, metadata={"reference": form.cleaned_data["reference"]})
    response = JsonResponse({"ok": True, "field": field, "value": value, "copy_token": token, "expires_in": 25})
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["Pragma"] = "no-cache"
    return response


@never_cache
@require_POST
@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def copy_event(request, pk):
    _enforce_schedule(request, "COPY")
    card = get_object_or_404(PaymentCard, pk=pk, active=True)
    grant = consume_copy_grant(request, card, request.POST.get("copy_token", ""))
    if not grant:
        audit(request, "COPY_ATTEMPT", card, result="DENIED", risk_level="HIGH", reason="Autorización de copia inválida o expirada")
        return JsonResponse({"ok": False, "error": "Autorización inválida o expirada."}, status=403)
    result = "SUCCESS" if request.POST.get("result") == "success" else "FAILED"
    audit(request, "COPY", card, grant.field_name, grant.reason, result=result, risk_level="LOW" if result == "SUCCESS" else "HIGH")
    return JsonResponse({"ok": result == "SUCCESS"}, status=200 if result == "SUCCESS" else 400)


@role_required(UserProfile.ADMIN, UserProfile.LEADER, UserProfile.ANALYST)
def audit_list(request):
    return redirect("vault:timeline")
