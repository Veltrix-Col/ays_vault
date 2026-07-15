import hashlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import AccessException, AuditEvent, PaymentCard, PolicyConfiguration, SecurityAlert, UserDevice, UserProfile
from .notifications import notify_alert
from .policies import get_policy
from .security import audit, verify_audit_chain


def _key(kind, scope, period):
    return hashlib.sha256(f"{kind}:{scope}:{period}".encode()).hexdigest()


def _create_alert(kind, severity, description, recommendation, period, user=None, metadata=None, dry_run=False):
    identifier = _key(kind, user.pk if user else "system", period)
    if SecurityAlert.objects.filter(idempotency_key=identifier).exists():
        return False
    if dry_run:
        return True
    event = audit(None, "POLICY_EVALUATION", user=user, reason=kind, metadata=metadata or {}, risk_level=severity)
    policy = get_policy()
    alert = SecurityAlert.objects.create(
        event=event,
        alert_type=kind,
        severity=severity,
        affected_user=user,
        description=description[:240],
        safe_metadata=metadata or {},
        recommendation=recommendation[:300],
        due_at=timezone.now() + timedelta(hours=policy.alert_review_hours),
        policy=policy if policy.pk else None,
        idempotency_key=identifier,
    )
    transaction.on_commit(lambda: notify_alert(alert))
    return True


def evaluate_security_policies(dry_run=False, now=None):
    now = now or timezone.now()
    policy = get_policy()
    results = {"created": 0, "existing": 0, "expired_exceptions": 0, "checks": []}
    if not dry_run:
        results["expired_exceptions"] = AccessException.objects.filter(status=AccessException.ACTIVE, ends_at__lt=now).update(status=AccessException.EXPIRED)

    vacations = AccessException.objects.filter(status=AccessException.ACTIVE, exception_type=AccessException.VACATION, starts_at__lte=now, ends_at__gte=now).exists()
    active_cards = PaymentCard.objects.filter(active=True).exists()
    last_operational = AuditEvent.objects.filter(action__in=["REVEAL", "COPY", "CREATE", "UPDATE", "VIEW"]).order_by("-created_at").first()
    operational_days = (now - last_operational.created_at).days if last_operational else None
    if active_cards and not vacations and (operational_days is None or operational_days >= policy.inactivity_general_days):
        days = operational_days if operational_days is not None else policy.inactivity_general_days
        created = _create_alert(
            "POSSIBLE_PARALLEL_TOOL_USE", "HIGH",
            "El sistema no presenta actividad operativa reciente; validar adopcion del proceso.",
            "Confirmar con el area si la baja actividad es esperada o si existe uso paralelo de Excel u otra herramienta no autorizada.",
            now.date().isoformat(), metadata={"days_without_use": days, "active_cards": True}, dry_run=dry_run,
        )
        results["created" if created else "existing"] += 1
    results["checks"].append("system_inactivity")

    users = get_user_model().objects.filter(is_active=True, vault_profile__active=True).select_related("vault_profile")
    for user in users:
        last_login = AuditEvent.objects.filter(user=user, action="LOGIN").order_by("-created_at").first()
        days = (now - last_login.created_at).days if last_login else policy.inactive_user_days
        threshold = policy.operational_user_days if last_login and user.vault_profile.role in {UserProfile.LEADER, UserProfile.ANALYST} else policy.inactive_user_days
        if days >= threshold:
            created = _create_alert(
                "INACTIVE_USER", "MEDIUM", "Usuario activo sin ingreso reciente.",
                "Validar necesidad de acceso, vacaciones y continuidad del rol.",
                now.strftime("%Y-%m"), user=user, metadata={"days_without_login": days}, dry_run=dry_run,
            )
            results["created" if created else "existing"] += 1
        if not user.vault_profile.mfa_enabled:
            created = _create_alert("USER_WITHOUT_MFA", "CRITICAL", "Usuario activo sin MFA habilitado.", "Completar o reiniciar el enrolamiento MFA.", now.date().isoformat(), user=user, dry_run=dry_run)
            results["created" if created else "existing"] += 1
    results["checks"].append("users")

    soon = now + timedelta(days=7)
    for exception in AccessException.objects.filter(status=AccessException.ACTIVE, ends_at__gt=now, ends_at__lte=soon):
        created = _create_alert("EXCEPTION_EXPIRING", "LOW", "Excepcion administrativa proxima a vencer.", "Confirmar que no requiere reemplazo y permitir su expiracion automatica.", exception.ends_at.date().isoformat(), metadata={"exception_id": exception.pk}, dry_run=dry_run)
        results["created" if created else "existing"] += 1
    for device in UserDevice.objects.filter(status=UserDevice.TRUSTED, trusted_until__gt=now, trusted_until__lte=soon):
        created = _create_alert("TRUSTED_DEVICE_EXPIRING", "LOW", "La confianza de un dispositivo vence pronto.", "Revisar si el dispositivo sigue autorizado.", device.trusted_until.date().isoformat(), user=device.user, metadata={"device_id": device.pk}, dry_run=dry_run)
        results["created" if created else "existing"] += 1
    results["checks"].append("expirations")

    overdue = SecurityAlert.objects.exclude(status__in=["CLOSED", "JUSTIFIED"]).filter(due_at__lt=now)
    if not dry_run:
        for alert in overdue:
            alert.status = "ESCALATED"
            alert.save(update_fields=["status"])
    results["overdue"] = overdue.count()

    valid, position = verify_audit_chain()
    if not valid:
        created = _create_alert("AUDIT_INTEGRITY_FAILURE", "CRITICAL", "La verificacion de integridad de auditoria fallo.", "Detener operaciones sensibles y escalar a seguridad.", now.date().isoformat(), metadata={"position": position}, dry_run=dry_run)
        results["created" if created else "existing"] += 1
    results["audit_chain"] = {"valid": valid, "position": position}
    results["configuration"] = {"policy_present": PolicyConfiguration.objects.filter(singleton=1, enabled=True).exists()}
    return results
