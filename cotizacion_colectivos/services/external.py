from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from vault.crypto import decrypt, encrypt
from vault.notifications import send_notification
from .otp_email import build_otp_email
from .mappings import (
    CONTACT_ID_TYPE_CHOICES,
    INSURED_STATE_CHOICES,
    RELATION_ROLE_CHOICES,
    RELATIONSHIP_CHOICES,
)

from ..models import (
    AccesoExternoSolicitudColectivo,
    CambioSolicitudColectivo,
    EventoSolicitudColectivo,
    NotificacionColectivos,
    RespuestaSolicitudColectivo,
    SolicitudColectivo,
)
from .task_publisher import ColectivosTaskPayload, enqueue_task

EXTERNAL_COOKIE = "colectivos_external_session"
SESSION_SALT = "cotizacion_colectivos.external_session.v1"
ALLOWED_ACTIONS = set(CambioSolicitudColectivo.Action.values)
ACTION_TO_ADJUSTMENT = {
    CambioSolicitudColectivo.Action.UNCHANGED: "SIN_CAMBIOS",
    CambioSolicitudColectivo.Action.MODIFY: "MODIFICACION",
    CambioSolicitudColectivo.Action.RETIRE: "RETIRO",
    CambioSolicitudColectivo.Action.INCLUDE: "INCLUSION",
}
EDITABLE_FIELDS = {
    "tipo_id", "documento", "nombre", "nombres", "apellidos", "rol", "plan", "parentesco",
    "fecha_nacimiento", "fecha_efectiva", "fecha_ingreso", "fecha_retiro", "motivo",
    "observaciones", "ciudad", "direccion", "tipo_uso",
    "anio_construccion", "descripcion", "valor_asegurado", "vehiculo",
    "placa", "marca", "modelo", "estado",
}


class ExternalAccessError(ValidationError):
    pass


class ActiveAccessExistsError(ExternalAccessError):
    """The full secret is unavailable, but a usable access already exists."""


@dataclass(frozen=True)
class GeneratedAccess:
    access: AccesoExternoSolicitudColectivo
    token: str
    url: str
    regenerated: bool = False


def _token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _recipient_hash(email: str) -> str:
    return salted_hmac("colectivos.external.recipient.v1", email.strip().casefold(), secret=settings.SECRET_KEY).hexdigest()


def _notify(request: SolicitudColectivo, kind: str, title: str, message: str, suffix: str) -> None:
    NotificacionColectivos.objects.get_or_create(
        user=request.assigned_to,
        deduplication_key=f"{kind}:{request.uuid}:{suffix}",
        defaults={"request": request, "notification_type": kind, "title": title, "message": message, "priority": "NORMAL"},
    )


@transaction.atomic
def generate_access(*, request: SolicitudColectivo, actor, recipient: str = "", contact_name: str = "", intro: str = "", instructions: str = "", regenerate: bool = False) -> GeneratedAccess:
    locked = SolicitudColectivo.objects.select_for_update().get(pk=request.pk)
    if locked.status in {locked.Status.CLOSED, locked.Status.CANCELLED, locked.Status.EXPIRED}:
        raise ExternalAccessError("La solicitud no admite un nuevo acceso.")
    if locked.status == locked.Status.DRAFT:
        locked.transition_to(locked.Status.READY)
        locked.save(update_fields=("status", "updated_at"))
    if locked.deadline <= timezone.localdate() or not locked.encrypted_snapshot or not locked.assigned_to_id:
        raise ExternalAccessError("La solicitud no cumple las condiciones de envío.")
    now = timezone.now()
    access_candidates = locked.external_accesses.select_for_update().filter(
        status__in=[
            AccesoExternoSolicitudColectivo.Status.ACTIVE,
            AccesoExternoSolicitudColectivo.Status.VERIFIED,
        ]
    )
    access_candidates.filter(expires_at__lte=now).update(
        status=AccesoExternoSolicitudColectivo.Status.EXPIRED
    )
    active = access_candidates.filter(expires_at__gt=now)
    if active.exists() and not regenerate:
        raise ActiveAccessExistsError("Ya existe un acceso externo activo.")
    active.update(status=AccesoExternoSolicitudColectivo.Status.REVOKED, revoked_at=now, revoked_by=actor)
    selector = secrets.token_urlsafe(18)[:24]
    secret = secrets.token_urlsafe(32)
    next_version = (locked.external_accesses.order_by("-version").values_list("version", flat=True).first() or 0) + 1
    configured_expiry = now + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS)
    maximum_expiry = now + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS)
    deadline_expiry = timezone.make_aware(datetime.combine(locked.deadline, time.max))
    expires_at = min(configured_expiry, maximum_expiry, deadline_expiry)
    access = AccesoExternoSolicitudColectivo.objects.create(
        request=locked, selector=selector, token_hash=_token_hash(secret), created_by=actor,
        version=next_version, channel="MANUAL", purpose="CLIENT_RESPONSE",
        expires_at=expires_at, encrypted_recipient=encrypt(recipient.strip()),
        recipient_hash=_recipient_hash(recipient) if recipient.strip() else "", encrypted_contact_name=encrypt(contact_name.strip()),
        encrypted_intro=encrypt(intro.strip()), encrypted_instructions=encrypt(instructions.strip()),
    )
    event = "EXTERNAL_ACCESS_REGENERATED" if regenerate else "EXTERNAL_ACCESS_CREATED"
    EventoSolicitudColectivo.objects.create(request=locked, actor=actor, event_type=event, safe_metadata={"access_version": access.version})
    # La generación queda en el historial técnico. No crea una tarea o
    # notificación administrativa para el analista.
    token = f"{selector}.{secret}"
    return GeneratedAccess(access, token, f"{settings.COLECTIVOS_EXTERNAL_BASE_URL}/solicitudes/colectivos/externa/{token}/", regenerate)


