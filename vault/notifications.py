import hashlib
import json
import logging
import re
import smtplib
import socket
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils import timezone

from .email_config import normalized_backend
from .forms import ALERT_TYPE_CHOICES
from .models import NotificationRecipient, NotificationRecord
from .tasks import run_async


SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
logger = logging.getLogger("vault.notifications")


class EmailDeliveryError(Exception):
    def __init__(self, safe_code, retryable=False):
        super().__init__(safe_code)
        self.safe_code = safe_code
        self.retryable = retryable


def mask_email(value):
    local, separator, domain = (value or "").partition("@")
    if not separator:
        return "***"
    return f"{local[:1]}***@{domain}"


def _recipient_hash(value):
    key = settings.FIELD_FINGERPRINT_KEY or settings.SECRET_KEY
    return hashlib.sha256(f"{key}:email:{value.lower()}".encode()).hexdigest()


def configured_recipients(alert):
    recipients = []
    for recipient in NotificationRecipient.objects.filter(active=True, delivery_mode=NotificationRecipient.IMMEDIATE):
        if recipient.alert_types and alert.alert_type not in recipient.alert_types:
            continue
        if SEVERITY_RANK[alert.severity] < SEVERITY_RANK[recipient.minimum_severity]:
            continue
        recipients.append(recipient.email)
    if not recipients:
        recipients = [value for value in (settings.ALERT_EMAIL_ADMIN, settings.ALERT_EMAIL_LEADER) if value]
    return sorted(set(recipients))


class MicrosoftGraphEmailBackend:
    name = "graph"

    def send(self, subject, text_body, html_body, recipient):
        import msal

        authority = f"https://login.microsoftonline.com/{settings.MS_GRAPH_TENANT_ID}"
        app = msal.ConfidentialClientApplication(
            settings.MS_GRAPH_CLIENT_ID,
            authority=authority,
            client_credential=settings.MS_GRAPH_CLIENT_SECRET,
        )
        token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        access_token = token.get("access_token")
        if not access_token:
            raise EmailDeliveryError("GRAPH_AUTHENTICATION_FAILED")
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
            "saveToSentItems": True,
        }
        request = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/users/{settings.MS_GRAPH_SENDER}/sendMail",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.EMAIL_TIMEOUT_SECONDS) as response:
                if response.status not in {200, 202}:
                    raise EmailDeliveryError(f"GRAPH_HTTP_{response.status}", retryable=response.status == 429 or response.status >= 500)
                return response.headers.get("request-id", "")
        except urllib.error.HTTPError as exc:
            raise EmailDeliveryError(f"GRAPH_HTTP_{exc.code}", retryable=exc.code == 429 or exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise EmailDeliveryError("GRAPH_CONNECTION_ERROR", retryable=True) from exc


class ConsoleEmailBackend:
    name = "console"

    def send(self, subject, text_body, html_body, recipient):
        connection = get_connection(backend=settings.EMAIL_BACKEND, fail_silently=False)
        message = EmailMultiAlternatives(subject, text_body, settings.ALERT_EMAIL_FROM, [recipient], connection=connection)
        message.attach_alternative(html_body, "text/html")
        if message.send(fail_silently=False) != 1:
            raise EmailDeliveryError("CONSOLE_EMAIL_NOT_SENT")
        return ""


class SMTPEmailBackend:
    name = "smtp"

    def send(self, subject, text_body, html_body, recipient):
        try:
            connection = get_connection(
                backend="django.core.mail.backends.smtp.EmailBackend",
                fail_silently=False,
                host=settings.EMAIL_HOST,
                port=settings.EMAIL_PORT,
                username=settings.EMAIL_HOST_USER,
                password=settings.EMAIL_HOST_PASSWORD,
                use_tls=settings.EMAIL_USE_TLS,
                use_ssl=settings.EMAIL_USE_SSL,
                timeout=settings.EMAIL_TIMEOUT_SECONDS,
            )
            message = EmailMultiAlternatives(
                subject,
                text_body,
                settings.ALERT_EMAIL_FROM or settings.DEFAULT_FROM_EMAIL,
                [recipient],
                connection=connection,
            )
            message.attach_alternative(html_body, "text/html")
            if message.send(fail_silently=False) != 1:
                raise EmailDeliveryError("SMTP_EMAIL_NOT_SENT", retryable=True)
            return ""
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailDeliveryError("SMTP_AUTHENTICATION_FAILED") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailDeliveryError("SMTP_RECIPIENT_REJECTED") from exc
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError, ConnectionError) as exc:
            raise EmailDeliveryError("SMTP_CONNECTION_ERROR", retryable=True) from exc
        except EmailDeliveryError:
            raise
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError("SMTP_DELIVERY_ERROR", retryable=True) from exc


