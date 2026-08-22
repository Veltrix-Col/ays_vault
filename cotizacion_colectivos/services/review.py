from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from vault.crypto import decrypt, encrypt

from ..models import EventoSolicitudColectivo, NotificacionColectivos, RespuestaSolicitudColectivo, RevisionSolicitudColectivo, SolicitudColectivo


@transaction.atomic
def record_reviews(*, response: RespuestaSolicitudColectivo, reviewer, decisions: dict[int, dict[str, str]]) -> None:
    locked = RespuestaSolicitudColectivo.objects.select_for_update().select_related("request").get(pk=response.pk)
    if locked.status != locked.Status.SUBMITTED or locked.request.status not in {SolicitudColectivo.Status.ANSWERED, SolicitudColectivo.Status.REVIEW}:
        raise ValidationError("La respuesta no está disponible para revisión.")
    if locked.request.status == SolicitudColectivo.Status.ANSWERED:
        locked.request.transition_to(SolicitudColectivo.Status.REVIEW)
        locked.request.save(update_fields=("status", "updated_at"))
    valid = set(RevisionSolicitudColectivo.Decision.values)
    changes = {item.pk: item for item in locked.changes.all()}
    for change_id, data in decisions.items():
        if change_id not in changes or data.get("decision") not in valid:
            raise ValidationError("Existe una decisión no válida.")
        version = (changes[change_id].reviews.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        digest = hashlib.sha256(f"{locked.pk}:{change_id}:{version}:{data['decision']}".encode()).hexdigest()
        RevisionSolicitudColectivo.objects.create(request=locked.request, response=locked, change=changes[change_id], reviewer=reviewer, decision=data["decision"], encrypted_approved_value=encrypt(data.get("approved_value", "")[:500]), encrypted_internal_observation=encrypt(data.get("internal_observation", "")[:1000]), encrypted_client_observation=encrypt(data.get("client_observation", "")[:1000]), version=version, checksum=digest)
    EventoSolicitudColectivo.objects.create(request=locked.request, actor=reviewer, event_type="REVIEW_UPDATED", safe_metadata={"decisions": len(decisions), "response_version": locked.version})


@transaction.atomic
def finalize_review(*, response: RespuestaSolicitudColectivo, reviewer, action: str) -> SolicitudColectivo:
    locked = RespuestaSolicitudColectivo.objects.select_for_update().select_related("request").get(pk=response.pk)
    request = SolicitudColectivo.objects.select_for_update().get(pk=locked.request_id)
    changes = list(locked.changes.prefetch_related("reviews"))
    latest = [change.reviews.order_by("-version").first() for change in changes]
    if not changes or any(review is None for review in latest):
        raise ValidationError("Todas las novedades requieren decisión.")
    if action == "approve":
        if any(review.decision not in {RevisionSolicitudColectivo.Decision.APPROVE, RevisionSolicitudColectivo.Decision.ADJUST} for review in latest):
            raise ValidationError("Existen decisiones pendientes o rechazadas.")
        request.transition_to(request.Status.APPROVED)
        locked.status = locked.Status.APPROVED
        locked.save(update_fields=("status", "updated_at"))
        event = "RESPONSE_APPROVED"
    elif action == "correction":
        if not any(review.decision == RevisionSolicitudColectivo.Decision.CORRECTION for review in latest):
            raise ValidationError("Debe marcar al menos una corrección.")
        request.transition_to(request.Status.CORRECTION)
        event = "CORRECTION_REQUESTED"
    else:
        raise ValidationError("La acción de revisión no es válida.")
    request.save(update_fields=("status", "updated_at"))
    EventoSolicitudColectivo.objects.create(request=request, actor=reviewer, event_type=event, new_status=request.status, safe_metadata={"response_version": locked.version})
    NotificacionColectivos.objects.get_or_create(user=request.assigned_to, deduplication_key=f"{event}:{request.uuid}:{locked.version}", defaults={"request": request, "notification_type": event, "title": "Revisión actualizada", "message": f"La solicitud {request.public_id} cambió en revisión.", "priority": "NORMAL"})
    if action == "correction":
        previous_access = locked.access
        if previous_access is None:
            raise ValidationError("No existe un destinatario seguro para solicitar la corrección.")
        from .external import generate_access, send_invitation

        generated = generate_access(
            request=request,
            actor=reviewer,
            recipient=decrypt(previous_access.encrypted_recipient),
            contact_name=decrypt(previous_access.encrypted_contact_name),
            intro="A&S requiere una corrección sobre la respuesta enviada.",
            instructions="Revise únicamente los campos señalados por el equipo de A&S.",
            regenerate=True,
        )
        transaction.on_commit(lambda: send_invitation(generated))
    return request
