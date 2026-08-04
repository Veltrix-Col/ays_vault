from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from vault.crypto import decrypt, encrypt
from vault.notifications import send_notification

from ..models import (
    AccesoExternoSolicitudColectivo,
    CambioSolicitudColectivo,
    EventoSolicitudColectivo,
    NotificacionColectivos,
    RespuestaSolicitudColectivo,
    SolicitudColectivo,
)

EXTERNAL_COOKIE = "colectivos_external_session"
SESSION_SALT = "cotizacion_colectivos.external_session.v1"
ALLOWED_ACTIONS = set(CambioSolicitudColectivo.Action.values)
EDITABLE_FIELDS = {"tipo_id", "documento", "nombre", "rol", "plan", "parentesco", "fecha_efectiva", "fecha_ingreso", "fecha_retiro", "motivo", "observaciones"}


class ExternalAccessError(ValidationError):
    pass


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
def generate_access(*, request: SolicitudColectivo, actor, recipient: str, contact_name: str = "", intro: str = "", instructions: str = "", regenerate: bool = False) -> GeneratedAccess:
    locked = SolicitudColectivo.objects.select_for_update().get(pk=request.pk)
    if locked.status in {locked.Status.CLOSED, locked.Status.CANCELLED, locked.Status.EXPIRED}:
        raise ExternalAccessError("La solicitud no admite un nuevo acceso.")
    if locked.status == locked.Status.DRAFT:
        raise ExternalAccessError("La solicitud debe marcarse lista para enviar.")
    if locked.deadline <= timezone.localdate() or not locked.encrypted_snapshot or not locked.assigned_to_id:
        raise ExternalAccessError("La solicitud no cumple las condiciones de envío.")
    now = timezone.now()
    active = locked.external_accesses.select_for_update().filter(status__in=[AccesoExternoSolicitudColectivo.Status.ACTIVE, AccesoExternoSolicitudColectivo.Status.VERIFIED])
    if active.exists() and not regenerate:
        raise ExternalAccessError("Ya existe un acceso externo activo.")
    active.update(status=AccesoExternoSolicitudColectivo.Status.REVOKED, revoked_at=now, revoked_by=actor)
    selector = secrets.token_urlsafe(18)[:24]
    secret = secrets.token_urlsafe(32)
    ttl = min(settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS, settings.COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS)
    access = AccesoExternoSolicitudColectivo.objects.create(
        request=locked, selector=selector, token_hash=_token_hash(secret), created_by=actor,
        expires_at=now + timedelta(seconds=ttl), encrypted_recipient=encrypt(recipient.strip()),
        recipient_hash=_recipient_hash(recipient), encrypted_contact_name=encrypt(contact_name.strip()),
        encrypted_intro=encrypt(intro.strip()), encrypted_instructions=encrypt(instructions.strip()),
    )
    event = "EXTERNAL_ACCESS_REGENERATED" if regenerate else "EXTERNAL_ACCESS_CREATED"
    EventoSolicitudColectivo.objects.create(request=locked, actor=actor, event_type=event, safe_metadata={"access_version": access.version})
    _notify(locked, event, "Acceso externo generado", f"Se generó un acceso para {locked.public_id}.", str(access.pk))
    token = f"{selector}.{secret}"
    return GeneratedAccess(access, token, f"{settings.COLECTIVOS_EXTERNAL_BASE_URL}/solicitudes/colectivos/externa/{token}/", regenerate)


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
    if access.status in {access.Status.REVOKED, access.Status.USED, access.Status.BLOCKED}:
        raise ExternalAccessError("El acceso no está disponible.")
    if access.expires_at <= timezone.now() or access.request.deadline < timezone.localdate():
        access.status = access.Status.EXPIRED
        access.save(update_fields=("status",))
        raise ExternalAccessError("El acceso ha expirado.")
    return access


def issue_otp(access: AccesoExternoSolicitudColectivo) -> None:
    code = f"{secrets.randbelow(1_000_000):06d}"
    access.otp_hash = make_password(code)
    access.otp_expires_at = timezone.now() + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS)
    access.otp_attempts = 0
    access.save(update_fields=("otp_hash", "otp_expires_at", "otp_attempts"))
    recipient = decrypt(access.encrypted_recipient)
    send_notification(
        notification_type="COLECTIVOS_OTP", recipient=recipient,
        subject=f"Código de acceso · {access.request.public_id}",
        text_body=f"Su código de un solo uso es {code}. Expira pronto. A&S nunca le solicitará compartirlo.",
        html_body=f"<p>Su código de un solo uso es <strong>{code}</strong>.</p><p>Expira pronto. No lo comparta.</p>",
        idempotency_key=f"colectivos-otp:{access.pk}:{access.otp_expires_at.isoformat()}",
    )
    EventoSolicitudColectivo.objects.create(request=access.request, event_type="OTP_SENT", origin="EXTERNO")


@transaction.atomic
def send_invitation(generated: GeneratedAccess) -> None:
    access = AccesoExternoSolicitudColectivo.objects.select_for_update().select_related("request").get(pk=generated.access.pk)
    request = access.request
    recipient = decrypt(access.encrypted_recipient)
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