@transaction.atomic
def update_access_recipient(*, access: AccesoExternoSolicitudColectivo, actor, recipient: str) -> bool:
    """Persist an explicitly edited recipient on a reusable live access.

    A pending OTP belongs to the previous recipient.  Changing the authorized
    address therefore invalidates only that challenge (and a verified session),
    so the next normal access issues a fresh OTP to the new address.
    """
    normalized = recipient.strip()
    if not normalized:
        raise ExternalAccessError("El acceso no tiene un correo autorizado.")
    locked = AccesoExternoSolicitudColectivo.objects.select_for_update().select_related(
        "request"
    ).get(pk=access.pk)
    next_hash = _recipient_hash(normalized)
    if hmac.compare_digest(locked.recipient_hash or "", next_hash):
        return False
    locked.encrypted_recipient = encrypt(normalized)
    locked.recipient_hash = next_hash
    locked.otp_hash = ""
    locked.otp_expires_at = None
    locked.otp_attempts = 0
    locked.otp_used_at = None
    update_fields = (
        "encrypted_recipient", "recipient_hash", "otp_hash",
        "otp_expires_at", "otp_attempts", "otp_used_at",
    )
    if locked.status == locked.Status.VERIFIED:
        locked.status = locked.Status.ACTIVE
        update_fields = (*update_fields, "status")
    locked.save(update_fields=update_fields)
    EventoSolicitudColectivo.objects.create(
        request=locked.request,
        actor=actor,
        event_type="EXTERNAL_ACCESS_RECIPIENT_UPDATED",
        safe_metadata={"access_version": locked.version},
    )
    access.encrypted_recipient = locked.encrypted_recipient
    access.recipient_hash = locked.recipient_hash
    access.otp_hash = ""
    access.otp_expires_at = None
    access.otp_attempts = 0
    access.otp_used_at = None
    access.status = locked.status
    return True


@transaction.atomic
def revoke_access(*, request: SolicitudColectivo, actor) -> AccesoExternoSolicitudColectivo:
    access = request.external_accesses.select_for_update().filter(
        status__in=[AccesoExternoSolicitudColectivo.Status.ACTIVE, AccesoExternoSolicitudColectivo.Status.VERIFIED]
    ).order_by("-created_at").first()
    if not access:
        raise ExternalAccessError("No existe un acceso vigente para revocar.")
    access.status = access.Status.REVOKED
    access.revoked_at = timezone.now()
    access.revoked_by = actor
    access.otp_hash = ""
    access.otp_expires_at = None
    access.save(update_fields=("status", "revoked_at", "revoked_by", "otp_hash", "otp_expires_at"))
    EventoSolicitudColectivo.objects.create(request=request, actor=actor, event_type="EXTERNAL_ACCESS_REVOKED", safe_metadata={"access_version": access.version})
    return access