def get_backend():
    backend = normalized_backend()
    if backend == "console":
        return ConsoleEmailBackend()
    if backend == "smtp":
        return SMTPEmailBackend()
    if backend == "graph":
        return MicrosoftGraphEmailBackend()
    raise EmailDeliveryError("EMAIL_BACKEND_NOT_SUPPORTED")


def _redact_email_content(value, alert=None):
    safe = value or ""
    safe = re.sub(r"(?<!\d)\d{13,19}(?!\d)", "[DATO PROTEGIDO OMITIDO]", safe)
    safe = re.sub(r"\b(?:0[1-9]|1[0-2])/\d{2,4}\b", "[DATO PROTEGIDO OMITIDO]", safe)
    safe = re.sub(r"(?<!\d)\d{6}(?!\d)", "[CÓDIGO OMITIDO]", safe)
    for secret in (getattr(settings, "EMAIL_HOST_PASSWORD", ""), getattr(settings, "MS_GRAPH_CLIENT_SECRET", "")):
        if secret:
            safe = safe.replace(secret, "[SECRETO OMITIDO]")
    card = getattr(getattr(alert, "event", None), "card", None) if alert else None
    if card and card.has_company:
        company = card.get_company()
        if company:
            safe = re.sub(re.escape(company), "[DATO PROTEGIDO OMITIDO]", safe, flags=re.IGNORECASE)
    return safe


def _audit_alert_delivery(record, alert):
    if not alert:
        return
    from .security import audit

    actor = alert.actor or getattr(alert.event, "user", None)
    audit(
        None,
        "EMAIL_SENT" if record.result == NotificationRecord.SENT else "EMAIL_FAILED",
        user=actor,
        reason="Resultado de entrega de notificación",
        metadata={
            "notification_id": record.pk,
            "notification_type": record.notification_type,
            "backend": record.backend,
            "recipient": record.masked_recipient,
            "delivery_result": record.result,
            "attempts": record.attempts,
            "safe_error_code": record.safe_error_code,
        },
    )


def send_notification(*, notification_type, recipient, subject, text_body, html_body, idempotency_key, alert=None, force_retry=False):
    idempotency = hashlib.sha256(idempotency_key.encode()).hexdigest()
    record, created = NotificationRecord.objects.get_or_create(
        idempotency_hash=idempotency,
        defaults={
            "alert": alert,
            "notification_type": notification_type,
            "masked_recipient": mask_email(recipient),
            "recipient_hash": _recipient_hash(recipient),
            "backend": normalized_backend(),
        },
    )
    if not created and record.result == NotificationRecord.SENT:
        return record
    if not created and not force_retry and record.attempts >= settings.EMAIL_MAX_RETRIES:
        return record

    subject = _redact_email_content(subject, alert)
    text_body = _redact_email_content(text_body, alert)
    html_body = _redact_email_content(html_body, alert)
    try:
        backend = get_backend()
    except EmailDeliveryError as exc:
        record.attempts += 1
        record.backend = normalized_backend()
        record.result = NotificationRecord.FAILED
        record.safe_error_code = exc.safe_code
        record.next_attempt_at = None
        record.save(update_fields=["attempts", "backend", "result", "safe_error_code", "next_attempt_at"])
        logger.warning("Fallo de correo id=%s backend=no-reconocido intento=%s codigo=%s", record.pk, record.attempts, exc.safe_code)
        _audit_alert_delivery(record, alert)
        return record
    remaining_attempts = 1 if force_retry else max(1, settings.EMAIL_MAX_RETRIES - record.attempts)
    while remaining_attempts:
        remaining_attempts -= 1
        record.attempts += 1
        record.backend = backend.name
        try:
            record.external_id = backend.send(subject, text_body, html_body, recipient)[:160]
            record.result = NotificationRecord.SENT
            record.sent_at = timezone.now()
            record.safe_error_code = ""
            record.next_attempt_at = None
            break
        except EmailDeliveryError as exc:
            record.safe_error_code = exc.safe_code
            will_retry = exc.retryable and remaining_attempts > 0
            record.result = NotificationRecord.RETRY if will_retry else NotificationRecord.FAILED
            record.next_attempt_at = timezone.now() + timedelta(minutes=min(5 * record.attempts, 30)) if will_retry else None
            logger.warning(
                "Fallo de correo id=%s backend=%s intento=%s codigo=%s",
                record.pk,
                backend.name,
                record.attempts,
                exc.safe_code,
            )
            if not exc.retryable:
                break
        except Exception:
            record.safe_error_code = "EMAIL_DELIVERY_ERROR"
            record.result = NotificationRecord.FAILED
            record.next_attempt_at = None
            logger.warning("Fallo de correo id=%s backend=%s intento=%s codigo=EMAIL_DELIVERY_ERROR", record.pk, backend.name, record.attempts)
            break
    record.save(update_fields=["attempts", "backend", "external_id", "result", "sent_at", "safe_error_code", "next_attempt_at"])
    _audit_alert_delivery(record, alert)
    return record


