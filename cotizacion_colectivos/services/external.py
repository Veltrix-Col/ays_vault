from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Mapping

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
    INSURED_STATE_CHOICES,
    RELATION_ROLE_CHOICES,
    RELATIONSHIP_CHOICES,
)
from ..branches import contract_required_ingress_fields
from .catalogs import CatalogUnavailable, identification_type_values
from .common import ColectivosServiceError, unsign_record_context

from ..models import (
    AccesoExternoSolicitudColectivo,
    CambioSolicitudColectivo,
    EventoSolicitudColectivo,
    NotificacionColectivos,
    RespuestaSolicitudColectivo,
    SolicitudColectivo,
    RenovacionColectiva,
)
from .task_publisher import (
    NOVELTIES_ANALYST_REQUEST,
    NOVELTIES_TASK_AREA,
    ColectivosTaskPayload,
    enqueue_task,
    publish_task_outbox,
)
from .requests import _restore_policy_token, request_snapshot
from .novelty_response_email import send_novelty_response_email
from .person_contract import contact_identity_missing_fields

EXTERNAL_COOKIE = "colectivos_external_session"
SESSION_SALT = "cotizacion_colectivos.external_session.v1"
NO_CHANGES_SALT = "cotizacion_colectivos.no_changes.v1"
ALLOWED_ACTIONS = set(CambioSolicitudColectivo.Action.values)
ACTION_TO_ADJUSTMENT = {
    CambioSolicitudColectivo.Action.UNCHANGED: "SIN_CAMBIOS",
    CambioSolicitudColectivo.Action.MODIFY: "MODIFICACION",
    CambioSolicitudColectivo.Action.RETIRE: "RETIRO",
    CambioSolicitudColectivo.Action.INCLUDE: "INCLUSION",
}
_TASK_FIELD_LABELS = {
    "nombres": "Nombres", "apellidos": "Apellidos", "nombre": "Nombre",
    "tipo_id": "Tipo de identificación", "documento": "Identificación",
    "fecha_nacimiento": "Fecha de nacimiento", "fecha_ingreso": "Fecha de ingreso",
    "fecha_retiro": "Fecha de retiro", "fecha_efectiva": "Fecha efectiva",
    "observaciones": "Observaciones", "correo": "Correo", "email": "Correo", "telefono": "Teléfono", "phone": "Teléfono",
    "placa": "Placa", "marca": "Marca",
    "modelo": "Modelo", "vehiculo": "Vehículo", "ciudad": "Ciudad",
    "tipo_uso": "Tipo de uso", "rol": "Rol", "parentesco": "Parentesco",
}
EDITABLE_FIELDS = {
    "tipo_id", "documento", "nombre", "nombres", "apellidos", "rol", "plan", "parentesco",
    "fecha_nacimiento", "fecha_efectiva", "fecha_ingreso", "fecha_retiro", "motivo",
    "observaciones", "ciudad", "direccion", "tipo_uso",
    "anio_construccion", "descripcion", "valor_asegurado", "vehiculo",
    "placa", "marca", "modelo", "estado", "email", "phone",
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


def generate_no_changes_token(*, cycle, request_obj=None) -> str:
    """Create a signed, policy-scoped confirmation token for renewal email CTA."""
    request_obj = request_obj or cycle.request
    payload = {
        "action": "NO_CHANGES",
        "cycle": int(cycle.pk),
        "request": str(request_obj.public_id) if request_obj is not None else "",
        "policy": str(cycle.policy_remote_id),
        "period": str(cycle.monthly_period),
    }
    return signing.dumps(payload, salt=NO_CHANGES_SALT, compress=True)


def resolve_no_changes_token(token: str, *, max_age: int | None = None):
    """Resolve and validate the signed renewal/policy scope without mutating state."""
    try:
        payload = signing.loads(
            token,
            salt=NO_CHANGES_SALT,
            max_age=max_age or int(getattr(settings, "COLECTIVOS_RENEWAL_LINK_TTL_DAYS", 8)) * 86400,
        )
        if payload.get("action") != "NO_CHANGES":
            raise ValueError("invalid action")
        cycle = RenovacionColectiva.objects.select_related("request", "access").get(pk=int(payload["cycle"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, RenovacionColectiva.DoesNotExist) as exc:
        raise ExternalAccessError("El enlace de confirmación no es válido o expiró.") from exc
    if str(cycle.policy_remote_id) != str(payload.get("policy")) or str(cycle.monthly_period) != str(payload.get("period")):
        raise ExternalAccessError("El enlace de confirmación no corresponde a este periodo.")
    if cycle.request_id and str(cycle.request.public_id) != str(payload.get("request")):
        raise ExternalAccessError("El enlace de confirmación no corresponde a esta solicitud.")
    if cycle.status != RenovacionColectiva.Status.RESPONDED and (
        not cycle.access_id
        or cycle.access.status in {
            AccesoExternoSolicitudColectivo.Status.REVOKED,
            AccesoExternoSolicitudColectivo.Status.EXPIRED,
            AccesoExternoSolicitudColectivo.Status.BLOCKED,
            AccesoExternoSolicitudColectivo.Status.USED,
        }
        or cycle.access.expires_at <= timezone.now()
    ):
        raise ExternalAccessError("El enlace de confirmación ya no está disponible.")
    return cycle


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
def generate_access(*, request: SolicitudColectivo, actor, recipient: str = "", contact_name: str = "", intro: str = "", instructions: str = "", regenerate: bool = False, ttl_seconds: int | None = None) -> GeneratedAccess:
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
    configured_expiry = now + timedelta(seconds=ttl_seconds if ttl_seconds is not None else settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS)
    maximum_expiry = now + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS)
    # The public link contract is elapsed time from generation.  The internal
    # request deadline is a workflow reminder and must not shorten a valid
    # 48-hour external link at midnight.
    expires_at = min(configured_expiry, maximum_expiry)
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

def _task_novelty_observations(request, changes, label: str) -> str:
    """Build a human operational summary without exposing payload keys."""
    action = CambioSolicitudColectivo.Action.INCLUDE if label == "Ingreso" else CambioSolicitudColectivo.Action.RETIRE
    lines = [f"Solicitud de {label.lower()} de {request.branch_name or 'ramo no informado'}."]
    policy_labels = []
    grouped = {}
    for change in changes:
        if change.action != action:
            continue
        policy = getattr(change, "policy", None)
        policy_label = getattr(policy, "masked_policy_reference", "") if policy else ""
        if policy_label and policy_label not in policy_labels:
            policy_labels.append(policy_label)
        try:
            value = decrypt(change.encrypted_new_value).strip() if change.encrypted_new_value else ""
        except (TypeError, ValueError):
            value = ""
        if change.functional_field == "observaciones" and change.encrypted_observation:
            try:
                value = decrypt(change.encrypted_observation).strip()
            except (TypeError, ValueError):
                pass
        position = change.position if change.position is not None else 0
        row = grouped.setdefault(position, {})
        if value:
            row[change.functional_field] = value
        # Retire rows commonly carry only the effective date in the change;
        # enrich the operational summary from the persisted snapshot rather
        # than performing a remote lookup or exposing technical identifiers.
        record = getattr(change, "original_record", None)
        payload_source = getattr(record, "encrypted_branch_payload", "") if record else ""
        payload_source = payload_source or getattr(change, "encrypted_branch_payload", "")
        if payload_source:
            try:
                snapshot = json.loads(decrypt(payload_source))
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = {}
            if isinstance(snapshot, dict):
                aliases = {
                    "id_type": "tipo_id", "identification_type": "tipo_id",
                    "document": "documento", "identification_number": "documento",
                    "full_name": "nombre", "display_name": "nombre",
                    "first_name": "nombres", "last_name": "apellidos",
                    "birth_date": "fecha_nacimiento", "entry_date": "fecha_ingreso",
                    "exit_date": "fecha_retiro", "effective_date": "fecha_efectiva",
                    "email": "correo", "phone": "telefono",
                }
                for source_key, target_key in aliases.items():
                    if not row.get(target_key) and snapshot.get(source_key) not in (None, ""):
                        row[target_key] = str(snapshot[source_key]).strip()
                for source_key, target_key in (
                    ("Tipo_ID", "tipo_id"), ("N_mero_de_ID", "documento"),
                    ("Nombres", "nombres"), ("Apellidos", "apellidos"),
                    ("Fecha_de_nacimiento", "fecha_nacimiento"),
                    ("Fecha_de_ingreso", "fecha_ingreso"),
                    ("Fecha_de_retiro", "fecha_retiro"),
                    ("Correo", "correo"), ("Tel_fono", "telefono"),
                ):
                    if not row.get(target_key) and snapshot.get(source_key) not in (None, ""):
                        row[target_key] = str(snapshot[source_key]).strip()
                row.setdefault("_element_type", getattr(record, "element_type", ""))
    if policy_labels:
        lines.append(f"Póliza: {', '.join(policy_labels)}")
    for position in sorted(grouped):
        row = grouped[position]
        vehicle_keys = {"placa", "marca", "modelo", "vehiculo", "ciudad", "tipo_uso"}
        heading = "Vehículo:" if vehicle_keys.intersection(row) or str(row.get("_element_type", "")) == "VEHICULO" else "Persona:"
        lines.extend(("", heading))
        full_name = " ".join(filter(None, (row.get("nombres"), row.get("apellidos")))) or row.get("nombre")
        if full_name:
            lines.append(f"Nombre: {full_name}")
        for key in ("tipo_id", "documento", "fecha_nacimiento", "fecha_ingreso", "fecha_retiro", "fecha_efectiva", "correo", "telefono", "placa", "marca", "modelo", "vehiculo", "ciudad", "tipo_uso", "rol", "parentesco", "observaciones"):
            if row.get(key):
                lines.append(f"{_TASK_FIELD_LABELS[key]}: {row[key]}")
    return "\n".join(lines)[:2000]


def _stored_policy_record_id(encrypted_reference: str) -> str:
    """Recover the Zoho policy ID from the protected stored reference.

    ``SolicitudColectivoPoliza`` stores the compact JSON reference produced by
    ``_store_policy_reference`` rather than the original signed token.  The
    canonical restore helper rebuilds and validates the signed context before
    this service consumes the ID, while still supporting legacy signed values.
    """
    if not encrypted_reference:
        return ""
    try:
        stored = decrypt(encrypted_reference)
        restored = _restore_policy_token(stored)
        context = unsign_record_context(restored, expected_type="policy")
        return str(context.get("id") or "").strip()
    except (TypeError, ValueError, ValidationError, ColectivosServiceError):
        return ""


@transaction.atomic
def ensure_novelty_ingress_items(request: SolicitudColectivo):
    """Materialize confirmed ingress rows once, without performing Zoho writes."""
    from ..models import NovedadIngresoZoho

    response = request.responses.filter(status=RespuestaSolicitudColectivo.Status.SUBMITTED).order_by("-version").first()
    if response is None:
        return []
    result = []
    for change in response.changes.select_related("policy").filter(action=CambioSolicitudColectivo.Action.INCLUDE, functional_field="accion"):
        payload = {}
        source = change.encrypted_branch_payload or ""
        if source:
            try:
                payload = json.loads(decrypt(source))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
        # The action row carries the stable identity, while the submitted
        # fields are persisted as sibling changes at the same position. Build
        # one canonical operational payload so Web and Excel feed the exact
        # same resolver contract.
        sibling_changes = response.changes.filter(
            position=change.position,
            action=CambioSolicitudColectivo.Action.INCLUDE,
        ).exclude(functional_field="accion")
        field_map = {
            "tipo_id": "id_type", "documento": "document",
            "id_type": "id_type", "document": "document",
            "nombres": "first_name", "nombre": "first_name",
            "apellidos": "last_name", "apellido": "last_name",
            "fecha_nacimiento": "birth_date", "fecha_de_nacimiento": "birth_date",
            "correo": "email", "correo_electronico": "email",
            "correo electrónico": "email", "email": "email",
            "email_address": "email",
            "telefono": "phone", "teléfono": "phone", "phone": "phone",
            "telefono_contacto": "phone", "teléfono de contacto": "phone",
            "celular": "phone", "mobile": "phone",
            "fecha_ingreso": "entry_date", "fecha_de_ingreso": "entry_date",
            "rol": "rol", "parentesco": "parentesco", "plan": "plan",
            "placa": "plate", "marca": "brand", "modelo": "model",
            "ciudad": "city", "tipo_uso": "use", "clase": "vehicle_class",
        }
        for sibling in sibling_changes:
            target = field_map.get(str(sibling.functional_field or ""))
            if not target or not sibling.encrypted_new_value:
                continue
            try:
                value = decrypt(sibling.encrypted_new_value)
            except (TypeError, ValueError):
                value = ""
            if value not in (None, ""):
                payload[target] = str(value).strip()
        # Older materialized rows can already contain the values under the
        # labels used by the web/Excel adapters. Normalize those aliases too.
        for source_key, target_key in (
            ("Nombres", "first_name"), ("nombre", "first_name"),
            ("Apellidos", "last_name"), ("apellido", "last_name"),
            ("Tipo_ID", "id_type"), ("tipo_id", "id_type"),
            ("N_mero_de_ID", "document"), ("documento", "document"),
        ):
            if not payload.get(target_key) and payload.get(source_key) not in (None, ""):
                payload[target_key] = str(payload[source_key]).strip()
        item_key = str(payload.get("functional_key") or f"position-{change.position}").strip()
        if not item_key:
            item_key = f"position-{change.position}"
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        policy_remote_id = _stored_policy_record_id(
            change.policy.encrypted_policy_token if change.policy else ""
        )
        # The change-level policy is the authoritative source for a
        # multi-policy response.  Preserve its visible branch value in the
        # operational payload so later entity processing can select the same
        # contract as the individual quotation flow, without another remote
        # lookup or a guessed generic branch.
        policy_branch_name = str(
            getattr(change.policy, "branch_name", "") or request.branch_name or ""
        ).strip()
        if policy_branch_name:
            payload["ramo"] = policy_branch_name
        # A small number of legacy responses do not retain the policy FK on
        # the action marker.  A single-policy request still has an
        # unambiguous protected source; never guess for a multi-policy one.
        if not policy_remote_id:
            policy_candidates = list(request.policies.filter(active=True).order_by("position")[:2])
            if len(policy_candidates) == 1:
                policy_remote_id = _stored_policy_record_id(
                    policy_candidates[0].encrypted_policy_token
                )
        if not policy_remote_id and request.encrypted_policy_token:
            # Legacy single-policy requests kept the signed reference only on
            # the parent request.  Recover it locally before considering any
            # remote fallback; never guess among multiple policies.
            policy_remote_id = _stored_policy_record_id(request.encrypted_policy_token)
        item, created = NovedadIngresoZoho.objects.get_or_create(
            request=request,
            item_key=item_key,
            defaults={
                "branch_code": request.branch_code or "",
                "policy_remote_id": policy_remote_id,
                "encrypted_payload": encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
                "payload_hash": digest,
            },
        )
        if not created:
            # Existing rows may have been materialized before the canonical
            # sibling-field payload was introduced.  Reconcile only the
            # operational data/error; never reset confirmed entity IDs or an
            # uncertain write state.
            update_fields = []
            terminal_or_uncertain = {
                NovedadIngresoZoho.Status.PUBLISHED,
                NovedadIngresoZoho.Status.RECONCILE_REQUIRED,
            }
            if item.status not in terminal_or_uncertain and item.payload_hash != digest:
                item.encrypted_payload = encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                item.payload_hash = digest
                update_fields.extend(("encrypted_payload", "payload_hash"))
            if policy_remote_id and not item.policy_remote_id:
                item.policy_remote_id = policy_remote_id
                update_fields.append("policy_remote_id")

            # Once the signed parent policy reference is recovered locally,
            # this historic materialization error is no longer active.
            if (
                policy_remote_id
                and str(item.safe_error or "").strip()
                == "No fue posible resolver la póliza Zoho del ingreso."
            ):
                item.safe_error = ""
                if item.status == NovedadIngresoZoho.Status.BLOCKED:
                    item.status = NovedadIngresoZoho.Status.PENDING
                update_fields.extend(("safe_error", "status"))

            contact_data = {
                "First_Name": payload.get("first_name"),
                "Last_Name": payload.get("last_name"),
                "Tipo_ID": payload.get("id_type"),
                "N_mero_de_ID": payload.get("document"),
            }
            missing = contact_identity_missing_fields(contact_data)
            data_error = "Faltan datos para procesar el ingreso:" in str(item.safe_error or "")
            if item.status == NovedadIngresoZoho.Status.BLOCKED and data_error:
                next_error = (
                    "Faltan datos para procesar el ingreso: " + ", ".join(missing)
                    if missing else ""
                )
                item.safe_error = next_error
                item.status = NovedadIngresoZoho.Status.BLOCKED if missing else NovedadIngresoZoho.Status.PENDING
                item.reconcile_required = False
                update_fields.extend(("safe_error", "status", "reconcile_required"))
            if update_fields:
                item.save(update_fields=tuple(dict.fromkeys((*update_fields, "updated_at"))))
        result.append(item)
    return result


def novelty_ingress_attachments(request: SolicitudColectivo, item_key: str):
    """Return only person-level attachments for one ingress row.

    Excel imports remain evidence at response level and are deliberately
    excluded from this operational document path.
    """
    response = request.responses.filter(status=RespuestaSolicitudColectivo.Status.SUBMITTED).order_by("-version").first()
    if response is None:
        return ()
    for change in response.changes.filter(action=CambioSolicitudColectivo.Action.INCLUDE, functional_field="accion").prefetch_related("attachments"):
        try:
            marker = json.loads(decrypt(change.encrypted_branch_payload or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            marker = {}
        key = str(marker.get("functional_key") or f"position-{change.position}").strip()
        if key == str(item_key).strip():
            return tuple(change.attachments.exclude(category="EXCEL_IMPORT"))
    return ()


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
    if access.expires_at <= timezone.now():
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
    access.otp_expires_at = min(
        now + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS),
        access.expires_at,
    )
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
    if locked.expires_at <= now:
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
        or access.request.status in {access.request.Status.CLOSED, access.request.Status.CANCELLED, access.request.Status.EXPIRED}
    ):
        raise ExternalAccessError("La sesión externa no está disponible.")
    return access


def response_checksum(rows: list[dict[str, object]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _draft_change_rows(response: RespuestaSolicitudColectivo) -> list[dict[str, str]]:
    """Rebuild actionable rows from the current draft for incremental saves."""
    rows = []
    markers = response.changes.filter(
        functional_field="accion",
        action__in=(CambioSolicitudColectivo.Action.INCLUDE, CambioSolicitudColectivo.Action.RETIRE, CambioSolicitudColectivo.Action.MODIFY),
    ).order_by("position", "id")
    for marker in markers:
        try:
            source = json.loads(decrypt(marker.encrypted_branch_payload or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            source = {}
        records = tuple(str(value) for value in (source.get("source_record_keys") or ()) if str(value).strip())
        row = {
            "record": records[0] if records else "",
            "records": records,
            "policy": str(marker.policy_id or ""),
            "action": marker.action,
            "functional_key": str(source.get("functional_key") or ""),
        }
        for change in response.changes.filter(action=marker.action, position=marker.position).exclude(functional_field="accion"):
            try:
                row[change.functional_field] = decrypt(change.encrypted_new_value or "")
            except Exception:
                row[change.functional_field] = ""
        rows.append(row)
    return rows


def _draft_row_identity(row: Mapping[str, object]) -> tuple[str, ...]:
    action = str(row.get("action") or "").strip().upper()
    if action == CambioSolicitudColectivo.Action.INCLUDE:
        functional_key = str(row.get("functional_key") or "").strip()
        if functional_key:
            return (action, functional_key)
        return (
            action,
            str(row.get("policy") or "").strip(),
            str(row.get("tipo_id") or "").strip(),
            str(row.get("documento") or "").strip(),
        )
    records = tuple(sorted(str(value).strip() for value in (row.get("records") or (row.get("record") or "",)) if str(value).strip()))
    return (action, *records)


@transaction.atomic
def save_response(*, access: AccesoExternoSolicitudColectivo, rows: list[dict[str, str]], observations: str, origin: str = "WEB", relationship_choices=None) -> RespuestaSolicitudColectivo:
    request = SolicitudColectivo.objects.select_for_update().get(pk=access.request_id)
    if request.status not in {request.Status.OPENED, request.Status.CORRECTION}:
        raise ExternalAccessError("La solicitud no admite cambios.")
    current = request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).order_by("-version").first()
    if current and origin == "WEB":
        existing_rows = _draft_change_rows(current)
        incoming_keys = {_draft_row_identity(row) for row in rows if str(row.get("action") or "").strip().upper() != CambioSolicitudColectivo.Action.UNCHANGED}
        rows = list(rows) + [row for row in existing_rows if _draft_row_identity(row) not in incoming_keys]
    normalized = []
    seen = set()
    policy_count = request.policies.count()
    scoped_policy_ids = None
    if policy_count == 1:
        scoped_policy_ids = set(request.policies.values_list("id", flat=True))
    valid_id_types = None
    if any(str(row.get("action", "")).strip().upper() == CambioSolicitudColectivo.Action.INCLUDE for row in rows):
        try:
            valid_id_types = identification_type_values()
        except CatalogUnavailable as exc:
            raise ExternalAccessError("No fue posible cargar los tipos de identificación vigentes.") from exc
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
                request.records.select_related("policy").filter(
                    public_key__in=record_keys,
                    **({"policy_id__in": scoped_policy_ids} if scoped_policy_ids is not None else {}),
                )
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
            policy = request.policies.filter(
                pk=int(policy_key), active=True,
                **({"pk__in": scoped_policy_ids} if scoped_policy_ids is not None else {}),
            ).first()
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
                fields["tipo_id"] not in valid_id_types
                or fields["rol"] not in RELATION_ROLE_CHOICES
                or not fields["documento"].isdigit()
                or not fields["nombres"]
                or not fields["apellidos"]
            ):
                raise ExternalAccessError("El ingreso requiere nombres, apellidos, identificación y documento válidos.")
            contract_branch_code = getattr(policy, "branch_code", "") if policy is not None else getattr(request, "branch_code", "")
            contract_branch_name = getattr(policy, "branch_name", "") if policy is not None else getattr(request, "branch_name", "")
            if relationship_choices is not None and "parentesco" in contract_required_ingress_fields(contract_branch_code, contract_branch_name) and not fields["parentesco"]:
                raise ExternalAccessError("El parentesco es obligatorio para este ingreso.")
        # ``relationship_choices`` is supplied by the catalog boundary as
        # ``(value, label)`` pairs (the same representation used by the
        # HTML select).  Validation must compare the submitted canonical
        # value against the first member of each pair, never against the
        # pair object itself.  Legacy callers still provide the historical
        # flat tuple of strings.
        if relationship_choices is None:
            allowed_relationships = tuple(RELATIONSHIP_CHOICES)
        else:
            allowed_relationships = tuple(
                str(choice[0] if isinstance(choice, (tuple, list)) else getattr(choice, "value", choice) or "").strip()
                for choice in relationship_choices
            )
        if fields["parentesco"] and fields["parentesco"] not in allowed_relationships:
            raise ExternalAccessError("El parentesco seleccionado no es válido.")
        if fields["estado"] and fields["estado"] not in INSURED_STATE_CHOICES:
            raise ExternalAccessError("El estado seleccionado no es válido.")
        normalized.append({
            "action": action, "record": record, "source_records": tuple(source_records),
            "functional_key": str(row.get("functional_key") or ""),
            "policy": policy, "fields": fields, "position": position,
        })
    prior_attachments = list(current.attachments.all()) if current else []
    prior_attachment_keys = {}
    for attachment in prior_attachments:
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        key = str(metadata.get("functional_key") or "")
        if key:
            prior_attachment_keys.setdefault(key, []).append(attachment)
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
            "origin": origin,
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
            CambioSolicitudColectivo.objects.create(
                response=response, policy=item["policy"], original_record=item["record"],
                action=item["action"], functional_field=field,
                encrypted_previous_value=encrypt(prior), encrypted_new_value=encrypt(value),
                encrypted_observation=encrypt(value) if field == "observaciones" else "",
                encrypted_branch_payload=source_payload, position=item["position"], checksum=digest,
            )
    new_changes_by_key = {}
    for change in response.changes.filter(functional_field="accion"):
        try:
            payload = json.loads(decrypt(change.encrypted_branch_payload or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        key = str(payload.get("functional_key") or "")
        if key:
            new_changes_by_key[key] = change
    for key, attachments in prior_attachment_keys.items():
        target_change = new_changes_by_key.get(key)
        if not target_change:
            continue
        for attachment in attachments:
            attachment.response = response
            attachment.change = target_change
            attachment.save(update_fields=("response", "change"))
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_DRAFT_SAVED", origin="EXTERNO", safe_metadata={"version": version, "changes": response.changes.count()})
    return response


@transaction.atomic
def submit_response(*, access: AccesoExternoSolicitudColectivo, response: RespuestaSolicitudColectivo, declaration: bool, no_changes: bool = False) -> RespuestaSolicitudColectivo:
    # Lock only the response row.  ``access`` is nullable, so joining it
    # through ``select_related`` would make PostgreSQL reject the
    # ``FOR UPDATE`` query (it cannot lock the nullable side of an outer
    # join).  The related objects are loaded lazily after the row lock while
    # the surrounding transaction remains active.
    locked = RespuestaSolicitudColectivo.objects.select_for_update().get(pk=response.pk)
    if locked.status == locked.Status.SUBMITTED:
        return locked
    valid_novelties = locked.changes.filter(
        action__in=(CambioSolicitudColectivo.Action.INCLUDE, CambioSolicitudColectivo.Action.RETIRE, CambioSolicitudColectivo.Action.MODIFY),
        validation_status__in=(CambioSolicitudColectivo.Validation.VALID, CambioSolicitudColectivo.Validation.WARNING),
    )
    if locked.status != locked.Status.DRAFT or not declaration or locked.changes.filter(validation_status=CambioSolicitudColectivo.Validation.INVALID).exists():
        raise ExternalAccessError("La respuesta no está lista para enviar.")
    has_changes = valid_novelties.exists()
    if no_changes and has_changes:
        raise ExternalAccessError("Ha registrado novedades. Desmarque 'No tengo novedades' para continuar.")
    if not has_changes and not no_changes:
        raise ExternalAccessError("Registre al menos una novedad o confirme que no tiene novedades para este periodo.")
    now = timezone.now()
    locked.status = locked.Status.SUBMITTED
    locked.declaration_confirmed = True
    locked.submitted_at = now
    metadata = dict(locked.safe_metadata or {})
    metadata["response_type"] = "NO_CHANGES" if no_changes else "CHANGES"
    locked.safe_metadata = metadata
    locked.save(update_fields=("status", "declaration_confirmed", "submitted_at", "safe_metadata", "updated_at"))
    request = locked.request
    request.transition_to(request.Status.ANSWERED)
    request.save(update_fields=("status", "updated_at"))
    kinds = set()
    for change in locked.changes.all():
        if change.action == CambioSolicitudColectivo.Action.INCLUDE:
            kinds.add("INCLUSION")
        elif change.action == CambioSolicitudColectivo.Action.RETIRE:
            kinds.add("RETIRO")
    snapshot = request_snapshot(request)
    policy_snapshot = snapshot.get("policy") if isinstance(snapshot, dict) else {}
    seller = str(policy_snapshot.get("seller") or "").strip() if isinstance(policy_snapshot, dict) else ""
    for kind in sorted(kinds):
        label = "Ingreso" if kind == "INCLUSION" else "Retiro"
        observations = _task_novelty_observations(
            request,
            locked.changes.select_related("policy", "original_record").all(),
            label,
        )
        outbox = enqueue_task(
            source=request,
            payload=ColectivosTaskPayload(
                request_kind=kind,
                source_kind="request",
                policy_context=str(request.public_id),
                branch_code=str(request.branch_code),
                local_reference=str(request.public_id),
                subject=f"{label} · {request.branch_name or 'Ramo'} · {request.client_label or 'Cliente'}"[:255],
                area=NOVELTIES_TASK_AREA,
                analyst_request=NOVELTIES_ANALYST_REQUEST,
                seller=seller,
                observations=observations,
            ),
            event_version=locked.version,
        )
        # The response transaction must commit before any Zoho write is
        # attempted.  The outbox remains the single idempotent source of
        # truth; publication failures are handled by its existing guards and
        # status transitions.
        transaction.on_commit(lambda outbox_id=outbox.pk: _publish_novelty_task_outbox(outbox_id))
    access.status = access.Status.USED
    access.used_for_submission_at = now
    access.save(update_fields=("status", "used_for_submission_at"))
    RenovacionColectiva.objects.filter(access=access).update(
        status=RenovacionColectiva.Status.RESPONDED,
        responded_at=now,
        last_activity_at=now,
        updated_at=now,
    )
    EventoSolicitudColectivo.objects.create(request=request, event_type="EXTERNAL_RESPONSE_SUBMITTED", origin="EXTERNO", new_status=request.status, safe_metadata={"version": locked.version})
    result_label = "Sin novedades" if no_changes else "Con novedades"
    _notify(
        request,
        "CLIENT_RESPONSE",
        "Sin novedades" if no_changes else "Novedad recibida",
        f"El cliente respondió: {result_label.lower()} · póliza {request.masked_policy_reference}.",
        str(locked.version),
    )
    # Email delivery is independent from the response transaction and task
    # outbox.  It runs only after the accepted response is committed.
    transaction.on_commit(lambda response_id=locked.pk: _send_novelty_response_alert(response_id))
    return locked


def _send_novelty_response_alert(response_id: int) -> None:
    try:
        response = RespuestaSolicitudColectivo.objects.select_related("request").get(pk=response_id)
        send_novelty_response_email(response=response)
    except Exception:
        # The response is already valid; delivery failures must not alter it.
        logger.exception("colectivos_novelty_response_email_failed response_id=%s", response_id)


def _publish_novelty_task_outbox(outbox_id: int) -> None:
    """Publish a novelty task after commit without affecting the response."""
    try:
        publish_task_outbox(outbox_id)
    except Exception:
        logger.exception("colectivos_novelty_task_publish_failed outbox_id=%s", outbox_id)


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