def resolve_token(token: str) -> AccesoExternoSolicitudColectivo:
    try:
        selector, secret = token.split(".", 1)
    except ValueError as exc:
        raise ExternalAccessError("El acceso no es válido.") from exc
    if len(selector) > 32 or len(secret) < 32:
        raise ExternalAccessError("El acceso no es válido.")
    try:
        access = AccesoExternoSolicitudColectivo.objects.select_related("request", "request__assigned_to").get(selector=selector)
    except AccesoExternoSolicitudColectivo.DoesNotExist as exc:
        raise ExternalAccessError("El acceso no es válido.") from exc
    if not hmac.compare_digest(access.token_hash, _token_hash(secret)):
        raise ExternalAccessError("El acceso no es válido.")
    if access.purpose != "CLIENT_RESPONSE" or access.version < 1:
        raise ExternalAccessError("El acceso no es válido.")
    if access.status in {access.Status.REVOKED, access.Status.USED, access.Status.BLOCKED}:
        raise ExternalAccessError("El acceso no está disponible.")
    if access.request.status in {
        access.request.Status.CLOSED,
        access.request.Status.CANCELLED,
        access.request.Status.EXPIRED,
        access.request.Status.ANSWERED,
        access.request.Status.REVIEW,
        access.request.Status.APPROVED,
        access.request.Status.PENDING_ZOHO,
        access.request.Status.LOADED_ZOHO,
    }:
        raise ExternalAccessError("El acceso no está disponible.")
    if access.expires_at <= timezone.now() or access.request.deadline < timezone.localdate():
        access.status = access.Status.EXPIRED
        access.save(update_fields=("status",))
        raise ExternalAccessError("El acceso ha expirado.")
    return access


