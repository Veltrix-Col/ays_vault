import hashlib
import json
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import NotificationRecipient, NotificationRecord
from .forms import ALERT_TYPE_CHOICES


SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def mask_email(value):
    local, separator, domain = (value or "").partition("@")
    if not separator:
        return "***"
    visible = local[:1]
    return f"{visible}***@{domain}"


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
        fallback = [settings.ALERT_EMAIL_ADMIN, settings.ALERT_EMAIL_LEADER]
        recipients = [value for value in fallback if value]
    return sorted(set(recipients))


class MicrosoftGraphEmailBackend:
    name = "microsoft_graph"

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
            raise RuntimeError("GRAPH_TOKEN_FAILED")
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
                    raise RuntimeError(f"GRAPH_HTTP_{response.status}")
                return response.headers.get("request-id", "")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GRAPH_HTTP_{exc.code}") from exc


class DjangoEmailBackend:
    name = "django_email"

    def send(self, subject, text_body, html_body, recipient):
        message = EmailMultiAlternatives(subject, text_body, settings.ALERT_EMAIL_FROM, [recipient])
        message.attach_alternative(html_body, "text/html")
        sent = message.send(fail_silently=False)
        if sent != 1:
            raise RuntimeError("EMAIL_NOT_SENT")
        return ""


def get_backend():
    if settings.ALERT_EMAIL_BACKEND == "microsoft_graph":
        return MicrosoftGraphEmailBackend()
    return DjangoEmailBackend()


def send_alert_notification(alert, recipient, force_retry=False):
    idempotency = hashlib.sha256(f"alert:{alert.pk}:{alert.alert_type}:{recipient.lower()}".encode()).hexdigest()
    record, created = NotificationRecord.objects.get_or_create(
        idempotency_hash=idempotency,
        defaults={
            "alert": alert,
            "notification_type": alert.alert_type,
            "masked_recipient": mask_email(recipient),
            "recipient_hash": _recipient_hash(recipient),
            "backend": settings.ALERT_EMAIL_BACKEND,
        },
    )
    if not created and record.result == NotificationRecord.SENT:
        return record
    if not created and not force_retry and record.attempts >= settings.EMAIL_MAX_RETRIES:
        return record
    context = {"alert": alert, "detail_url": f"{settings.VAULT_BASE_URL}/security/alerts/{alert.pk}/"}
    alert_label = dict(ALERT_TYPE_CHOICES).get(alert.alert_type, "Alerta de seguridad")
    subject = f"[A&S Vault] {alert.get_severity_display()}: {alert_label}"
    text_body = render_to_string("vault/email/alert.txt", context)
    html_body = render_to_string("vault/email/alert.html", context)
    backend = get_backend()
    record.attempts += 1
    record.backend = backend.name
    try:
        record.external_id = backend.send(subject, text_body, html_body, recipient)[:160]
        record.result = NotificationRecord.SENT
        record.sent_at = timezone.now()
        record.safe_error_code = ""
        record.next_attempt_at = None
    except Exception as exc:
        code = str(exc).splitlines()[0][:80]
        record.result = NotificationRecord.FAILED if record.attempts >= settings.EMAIL_MAX_RETRIES else NotificationRecord.RETRY
        record.safe_error_code = code if code.isascii() else exc.__class__.__name__
        record.next_attempt_at = timezone.now() + timedelta(minutes=min(5 * record.attempts, 30))
    record.save(update_fields=["attempts", "backend", "external_id", "result", "sent_at", "safe_error_code", "next_attempt_at"])
    return record


def notify_alert(alert):
    return [send_alert_notification(alert, recipient) for recipient in configured_recipients(alert)]


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
    """Builds safe aggregate data only; scheduling and automatic delivery stay opt-in."""
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
