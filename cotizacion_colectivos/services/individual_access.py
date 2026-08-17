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
from .otp_email import build_otp_email

from ..models import AccesoCotizacionIndividual


INDIVIDUAL_COOKIE = "colectivos_individual_session"
INDIVIDUAL_SESSION_SALT = "cotizacion_colectivos.individual.session.v1"


class IndividualAccessError(ValidationError):
    pass


@dataclass(frozen=True)
class GeneratedIndividualAccess:
    access: AccesoCotizacionIndividual
    token: str


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _recipient_hash(email: str) -> str:
    return salted_hmac(
        "colectivos.individual.recipient.v1", email.strip().casefold(),
        secret=settings.SECRET_KEY,
    ).hexdigest()


def _context_checksum(context: dict) -> str:
    material = ":".join((
        str(context.get("policy_token") or ""),
        str(context.get("affiliate_key") or ""),
        str(context.get("schema_version") or ""),
    ))
    return salted_hmac(
        "colectivos.individual.context.v1", material, secret=settings.SECRET_KEY,
    ).hexdigest()


@transaction.atomic
def generate_individual_access(*, context: dict, actor, recipient: str) -> GeneratedIndividualAccess:
    recipient = recipient.strip()
    if not recipient:
        raise IndividualAccessError("Debe indicar un correo autorizado.")
    checksum = _context_checksum(context)
    AccesoCotizacionIndividual.objects.select_for_update().filter(
        created_by=actor,
        context_checksum=checksum,
        status__in=(
            AccesoCotizacionIndividual.Status.ACTIVE,
            AccesoCotizacionIndividual.Status.VERIFIED,
        ),
    ).update(status=AccesoCotizacionIndividual.Status.REVOKED)
    selector = secrets.token_urlsafe(18)[:24]
    secret = secrets.token_urlsafe(32)
    access = AccesoCotizacionIndividual.objects.create(
        selector=selector,
        token_hash=_sha(secret),
        encrypted_context=encrypt(json.dumps(context, ensure_ascii=False, sort_keys=True)),
        context_checksum=checksum,
        encrypted_recipient=encrypt(recipient),
        recipient_hash=_recipient_hash(recipient),
        created_by=actor,
        expires_at=timezone.now() + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS),
        safe_metadata={
            "branch": str(context.get("branch_slug") or "")[:24],
            "schema_version": int(context.get("schema_version") or 0),
            "task_responsible_display": str(context.get("task_responsible_display") or "")[:120],
        },
    )
    return GeneratedIndividualAccess(access=access, token=f"{selector}.{secret}")


def resolve_individual_token(token: str) -> AccesoCotizacionIndividual:
    try:
        selector, secret = token.split(".", 1)
    except ValueError as exc:
        raise IndividualAccessError("El acceso no es válido.") from exc
    if len(selector) > 32 or len(secret) < 32:
        raise IndividualAccessError("El acceso no es válido.")
    try:
        access = AccesoCotizacionIndividual.objects.select_related("created_by").get(selector=selector)
    except AccesoCotizacionIndividual.DoesNotExist as exc:
        raise IndividualAccessError("El acceso no es válido.") from exc
    if not hmac.compare_digest(access.token_hash, _sha(secret)):
        raise IndividualAccessError("El acceso no es válido.")
    if access.expires_at <= timezone.now():
        access.status = access.Status.EXPIRED
        access.save(update_fields=("status",))
        raise IndividualAccessError("El acceso expiró.")
    if access.status not in {access.Status.ACTIVE, access.Status.VERIFIED}:
        raise IndividualAccessError("El acceso no está disponible.")
    return access


def access_context(access: AccesoCotizacionIndividual) -> dict:
    try:
        return json.loads(decrypt(access.encrypted_context))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IndividualAccessError("El contexto no está disponible.") from exc


def issue_individual_otp(access: AccesoCotizacionIndividual) -> bool:
    access.refresh_from_db(fields=("otp_hash", "otp_expires_at", "encrypted_recipient", "expires_at"))
    now = timezone.now()
    if access.otp_hash and access.otp_expires_at and access.otp_expires_at > now:
        return False
    code = f"{secrets.randbelow(1_000_000):06d}"
    access.otp_hash = make_password(code)
    access.otp_expires_at = access.expires_at
    access.otp_attempts = 0
    access.save(update_fields=("otp_hash", "otp_expires_at", "otp_attempts"))
    recipient = decrypt(access.encrypted_recipient)
    email = build_otp_email(code, expires_at=access.otp_expires_at)
    send_notification(
        notification_type="COLECTIVOS_INDIVIDUAL_OTP",
        recipient=recipient,
        subject=email.subject,
        text_body=email.text_body,
        html_body=email.html_body,
        idempotency_key=f"colectivos-individual-otp:{access.pk}:{access.otp_expires_at.isoformat()}",
        contains_one_time_code=True,
    )
    return True


def verify_individual_otp(access: AccesoCotizacionIndividual, code: str) -> str:
    with transaction.atomic():
        locked = AccesoCotizacionIndividual.objects.select_for_update().get(pk=access.pk)
        if (
            locked.otp_attempts >= settings.COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS
            or not locked.otp_hash
            or not locked.otp_expires_at
            or locked.otp_expires_at <= timezone.now()
            or not check_password(code, locked.otp_hash)
        ):
            locked.otp_attempts += 1
            locked.failed_attempts += 1
            if locked.otp_attempts >= settings.COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS:
                locked.status = locked.Status.BLOCKED
            locked.save(update_fields=("otp_attempts", "failed_attempts", "status"))
            raise IndividualAccessError("No fue posible validar el acceso.")
        now = timezone.now()
        locked.otp_used_at = now
        locked.otp_hash = ""
        locked.status = locked.Status.VERIFIED
        locked.first_access_at = locked.first_access_at or now
        locked.last_access_at = now
        locked.access_count += 1
        locked.save(update_fields=("otp_used_at", "otp_hash", "status", "first_access_at", "last_access_at", "access_count"))
    return signing.dumps(
        {"access": locked.selector, "nonce": secrets.token_urlsafe(16)},
        salt=INDIVIDUAL_SESSION_SALT,
        compress=True,
    )


def resolve_individual_session(cookie: str, access: AccesoCotizacionIndividual):
    try:
        payload = signing.loads(
            cookie, salt=INDIVIDUAL_SESSION_SALT,
            max_age=settings.COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise IndividualAccessError("La sesión no es válida.") from exc
    if payload.get("access") != access.selector or access.status != access.Status.VERIFIED:
        raise IndividualAccessError("La sesión no está disponible.")
    return access


@transaction.atomic
def consume_individual_access(access: AccesoCotizacionIndividual, quotation) -> None:
    locked = AccesoCotizacionIndividual.objects.select_for_update().get(pk=access.pk)
    if locked.status != locked.Status.VERIFIED or locked.expires_at <= timezone.now():
        raise IndividualAccessError("El acceso no admite una respuesta.")
    locked.status = locked.Status.USED
    locked.used_at = timezone.now()
    locked.quotation = quotation
    locked.save(update_fields=("status", "used_at", "quotation"))
