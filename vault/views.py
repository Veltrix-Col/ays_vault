from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .decorators import role_required
from .forms import CardEditForm, CardForm, RevealForm
from .models import AuditEvent, PaymentCard, SecurityAlert, UserProfile
from .security import audit, consume_copy_grant, create_reveal_grant, verify_audit_chain
from .identity import has_recent_reauth
from django.urls import reverse


def _active_profile(request):
    profile = getattr(request.user, "vault_profile", None)
    return profile if profile and profile.active and profile.role else None


@login_required
def dashboard(request):
    profile = _active_profile(request)
    if not profile:
        audit(request, "DENIED", result="DENIED", risk_level="HIGH", metadata={"reason": "profile_inactive_or_unassigned"})
        return render(request, "vault/access_denied.html", status=403)
    today = timezone.localdate()
    chain_ok, chain_position = verify_audit_chain()
    context = {
        "cards": PaymentCard.objects.filter(active=True).count(),
        "events_today": AuditEvent.objects.filter(created_at__date=today).count(),
        "alerts": SecurityAlert.objects.filter(status="NEW").count(),
        "recent": AuditEvent.objects.select_related("user", "card")[:10],
        "chain_ok": chain_ok,
        "chain_position": chain_position,
    }
    return render(request, "vault/dashboard.html", context)


@role_required(UserProfile.LEADER, UserProfile.ANALYST)
def card_list(request):
    cards = PaymentCard.objects.order_by("client_name")
    if request.user.vault_profile.role == UserProfile.ANALYST:
        cards = cards.filter(active=True)
    return render(request, "vault/card_list.html", {"cards": cards})


@role_required(UserProfile.LEADER)
def card_create(request):
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
    card = get_object_or_404(PaymentCard, pk=pk, active=True)
    grant = consume_copy_grant(request, card, request.POST.get("copy_token", ""))
    if not grant:
        audit(request, "COPY_ATTEMPT", card, result="DENIED", risk_level="HIGH", reason="Autorización de copia inválida o expirada")
        return JsonResponse({"ok": False, "error": "Autorización inválida o expirada."}, status=403)
    result = "SUCCESS" if request.POST.get("result") == "success" else "FAILED"
    audit(request, "COPY", card, grant.field_name, grant.reason, result=result, risk_level="LOW" if result == "SUCCESS" else "HIGH")
    return JsonResponse({"ok": result == "SUCCESS"}, status=200 if result == "SUCCESS" else 400)


@role_required(UserProfile.ADMIN, UserProfile.LEADER)
def audit_list(request):
    return render(request, "vault/audit.html", {"events": AuditEvent.objects.select_related("user", "card")[:250]})