def authorize_token_only(access: AccesoExternoSolicitudColectivo) -> str:
    if not settings.DEBUG or settings.COLECTIVOS_EXTERNAL_ACCESS_VERIFICATION != "token_only":
        raise ExternalAccessError("La verificación adicional es obligatoria.")
    now = timezone.now()
    access.status = access.Status.VERIFIED
    access.first_access_at = access.first_access_at or now
    access.last_access_at = now
    access.access_count += 1
    access.save(update_fields=("status", "first_access_at", "last_access_at", "access_count"))
    if access.request.status == access.request.Status.SENT:
        access.request.transition_to(access.request.Status.OPENED)
        access.request.save(update_fields=("status", "updated_at"))
    return signing.dumps({"access": access.selector, "request": str(access.request.uuid), "nonce": secrets.token_urlsafe(16)}, salt=SESSION_SALT, compress=True)


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
            if locked.request.status == locked.request.Status.SENT:
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


def response_checksum(rows: list[dict[str, str]]) -> str:
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
        if action != CambioSolicitudColectivo.Action.INCLUDE:
            record = request.records.filter(public_key=record_key).first()
            if not record or record_key in seen:
                raise ExternalAccessError("Existe una referencia de registro no válida.")
            seen.add(record_key)
        fields = {key: str(row.get(key, "")).strip()[:500] for key in EDITABLE_FIELDS}
        if action in {CambioSolicitudColectivo.Action.MODIFY, CambioSolicitudColectivo.Action.RETIRE, CambioSolicitudColectivo.Action.INCLUDE} and not fields["fecha_efectiva"]:
            raise ExternalAccessError("La fecha efectiva es obligatoria para la novedad.")
        if action == CambioSolicitudColectivo.Action.INCLUDE:
            if fields["tipo_id"] not in {"CC", "CE", "TI", "RC", "PP", "PEP", "PPT", "NIT"} or not fields["documento"].isdigit() or not fields["nombre"]:
                raise ExternalAccessError("La inclusión requiere identificación y nombre válidos.")
        normalized.append({"action": action, "record": record, "fields": fields, "position": position})
    current = request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).order_by("-version").first()
    version = (request.responses.order_by("-version").values_list("version", flat=True).first() or 0) + 1
    if current:
        current.status = current.Status.SUPERSEDED
        current.save(update_fields=("status",))
    checksum_rows = [{"action": item["action"], "record": str(item["record"].public_key) if item["record"] else "new", "fields": item["fields"]} for item in normalized]
    response = RespuestaSolicitudColectivo.objects.create(request=request, access=access, version=version, origin=origin, checksum=response_checksum(checksum_rows), encrypted_client_observations=encrypt(observations.strip()[:2000]))
    for item in normalized:
        marker = response_checksum([{"action": item["action"], "position": item["position"]}])
        CambioSolicitudColectivo.objects.create(response=response, original_record=item["record"], action=item["action"], functional_field="accion", position=item["position"], checksum=marker)
        for field, value in item["fields"].items():
            if not value:
                continue
            prior = ""
            if item["record"] and field in {"plan", "fecha_ingreso", "fecha_retiro"}:
                attribute = {"plan": "plan", "fecha_ingreso": "entry_date", "fecha_retiro": "exit_date"}[field]
                prior = str(getattr(item["record"], attribute, "") or "")
            digest = response_checksum([{"action": item["action"], "field": field, "position": item["position"], "value": value}])
            CambioSolicitudColectivo.objects.create(response=response, original_record=item["record"], action=item["action"], functional_field=field, encrypted_previous_value=encrypt(prior), encrypted_new_value=encrypt(value), position=item["position"], checksum=digest)
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_DRAFT_SAVED", origin="EXTERNO", safe_metadata={"version": version, "changes": response.changes.count()})
    return response


@transaction.atomic
def submit_response(*, access: AccesoExternoSolicitudColectivo, response: RespuestaSolicitudColectivo, declaration: bool) -> RespuestaSolicitudColectivo:
    locked = RespuestaSolicitudColectivo.objects.select_for_update().select_related("request", "access").get(pk=response.pk)
    if locked.status == locked.Status.SUBMITTED:
        return locked
    if locked.status != locked.Status.DRAFT or not declaration or locked.changes.filter(validation_status=CambioSolicitudColectivo.Validation.INVALID).exists():
        raise ExternalAccessError("La respuesta no está lista para enviar.")
    now = timezone.now()
    locked.status = locked.Status.SUBMITTED
    locked.declaration_confirmed = True
    locked.submitted_at = now
    locked.save(update_fields=("status", "declaration_confirmed", "submitted_at", "updated_at"))
    request = locked.request
    request.transition_to(request.Status.ANSWERED)
    request.save(update_fields=("status", "updated_at"))
    access.status = access.Status.USED
    access.used_for_submission_at = now
    access.save(update_fields=("status", "used_for_submission_at"))
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_RESPONSE_SUBMITTED", origin="EXTERNO", new_status=request.status, safe_metadata={"version": locked.version})
    _notify(request, "CLIENT_RESPONSE", "Respuesta recibida", f"La solicitud {request.public_id} recibió respuesta.", str(locked.version))
    transaction.on_commit(lambda: _send_submission_receipt(access.pk, request.public_id))
    return locked


def _send_submission_receipt(access_id: int, public_id: str) -> None:
    try:
        access = AccesoExternoSolicitudColectivo.objects.get(pk=access_id)
        recipient = decrypt(access.encrypted_recipient)
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