def send_alert_notification(alert, recipient, force_retry=False):
    context = {"alert": alert, "detail_url": f"{settings.VAULT_BASE_URL}/security/alerts/{alert.pk}/"}
    alert_label = dict(ALERT_TYPE_CHOICES).get(alert.alert_type, "Alerta de seguridad")
    action = getattr(getattr(alert, "event", None), "action", "")
    if action == "LOGIN":
        subject = "A&S Vault | Inicio de sesión fuera del horario habitual"
    elif action == "REVEAL":
        subject = "A&S Vault | Revelado de tarjeta fuera del horario habitual"
    else:
        subject = f"[A&S Vault] {alert.get_severity_display()}: {alert_label}"
    return send_notification(
        notification_type=alert.alert_type,
        recipient=recipient,
        subject=subject,
        text_body=render_to_string("vault/email/alert.txt", context),
        html_body=render_to_string("vault/email/alert.html", context),
        idempotency_key=f"alert:{alert.pk}:{alert.alert_type}:{recipient.lower()}",
        alert=alert,
        force_retry=force_retry,
    )


def notify_alert(alert):
    return [send_alert_notification(alert, recipient) for recipient in configured_recipients(alert)]


def automatic_alert_email_allowed(alert):
    """Correo automático solo para login/revelado fuera de horario o fin de semana."""
    event = getattr(alert, "event", None)
    return bool(
        event
        and event.action in {"LOGIN", "REVEAL"}
        and event.outside_office_hours
    )


def notify_alert_by_id(alert_id):
    from .models import SecurityAlert

    try:
        alert = SecurityAlert.objects.get(pk=alert_id)
    except SecurityAlert.DoesNotExist:
        return []
    if not automatic_alert_email_allowed(alert):
        return []
    return notify_alert(alert)


def notify_alert_async(alert):
    """Punto de reemplazo por notify_alert_by_id.delay(alert.pk) cuando exista un broker Celery."""
    run_async(notify_alert_by_id, alert.pk)


def retry_notification(record):
    recipients = configured_recipients(record.alert) if record.alert else []
    matching = [address for address in recipients if _recipient_hash(address) == record.recipient_hash]
    if not matching:
        record.result = NotificationRecord.FAILED
        record.safe_error_code = "RECIPIENT_CONFIGURATION_CHANGED"
        record.save(update_fields=["result", "safe_error_code"])
        return record
    return send_alert_notification(record.alert, matching[0], force_retry=True)


def build_periodic_summary(days=1):
    """Construye agregados seguros; el envío automático permanece deshabilitado."""
    from .models import AccessException, AuditEvent, SecurityAlert

    since = timezone.now() - timedelta(days=days)
    return {
        "period_days": days,
        "activity": AuditEvent.objects.filter(created_at__gte=since).count(),
        "new_alerts": SecurityAlert.objects.filter(created_at__gte=since).count(),
        "pending_alerts": SecurityAlert.objects.exclude(status__in=["CLOSED", "JUSTIFIED"]).count(),
        "outside_hours_access": AuditEvent.objects.filter(created_at__gte=since, outside_office_hours=True, action="LOGIN").count(),
        "new_devices": AuditEvent.objects.filter(created_at__gte=since, action="DEVICE_NEW").count(),
        "active_exceptions": AccessException.objects.filter(status="ACTIVE", ends_at__gte=timezone.now()).count(),
        "email_failures": NotificationRecord.objects.filter(created_at__gte=since, result__in=["FAILED", "RETRY"]).count(),
    }
