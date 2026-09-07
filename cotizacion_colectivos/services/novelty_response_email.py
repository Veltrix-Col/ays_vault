from __future__ import annotations

import logging

from django.conf import settings
from django.template.loader import render_to_string

from vault.notifications import send_notification

from .requests import request_snapshot

logger = logging.getLogger("cotizacion_colectivos")


def _context(response):
    request = response.request
    snapshot = request_snapshot(request)
    policy = snapshot.get("policy") if isinstance(snapshot, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    policy_row = request.policies.order_by("position").first()
    changes = list(response.changes.values_list("action", flat=True))
    kinds = {"Ingreso" if value == "INCLUIR" else "Retiro" for value in changes if value in {"INCLUIR", "RETIRAR"}}
    novelty_type = " / ".join(sorted(kinds)) or "Novedad"
    seller = str(policy.get("seller") or "").strip() or "—"
    insurer = str(policy.get("insurer") or getattr(policy_row, "insurer", "") or "").strip() or "—"
    base = str(getattr(settings, "VAULT_BASE_URL", "") or "").rstrip("/")
    logo_base = str(getattr(settings, "COLECTIVOS_EXTERNAL_BASE_URL", base) or base).rstrip("/")
    expediente_url = f"{base}/cotizacion-colectivos/solicitudes/{request.public_id}/" if base else ""
    return {
        "client": request.client_label or "—",
        "policy": request.masked_policy_reference or "—",
        "branch": request.branch_name or "—",
        "insurer": insurer,
        "seller": seller,
        "novelty_type": novelty_type,
        "responded_at": response.submitted_at,
        "expediente_url": expediente_url,
        "logo_url": f"{logo_base}/static/img/branding/logo-ays-azul.png" if logo_base else "",
    }


def send_novelty_response_email(*, response, recipient: str | None = None, test: bool = False):
    recipient = str(recipient if recipient is not None else getattr(settings, "COLECTIVOS_NOVELTY_RESPONSE_ALERT_EMAIL", "") or "").strip()
    if not recipient:
        logger.warning("colectivos_novelty_response_email_skipped reason=recipient_not_configured request_id=%s", response.request_id)
        return None
    context = _context(response)
    subject = f"Nueva respuesta de Novedades · {context['client']} · Póliza {context['policy']}"
    html = render_to_string("cotizacion_colectivos/email/novelty_response_alert.html", context)
    text = render_to_string("cotizacion_colectivos/email/novelty_response_alert.txt", context)
    key = f"colectivos-novelty-response-test:{response.pk}" if test else f"colectivos-novelty-response:{response.pk}"
    return send_notification(
        notification_type="COLECTIVOS_NOVELTY_RESPONSE_ALERT",
        recipient=recipient,
        subject=subject,
        text_body=text,
        html_body=html,
        idempotency_key=key,
    )
