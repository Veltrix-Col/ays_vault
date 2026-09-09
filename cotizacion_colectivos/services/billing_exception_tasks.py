"""Builder cerrado e idempotencia local para Tasks de casos de facturación.

No realiza IO. Publicar el outbox continúa dependiendo exclusivamente del
publisher y de sus guards de escritura por perfil.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection

from django.core.exceptions import ValidationError
from django.db import transaction

from vault.crypto import encrypt

from ..models import BillingOperationalCase, ColectivosTaskOutbox


BILLING_TASK_FIELDS = frozenset({
    "Subject", "tipo_de_solicitud", "Caso_de_excepci_n", "Motivo_de_excepci_n",
    "N_mero_p_liza", "rea", "Observaciones", "Ramo", "Aseguradora1",
    "Responsable", "Correo_responsable",
})


def build_billing_exception_task(
    case: BillingOperationalCase,
    *, responsible: str = "", responsible_email: str = "",
    allowed_responsibles: Collection[str] = (),
    allowed_branches: Collection[str] = (),
    allowed_insurers: Collection[str] = (),
) -> dict[str, object]:
    """Construye únicamente el contrato Tasks confirmado por metadata Production."""
    findings = tuple(case.exceptions.order_by("exception_type", "exception_key"))
    if not findings:
        raise ValidationError("El caso no contiene hallazgos técnicos.")
    selected = str(responsible or "").strip()
    if selected and selected not in set(allowed_responsibles):
        raise ValidationError("El Responsable no coincide con un actual_value permitido en Production.")
    label = (
        case.operation_name if case.operation_id and case.operation_name
        else f"Cuota {case.installment_number}" if case.kind == case.Kind.MISSING_OPERATION
        else "Cobro faltante"
    )
    reasons = "; ".join(item.functional_name for item in findings)
    record: dict[str, object] = {
        "Subject": f"Excepción facturación | {case.policy_number} | {label}"[:255],
        "tipo_de_solicitud": "Facturación", "Caso_de_excepci_n": True,
        "Motivo_de_excepci_n": reasons[:2000], "N_mero_p_liza": case.policy_number,
        "rea": "Cartera",
        "Observaciones": f"Caso operativo de facturación. {reasons}. Referencia: {case.case_key}"[:2000],
    }
    if case.branch and case.branch in set(allowed_branches):
        record["Ramo"] = case.branch
    if case.insurer and case.insurer in set(allowed_insurers):
        record["Aseguradora1"] = [case.insurer]
    if selected:
        record["Responsable"] = selected
        email = str(responsible_email or "").strip()
        if email:
            if "@" not in email or len(email) > 254:
                raise ValidationError("El correo del Responsable no es válido.")
            record["Correo_responsable"] = email
    if set(record) - BILLING_TASK_FIELDS:
        raise ValidationError("El payload contiene campos Tasks no autorizados.")
    return record


@transaction.atomic
def enqueue_billing_exception_task(
    case: BillingOperationalCase, *, record: dict[str, object], event_version: int = 1,
) -> ColectivosTaskOutbox:
    locked = BillingOperationalCase.objects.select_for_update().get(pk=case.pk)
    if locked.zoho_task_id:
        raise ValidationError("El caso ya tiene una Task Zoho asociada.")
    if set(record) - BILLING_TASK_FIELDS:
        raise ValidationError("El payload contiene campos Tasks no autorizados.")
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(serialized.encode()).hexdigest()
    key = hashlib.sha256(f"billing-case:{locked.pk}:{locked.case_key}:{event_version}".encode()).hexdigest()
    item, _created = ColectivosTaskOutbox.objects.get_or_create(
        idempotency_key=key,
        defaults={"billing_case": locked, "event_kind": "BILLING_EXCEPTION",
                  "event_version": event_version, "encrypted_payload": encrypt(serialized),
                  "payload_checksum": checksum},
    )
    if item.payload_checksum != checksum:
        raise ValidationError("La intención idempotente ya existe con otro payload.")
    return item
