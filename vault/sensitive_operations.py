import json
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .models import PendingSensitiveOperation
from .crypto import decrypt, encrypt
from .security import session_hash


OPERATION_TTL_MINUTES = 15
HANDLERS = {
    "MFA_RESET": "vault.security_views.execute_pending_mfa_reset",
    "CARD_DEACTIVATE": "vault.views.execute_pending_card_deactivate",
    "CARD_CREATE": "vault.views.execute_pending_card_create",
    "CARD_EDIT": "vault.views.execute_pending_card_edit",
}


class PendingOperationError(Exception):
    pass


def new_operation_id():
    return str(uuid.uuid4())


def _parse_operation_id(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise PendingOperationError("La confirmación de la operación no es válida.") from None


def prepare_operation(
    request,
    *,
    operation_id,
    action,
    purpose,
    target_type,
    target_id,
    reason,
    success_url,
    safe_payload=None,
    protected_payload=None,
):
    public_id = _parse_operation_id(operation_id)
    defaults = {
        "user": request.user,
        "session_hash": session_hash(request),
        "purpose": purpose,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
        "safe_payload": safe_payload or {},
        "encrypted_payload": encrypt(json.dumps(protected_payload, ensure_ascii=False)) if protected_payload else "",
        "success_url": success_url,
        "expires_at": timezone.now() + timedelta(minutes=OPERATION_TTL_MINUTES),
    }
    operation, created = PendingSensitiveOperation.objects.get_or_create(public_id=public_id, defaults=defaults)
    if not created:
        expected = (
            operation.user_id == request.user.pk
            and operation.session_hash == session_hash(request)
            and operation.purpose == purpose
            and operation.action == action
            and operation.target_type == target_type
            and operation.target_id == target_id
        )
        if not expected:
            raise PermissionDenied
    return operation


def protected_payload(operation):
    if not operation.encrypted_payload:
        return {}
    try:
        value = json.loads(decrypt(operation.encrypted_payload))
    except (TypeError, ValueError) as exc:
        raise PendingOperationError("El contenido protegido de la operación no se pudo validar.") from exc
    if not isinstance(value, dict):
        raise PendingOperationError("El contenido protegido de la operación no es válido.")
    return value


def operation_for_reauthentication(request, token, purpose):
    try:
        public_id = _parse_operation_id(token)
    except PendingOperationError:
        return None
    return PendingSensitiveOperation.objects.filter(
        public_id=public_id,
        user=request.user,
        session_hash=session_hash(request),
        purpose=purpose,
    ).first()


def reauthentication_url(operation):
    query = urlencode({"purpose": operation.purpose, "operation": str(operation.public_id)})
    return f"{reverse('vault:reauthenticate')}?{query}"


def _load_handler(path):
    module_name, function_name = path.rsplit(".", 1)
    module = __import__(module_name, fromlist=[function_name])
    return getattr(module, function_name)


def execute_operation(request, token):
    public_id = _parse_operation_id(token)
    with transaction.atomic():
        operation = (
            PendingSensitiveOperation.objects.select_for_update()
            .filter(public_id=public_id, user=request.user)
            .first()
        )
        if not operation:
            raise PermissionDenied
        if operation.session_hash != session_hash(request):
            raise PermissionDenied
        if operation.status == PendingSensitiveOperation.COMPLETED:
            messages.info(request, "La operación ya había sido completada; no se ejecutó nuevamente.")
            return redirect(operation.success_url)
        if operation.expires_at <= timezone.now():
            operation.encrypted_payload = ""
            operation.save(update_fields=["encrypted_payload"])
            messages.error(request, "La operación pendiente expiró. Iníciela nuevamente.")
            return redirect(operation.success_url)

        from .identity import has_recent_reauth

        if not has_recent_reauth(request, operation.purpose):
            raise PendingOperationError("La reautenticación de esta operación no está vigente.")
        handler_path = HANDLERS.get(operation.action)
        if not handler_path:
            raise PendingOperationError("La operación pendiente no está soportada.")

        claimed = PendingSensitiveOperation.objects.filter(
            pk=operation.pk,
            status=PendingSensitiveOperation.PENDING,
            consumed_at__isnull=True,
        ).update(status=PendingSensitiveOperation.PROCESSING)
        if not claimed:
            messages.info(request, "La operación ya está siendo procesada.")
            return redirect(operation.success_url)

        response = _load_handler(handler_path)(request, operation)
        operation.status = PendingSensitiveOperation.COMPLETED
        operation.consumed_at = timezone.now()
        operation.encrypted_payload = ""
        operation.save(update_fields=["status", "consumed_at", "encrypted_payload"])
        return response