def issue_otp(access: AccesoExternoSolicitudColectivo) -> bool:
    access.refresh_from_db(fields=("otp_hash", "otp_expires_at", "encrypted_recipient", "expires_at"))
    now = timezone.now()
    if access.otp_hash and access.otp_expires_at and access.otp_expires_at > now:
        return False
    recipient = decrypt(access.encrypted_recipient).strip()
    if not recipient:
        raise ExternalAccessError("El acceso no tiene un correo autorizado.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    access.otp_hash = make_password(code)
    access.otp_expires_at = access.expires_at
    access.otp_attempts = 0
    access.save(update_fields=("otp_hash", "otp_expires_at", "otp_attempts"))
    email = build_otp_email(code, expires_at=access.otp_expires_at)
    send_notification(
        notification_type="COLECTIVOS_OTP", recipient=recipient,
        subject=email.subject,
        text_body=email.text_body,
        html_body=email.html_body,
        idempotency_key=f"colectivos-otp:{access.pk}:{access.otp_expires_at.isoformat()}",
        contains_one_time_code=True,
    )
    EventoSolicitudColectivo.objects.create(request=access.request, event_type="OTP_SENT", origin="EXTERNO")
    return True


@transaction.atomic
def send_invitation(generated: GeneratedAccess) -> None:
    access = AccesoExternoSolicitudColectivo.objects.select_for_update().select_related("request").get(pk=generated.access.pk)
    request = access.request
    recipient = decrypt(access.encrypted_recipient)
    if not recipient:
        raise ExternalAccessError("Debe indicar un correo para enviar la invitación.")
    record = send_notification(
        notification_type="COLECTIVOS_ACCESS_REGENERATED" if generated.regenerated else "COLECTIVOS_INVITATION", recipient=recipient,
        subject=(f"Acceso renovado · {request.public_id}" if generated.regenerated else f"Solicitud A&S · {request.public_id}"),
        text_body=f"A&S le invita a responder la solicitud {request.public_id}. Fecha límite: {request.deadline}. Acceso: {generated.url}",
        html_body=f"<h1>Solicitud {request.public_id}</h1><p>Fecha límite: {request.deadline}</p><p><a href=\"{generated.url}\">Abrir solicitud segura</a></p><p>No comparta este enlace.</p>",
        idempotency_key=f"colectivos-invitation:{access.pk}",
    )
    if record.result != "SENT":
        EventoSolicitudColectivo.objects.create(request=request, event_type="EMAIL_ERROR", safe_metadata={"category": record.safe_error_code or "delivery"})
        raise ExternalAccessError("No fue posible enviar la invitación.")
    now = timezone.now()
    access.sent_at = now
    access.save(update_fields=("sent_at",))
    if request.status in {request.Status.READY, request.Status.CORRECTION}:
        request.transition_to(request.Status.SENT)
        request.save(update_fields=("status", "updated_at"))
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_INVITATION_SENT", new_status=request.status)


@transaction.atomic
def send_optional_invitation(*, generated: GeneratedAccess, recipient: str) -> None:
    """Attach an optional delivery address and send without changing the link."""

    access = AccesoExternoSolicitudColectivo.objects.select_for_update().get(pk=generated.access.pk)
    access.encrypted_recipient = encrypt(recipient.strip())
    access.recipient_hash = _recipient_hash(recipient)
    access.channel = "EMAIL"
    access.save(update_fields=("encrypted_recipient", "recipient_hash", "channel"))
    send_invitation(GeneratedAccess(access=access, token=generated.token, url=generated.url, regenerated=generated.regenerated))


def authorize_token_only(access: AccesoExternoSolicitudColectivo) -> str:
    # Legacy API name retained for callers from already-deployed QA data.
    return authorize_direct_access(access)


@transaction.atomic
def authorize_direct_access(access: AccesoExternoSolicitudColectivo) -> str:
    """Authorize a valid high-entropy link without a second shared secret."""

    locked = AccesoExternoSolicitudColectivo.objects.select_for_update().select_related("request").get(pk=access.pk)
    if locked.status not in {locked.Status.ACTIVE, locked.Status.VERIFIED}:
        raise ExternalAccessError("El acceso no está disponible.")
    now = timezone.now()
    if locked.expires_at <= now or locked.request.deadline < timezone.localdate():
        raise ExternalAccessError("El acceso ha expirado.")
    if locked.request.status not in {
        locked.request.Status.READY,
        locked.request.Status.SENT,
        locked.request.Status.OPENED,
        locked.request.Status.CORRECTION,
    }:
        raise ExternalAccessError("El acceso no está disponible.")
    locked.status = locked.Status.VERIFIED
    locked.first_access_at = locked.first_access_at or now
    locked.last_access_at = now
    locked.access_count += 1
    locked.save(update_fields=("status", "first_access_at", "last_access_at", "access_count"))
    if locked.request.status == locked.request.Status.READY:
        locked.request.transition_to(locked.request.Status.SENT)
    if locked.request.status in {locked.request.Status.SENT, locked.request.Status.CORRECTION}:
        locked.request.transition_to(locked.request.Status.OPENED)
    locked.request.save(update_fields=("status", "updated_at"))
    EventoSolicitudColectivo.objects.create(
        request=locked.request,
        event_type="EXTERNAL_LINK_OPENED",
        origin="EXTERNO",
        safe_metadata={"access_version": locked.version},
    )
    return signing.dumps(
        {"access": locked.selector, "request": str(locked.request.uuid), "nonce": secrets.token_urlsafe(16)},
        salt=SESSION_SALT,
        compress=True,
    )


def verify_otp(access: AccesoExternoSolicitudColectivo, code: str) -> str:
    failed = False
    cookie_payload = None
    with transaction.atomic():
        locked = AccesoExternoSolicitudColectivo.objects.select_for_update().select_related("request").get(pk=access.pk)
        if locked.otp_attempts >= settings.COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS:
            locked.status = locked.Status.BLOCKED
            locked.save(update_fields=("status",))
            failed = True
        elif not locked.otp_hash or not locked.otp_expires_at or locked.otp_expires_at <= timezone.now() or not check_password(code, locked.otp_hash):
            locked.otp_attempts += 1
            locked.failed_attempts += 1
            if locked.otp_attempts >= settings.COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS:
                locked.status = locked.Status.BLOCKED
                EventoSolicitudColectivo.objects.create(
                    request=locked.request,
                    event_type="OTP_BLOCKED",
                    origin="EXTERNO",
                )
            locked.save(update_fields=("otp_attempts", "failed_attempts", "status"))
            failed = True
        else:
            now = timezone.now()
            locked.otp_used_at = now
            locked.otp_hash = ""
            locked.status = locked.Status.VERIFIED
            locked.first_access_at = locked.first_access_at or now
            locked.last_access_at = now
            locked.access_count += 1
            locked.save(update_fields=("otp_used_at", "otp_hash", "status", "first_access_at", "last_access_at", "access_count"))
            if locked.request.status == locked.request.Status.READY:
                locked.request.transition_to(locked.request.Status.SENT)
            if locked.request.status in {locked.request.Status.SENT, locked.request.Status.CORRECTION}:
                locked.request.transition_to(locked.request.Status.OPENED)
                locked.request.save(update_fields=("status", "updated_at"))
            cookie_payload = {
                "access": locked.selector,
                "request": str(locked.request.uuid),
                "nonce": secrets.token_urlsafe(16),
            }
    if failed:
        raise ExternalAccessError("No fue posible validar el acceso.")
    return signing.dumps(cookie_payload, salt=SESSION_SALT, compress=True)


def resolve_external_session(cookie: str) -> AccesoExternoSolicitudColectivo:
    try:
        payload = signing.loads(cookie, salt=SESSION_SALT, max_age=settings.COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS)
        access = AccesoExternoSolicitudColectivo.objects.select_related("request").get(selector=payload["access"], request__uuid=payload["request"])
    except (signing.BadSignature, KeyError, AccesoExternoSolicitudColectivo.DoesNotExist) as exc:
        raise ExternalAccessError("La sesión externa no es válida.") from exc
    if (
        access.status != access.Status.VERIFIED
        or access.expires_at <= timezone.now()
        or access.request.deadline < timezone.localdate()
        or access.request.status in {access.request.Status.CLOSED, access.request.Status.CANCELLED, access.request.Status.EXPIRED}
    ):
        raise ExternalAccessError("La sesión externa no está disponible.")
    return access


def response_checksum(rows: list[dict[str, object]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def save_response(*, access: AccesoExternoSolicitudColectivo, rows: list[dict[str, str]], observations: str, origin: str = "WEB") -> RespuestaSolicitudColectivo:
    request = SolicitudColectivo.objects.select_for_update().get(pk=access.request_id)
    if request.status not in {request.Status.OPENED, request.Status.CORRECTION}:
        raise ExternalAccessError("La solicitud no admite cambios.")
    normalized = []
    seen = set()
    for position, row in enumerate(rows, 1):
        action = str(row.get("action", "")).strip().upper()
        if action not in ALLOWED_ACTIONS:
            raise ExternalAccessError("Existe una acción no permitida.")
        record_key = str(row.get("record", "")).strip()
        record = None
        source_records = []
        policy = None
        if action != CambioSolicitudColectivo.Action.INCLUDE:
            record_keys = tuple(dict.fromkeys(
                str(value).strip() for value in (row.get("records") or (record_key,)) if str(value).strip()
            ))
            source_records = list(
                request.records.select_related("policy").filter(public_key__in=record_keys)
            )
            found_keys = {str(item.public_key) for item in source_records}
            if not record_keys or found_keys != set(record_keys) or seen.intersection(found_keys):
                raise ExternalAccessError("Existe una referencia de registro no válida.")
            seen.update(found_keys)
            policy_ids = {item.policy_id for item in source_records}
            if len(policy_ids) != 1:
                raise ExternalAccessError("La referencia funcional mezcla pólizas.")
            record = sorted(source_records, key=lambda item: item.original_position)[0]
            policy = record.policy
        elif request.policies.exists():
            policy_key = str(row.get("policy", "")).strip()
            if not policy_key.isdigit():
                raise ExternalAccessError("La inclusión no identifica una póliza válida.")
            policy = request.policies.filter(pk=int(policy_key), active=True).first()
            if policy is None:
                raise ExternalAccessError("La inclusión no identifica una póliza válida.")
        if policy is not None:
            required_adjustment = ACTION_TO_ADJUSTMENT[action]
            if required_adjustment not in set(policy.enabled_adjustments or ()):
                raise ExternalAccessError("La acción no está habilitada para esta póliza.")
        fields = {key: str(row.get(key, "")).strip()[:500] for key in EDITABLE_FIELDS}
        if action == CambioSolicitudColectivo.Action.UNCHANGED:
            fields = {key: "" for key in EDITABLE_FIELDS}
        if action == CambioSolicitudColectivo.Action.MODIFY and not fields["fecha_efectiva"]:
            raise ExternalAccessError("La fecha efectiva es obligatoria para la modificación histórica.")
        if action == CambioSolicitudColectivo.Action.RETIRE and not (fields["fecha_retiro"] or fields["fecha_efectiva"]):
            raise ExternalAccessError("La fecha solicitada de retiro es obligatoria.")
        if action == CambioSolicitudColectivo.Action.INCLUDE and not (fields["fecha_ingreso"] or fields["fecha_efectiva"]):
            raise ExternalAccessError("La fecha solicitada de ingreso es obligatoria.")
        if action == CambioSolicitudColectivo.Action.INCLUDE:
            if (
                fields["tipo_id"] not in CONTACT_ID_TYPE_CHOICES
                or fields["rol"] not in RELATION_ROLE_CHOICES
                or not fields["documento"].isdigit()
                or not fields["nombres"]
                or not fields["apellidos"]
            ):
                raise ExternalAccessError("El ingreso requiere nombres, apellidos, identificación y documento válidos.")
        if fields["parentesco"] and fields["parentesco"] not in RELATIONSHIP_CHOICES:
            raise ExternalAccessError("El parentesco seleccionado no es válido.")
        if fields["estado"] and fields["estado"] not in INSURED_STATE_CHOICES:
            raise ExternalAccessError("El estado seleccionado no es válido.")
        normalized.append({
            "action": action, "record": record, "source_records": tuple(source_records),
            "functional_key": str(row.get("functional_key") or ""),
            "policy": policy, "fields": fields, "position": position,
        })
    current = request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).order_by("-version").first()
    version = (request.responses.order_by("-version").values_list("version", flat=True).first() or 0) + 1
    if current:
        current.status = current.Status.SUPERSEDED
        current.save(update_fields=("status",))
    checksum_rows = [{"action": item["action"], "records": sorted(str(record.public_key) for record in item["source_records"]) or ["new"], "policy_position": item["policy"].position if item["policy"] else 0, "fields": item["fields"]} for item in normalized]
    response = RespuestaSolicitudColectivo.objects.create(request=request, access=access, version=version, origin=origin, checksum=response_checksum(checksum_rows), encrypted_client_observations=encrypt(observations.strip()[:2000]))
    for item in normalized:
        marker = response_checksum([{"action": item["action"], "position": item["position"], "policy_position": item["policy"].position if item["policy"] else 0}])
        source_payload = encrypt(json.dumps({
            "functional_key": item["functional_key"],
            "source_record_keys": sorted(str(record.public_key) for record in item["source_records"]),
        }, sort_keys=True))
        CambioSolicitudColectivo.objects.create(response=response, policy=item["policy"], original_record=item["record"], action=item["action"], functional_field="accion", position=item["position"], encrypted_branch_payload=source_payload, checksum=marker)
        for field, value in item["fields"].items():
            if not value:
                continue
            prior = ""
            if item["record"] and field in {"plan", "fecha_ingreso", "fecha_retiro"}:
                attribute = {"plan": "plan", "fecha_ingreso": "entry_date", "fecha_retiro": "exit_date"}[field]
                prior = str(getattr(item["record"], attribute, "") or "")
            digest = response_checksum([{"action": item["action"], "field": field, "position": item["position"], "policy_position": item["policy"].position if item["policy"] else 0, "value": value}])
            CambioSolicitudColectivo.objects.create(response=response, policy=item["policy"], original_record=item["record"], action=item["action"], functional_field=field, encrypted_previous_value=encrypt(prior), encrypted_new_value=encrypt(value), encrypted_branch_payload=source_payload, position=item["position"], checksum=digest)
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_DRAFT_SAVED", origin="EXTERNO", safe_metadata={"version": version, "changes": response.changes.count()})
    return response


@transaction.atomic
def submit_response(*, access: AccesoExternoSolicitudColectivo, response: RespuestaSolicitudColectivo, declaration: bool) -> RespuestaSolicitudColectivo:
    locked = RespuestaSolicitudColectivo.objects.select_for_update().select_related("request", "access").get(pk=response.pk)
    if locked.status == locked.Status.SUBMITTED:
        return locked
    valid_novelties = locked.changes.filter(
        action__in=(CambioSolicitudColectivo.Action.INCLUDE, CambioSolicitudColectivo.Action.RETIRE, CambioSolicitudColectivo.Action.MODIFY),
        validation_status__in=(CambioSolicitudColectivo.Validation.VALID, CambioSolicitudColectivo.Validation.WARNING),
    )
    if locked.status != locked.Status.DRAFT or not declaration or locked.changes.filter(validation_status=CambioSolicitudColectivo.Validation.INVALID).exists():
        raise ExternalAccessError("La respuesta no está lista para enviar.")
    if not valid_novelties.exists():
        raise ExternalAccessError("No hay cambios preparados para enviar.")
    now = timezone.now()
    locked.status = locked.Status.SUBMITTED
    locked.declaration_confirmed = True
    locked.submitted_at = now
    locked.save(update_fields=("status", "declaration_confirmed", "submitted_at", "updated_at"))
    request = locked.request
    request.transition_to(request.Status.ANSWERED)
    request.save(update_fields=("status", "updated_at"))
    kinds = set()
    for change in locked.changes.all():
        if change.action == CambioSolicitudColectivo.Action.INCLUDE:
            kinds.add("INCLUSION")
        elif change.action == CambioSolicitudColectivo.Action.RETIRE:
            kinds.add("RETIRO")
    for kind in sorted(kinds):
        label = "Ingreso" if kind == "INCLUSION" else "Retiro"
        observations = [f"Solicitud de {label.lower()} de {request.branch_name or 'ramo no informado'}." ]
        for change in locked.changes.all():
            if ((kind == "INCLUSION" and change.action != CambioSolicitudColectivo.Action.INCLUDE)
                    or (kind == "RETIRO" and change.action != CambioSolicitudColectivo.Action.RETIRE)):
                continue
            values = []
            if change.encrypted_new_value:
                try:
                    value = decrypt(change.encrypted_new_value).strip()
                except (TypeError, ValueError):
                    value = ""
                if value:
                    values.append(f"{change.functional_field}: {value}")
            if change.encrypted_observation:
                try:
                    value = decrypt(change.encrypted_observation).strip()
                except (TypeError, ValueError):
                    value = ""
                if value:
                    values.append(f"observaciones: {value}")
            if values:
                observations.append(" · ".join(values))
        enqueue_task(
            source=request,
            payload=ColectivosTaskPayload(
                request_kind=kind,
                source_kind="request",
                policy_context=str(request.public_id),
                branch_code=str(request.branch_code),
                local_reference=str(request.public_id),
                subject=f"{label} · {request.branch_name or 'Ramo'} · {request.client_label or 'Cliente'}"[:255],
                observations="\n".join(observations)[:2000],
            ),
            event_version=locked.version,
        )
    access.status = access.Status.USED
    access.used_for_submission_at = now
    access.save(update_fields=("status", "used_for_submission_at"))
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_RESPONSE_SUBMITTED", origin="EXTERNO", new_status=request.status, safe_metadata={"version": locked.version})
    _notify(
        request,
        "CLIENT_RESPONSE",
        "Novedad recibida",
        f"El cliente respondió una novedad de la póliza {request.masked_policy_reference}.",
        str(locked.version),
    )
    return locked


def _send_submission_receipt(access_id: int, public_id: str) -> None:
    try:
        access = AccesoExternoSolicitudColectivo.objects.get(pk=access_id)
        recipient = decrypt(access.encrypted_recipient)
        if not recipient:
            return
        record = send_notification(
            notification_type="COLECTIVOS_RESPONSE_RECEIPT",
            recipient=recipient,
            subject=f"Respuesta recibida · {public_id}",
            text_body=f"A&S recibió correctamente su respuesta para la solicitud {public_id}.",
            html_body=f"<p>A&S recibió correctamente su respuesta para la solicitud <strong>{public_id}</strong>.</p>",
            idempotency_key=f"colectivos-response-receipt:{access_id}",
        )
        if record.result != "SENT":
            EventoSolicitudColectivo.objects.create(
                request=access.request,
                event_type="EMAIL_ERROR",
                safe_metadata={"category": record.safe_error_code or "delivery", "purpose": "response_receipt"},
            )
    except Exception:
        try:
            access = AccesoExternoSolicitudColectivo.objects.get(pk=access_id)
            EventoSolicitudColectivo.objects.create(
                request=access.request,
                event_type="EMAIL_ERROR",
                safe_metadata={"category": "delivery", "purpose": "response_receipt"},
            )
        except AccesoExternoSolicitudColectivo.DoesNotExist:
            pass
