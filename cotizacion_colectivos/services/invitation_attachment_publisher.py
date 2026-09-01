"""Prepare and publish an insurer invitation file for one signed policy."""
from __future__ import annotations

import base64
import hashlib
import secrets
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from vault.crypto import encrypt

from ..filenames import build_attachment_filename
from ..models import InvitacionAseguradoraAdjunto
from .common import ColectivosServiceError, unsign_record_context
from .individual_attachment_publisher import publish_attachment
from .invitation_templates import generate_invitation_templates, preview_invitation_templates


INVITATION_ATTACHMENT_FEATURE_FLAG = "COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED"


def prepare_invitation_attachment(*, token: str, insurer_code: str, template_code: str = "", zoho=None):
    """Generate one insurer file, persist it locally, and publish it to Polizas.

    ``token`` is the only source of the policy Record ID. Consolidated tokens
    are rejected because they do not identify one Polizas record.
    """
    context = unsign_record_context(token, "policy")
    policy_record_id = str(context["id"])
    detail, _previews, _metadata = preview_invitation_templates(token)
    content, generated_name, content_type, errors = generate_invitation_templates(
        token, template_code=str(template_code or ""), insurer_code=str(insurer_code or ""),
    )
    if errors:
        raise ColectivosServiceError("template_unavailable", "La plantilla de invitación no está completa.")
    insurer = str(insurer_code or "").strip()
    if not insurer:
        raise ValidationError("La aseguradora es obligatoria.")
    canonical_name = build_attachment_filename(
        document_type="invitation_document", original_filename=generated_name,
        policy_number=detail.full_reference or detail.masked_reference,
        detail=f"{insurer}_{detail.branch_code}",
    )
    checksum = hashlib.sha256(content).hexdigest()
    extension = Path(canonical_name).suffix.lower()
    with transaction.atomic():
        existing = InvitacionAseguradoraAdjunto.objects.select_for_update().filter(
            policy_record_id=policy_record_id, insurer_code=insurer,
            template_code=str(template_code or ""), checksum=checksum,
        ).first()
        if existing is None:
            root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
            root.mkdir(parents=True, exist_ok=True)
            internal_name = f"{secrets.token_hex(24)}{extension}"
            target = (root / "individual_quotations" / internal_name).resolve()
            folder = target.parent
            folder.mkdir(parents=True, exist_ok=True)
            if root not in target.parents:
                raise ValidationError("La ruta de almacenamiento no es válida.")
            target.write_bytes(encrypt(base64.b64encode(content).decode()).encode())
            try:
                existing = InvitacionAseguradoraAdjunto.objects.create(
                    policy_record_id=policy_record_id, insurer_code=insurer,
                    template_code=str(template_code or ""), safe_original_name=generated_name,
                    internal_name=internal_name, extension=extension,
                    detected_mime=content_type, size=len(content), checksum=checksum,
                    stored_path=internal_name,
                    safe_metadata={
                        "owner_type": "policy", "document_type": "invitation_document",
                        "policy_record_id": policy_record_id, "insurer_code": insurer,
                        "template_code": str(template_code or ""),
                        "checksum": checksum,
                        "content_type": content_type,
                        "filename_detail": f"{insurer}_{detail.branch_code}",
                        "policy_number": detail.full_reference or detail.masked_reference,
                        "canonical_filename": canonical_name,
                    },
                )
            except IntegrityError:
                existing = InvitacionAseguradoraAdjunto.objects.select_for_update().get(
                    policy_record_id=policy_record_id, insurer_code=insurer,
                    template_code=str(template_code or ""), checksum=checksum,
                )
        metadata = existing.safe_metadata if isinstance(existing.safe_metadata, dict) else {}
        uploaded = metadata.get("zoho_status") == "uploaded" and metadata.get("zoho_record_id") == policy_record_id
    if uploaded:
        return {"status": "UPLOADED", "attachment_id": metadata.get("zoho_attachment_id"), "filename": canonical_name, "policy_record_id": policy_record_id}
    result = publish_attachment(
        attachment=existing, module="Polizas", record_id=policy_record_id, zoho=zoho,
        feature_flag=INVITATION_ATTACHMENT_FEATURE_FLAG,
    )
    return {"status": str(result.get("status") if isinstance(result, dict) else "UPLOADED"), "attachment_id": (result.get("attachment_id") if isinstance(result, dict) else getattr(result, "attachment_id", "")), "filename": canonical_name, "policy_record_id": policy_record_id}
