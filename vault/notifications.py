import hashlib
import base64
import json
import logging
import re
import smtplib
import socket
import urllib.error
import urllib.request
from datetime import timedelta
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils import timezone

from .email_config import normalized_backend
from .forms import ALERT_TYPE_CHOICES
from .models import NotificationRecipient, NotificationRecord
from .tasks import run_async


SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
logger = logging.getLogger("vault.notifications")
EMAIL_BRAND_CID = "cardmanager-brand-logo"
EMAIL_BRAND_STATIC_PATH = "img/branding/cardmanager/Logo-CardManager-CO-BLANCO.png"
OTP_NOTIFICATION_TYPES = frozenset({"COLECTIVOS_OTP", "COLECTIVOS_INDIVIDUAL_OTP"})


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


def _user_agent_summary(value):
    user_agent = value or ""
    browser = "Microsoft Edge" if "Edg/" in user_agent else "Chrome" if "Chrome/" in user_agent else "Firefox" if "Firefox/" in user_agent else "Safari" if "Safari/" in user_agent else "No disponible"
    operating_system = "Windows" if "Windows" in user_agent else "Android" if "Android" in user_agent else "iOS" if "iPhone" in user_agent or "iPad" in user_agent else "macOS" if "Mac OS" in user_agent else "Linux" if "Linux" in user_agent else "No disponible"
    device_type = "Móvil" if any(marker in user_agent for marker in ("Mobile", "Android", "iPhone")) else "Escritorio"
    return browser, operating_system, device_type


def _outside_schedule_reason(alert):
    from .models import Holiday
    from .policies import get_policy

    event = getattr(alert, "event", None)
    event_time = timezone.localtime(event.created_at) if event else timezone.localtime()
    holiday = Holiday.objects.filter(date=event_time.date(), working_day=False).first()
    if holiday:
        return f"Festivo configurado: {holiday.name}"
    policy = get_policy()
    if event_time.weekday() == 6 and not policy.sunday_enabled:
        return "Día no laboral: domingo"
    if event_time.weekday() == 5 and not policy.saturday_enabled:
        return "Fin de semana: sábado no laborable"
    return "Fuera de la jornada configurada"


def configured_recipients(alert):
    recipients = list(
        NotificationRecipient.objects.filter(active=True).values_list("email", flat=True)
    )
    if not recipients:
        recipients = [value for value in (settings.ALERT_EMAIL_ADMIN, settings.ALERT_EMAIL_LEADER) if value]
    return sorted(set(recipients))


def _email_brand_logo_bytes():
    logo_path = finders.find(EMAIL_BRAND_STATIC_PATH)
    if not logo_path:
        logger.warning("No fue posible localizar el logo de CardManager para el correo.")
        return b""
    try:
        return Path(logo_path).read_bytes()
    except OSError:
        logger.exception("No fue posible leer el logo de CardManager para el correo.")
        return b""


def _attach_email_brand_logo(message, html_body):
    if f"cid:{EMAIL_BRAND_CID}" not in (html_body or ""):
        return
    logo_bytes = _email_brand_logo_bytes()
    if not logo_bytes:
        return
    image = MIMEImage(logo_bytes, _subtype="png")
    image.add_header("Content-ID", f"<{EMAIL_BRAND_CID}>")
    image.add_header(
        "Content-Disposition",
        "inline",
        filename="cardmanager-ays-white.png",
    )
    message.mixed_subtype = "related"
    message.attach(image)


def _graph_brand_attachment(html_body):
    if f"cid:{EMAIL_BRAND_CID}" not in (html_body or ""):
        return []
    logo_bytes = _email_brand_logo_bytes()
    if not logo_bytes:
        return []
    return [{
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "cardmanager-ays-white.png",
        "contentType": "image/png",
        "contentId": EMAIL_BRAND_CID,
        "isInline": True,
        "contentBytes": base64.b64encode(logo_bytes).decode("ascii"),
    }]


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
                "attachments": _graph_brand_attachment(html_body),
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
        _attach_email_brand_logo(message, html_body)
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
            _attach_email_brand_logo(message, html_body)
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


def _redact_email_content(value, alert=None, *, redact_one_time_code=True):
    safe = value or ""
    safe = re.sub(r"(?<!\d)\d{13,19}(?!\d)", "[DATO PROTEGIDO OMITIDO]", safe)
    # Un vencimiento aislado (MM/AA o MM/AAAA) se redacta, pero el mismo
    # fragmento dentro de una fecha legítima DD/MM/AAAA no se considera un
    # dato de tarjeta.
    safe = re.sub(
        r"(?<![\d/])(?:0[1-9]|1[0-2])/\d{2,4}(?![\d/])",
        "[DATO PROTEGIDO OMITIDO]",
        safe,
    )
    if redact_one_time_code:
        safe = re.sub(r"(?<!\d)\d{6}(?!\d)", "[CÓDIGO OMITIDO]", safe)
    for secret in (getattr(settings, "EMAIL_HOST_PASSWORD", ""), getattr(settings, "MS_GRAPH_CLIENT_SECRET", "")):
        if secret:
            safe = safe.replace(secret, "[SECRETO OMITIDO]")
    card = getattr(getattr(alert, "event", None), "card", None) if alert else None
    if card and card.has_code:
        code = card.get_code()
        if code:
            safe = re.sub(re.escape(code), "[DATO PROTEGIDO OMITIDO]", safe, flags=re.IGNORECASE)
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


def send_notification(
    *,
    notification_type,
    recipient,
    subject,
    text_body,
    html_body,
    idempotency_key,
    alert=None,
    force_retry=False,
    require_external_delivery=False,
    contains_one_time_code=False,
):
    if contains_one_time_code and notification_type not in OTP_NOTIFICATION_TYPES:
        raise ValueError("Sólo las notificaciones OTP autorizadas pueden entregar un código de un solo uso.")
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
    if (
        not created
        and record.result == NotificationRecord.SENT
        and (not require_external_delivery or record.backend in {"smtp", "graph"})
    ):
        return record
    if (
        not created
        and not force_retry
        and record.attempts >= settings.EMAIL_MAX_RETRIES
        and not (require_external_delivery and record.backend not in {"smtp", "graph"})
    ):
        return record

    redact_one_time_code = not contains_one_time_code
    subject = _redact_email_content(
        subject, alert, redact_one_time_code=redact_one_time_code,
    )
    text_body = _redact_email_content(
        text_body, alert, redact_one_time_code=redact_one_time_code,
    )
    html_body = _redact_email_content(
        html_body, alert, redact_one_time_code=redact_one_time_code,
    )
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
    if require_external_delivery and backend.name not in {"smtp", "graph"}:
        django_backend = str(getattr(settings, "EMAIL_BACKEND", "")).lower()
        record.attempts += 1
        record.backend = backend.name
        record.result = NotificationRecord.FAILED
        record.safe_error_code = (
            "EMAIL_BACKEND_CONSOLE"
            if "console" in django_backend
            else "EMAIL_BACKEND_LOCAL"
        )
        record.next_attempt_at = None
        record.save(
            update_fields=[
                "attempts",
                "backend",
                "result",
                "safe_error_code",
                "next_attempt_at",
            ]
        )
        logger.warning(
            "Prueba externa de correo rechazada id=%s backend=%s codigo=%s",
            record.pk,
            backend.name,
            record.safe_error_code,
        )
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
    action = getattr(getattr(alert, "event", None), "action", "")
    event = getattr(alert, "event", None)
    browser, operating_system, device_type = _user_agent_summary(getattr(event, "user_agent", ""))
    is_login_outside = action == "LOGIN"
    is_reveal_outside = action == "REVEAL"
    if is_login_outside:
        title = "Inicio de sesión fuera del horario permitido"
        message = (
            "Se detectó un inicio de sesión en CardManager fuera del horario laboral configurado. "
            "Revise la información del acceso y confirme que corresponda a una actividad autorizada."
        )
    elif is_reveal_outside:
        title = "Revelado de información protegida fuera del horario permitido"
        message = (
            "Se detectó el revelado de información protegida en CardManager fuera del horario laboral "
            "configurado. Revise los detalles del evento y confirme que corresponda a una gestión autorizada."
        )
    else:
        title = dict(ALERT_TYPE_CHOICES).get(alert.alert_type, "Alerta de seguridad")
        message = alert.description
    context = {
        "alert": alert,
        "detail_url": f"{settings.VAULT_BASE_URL}/security/alerts/{alert.pk}/",
        "title": title,
        "message": message,
        "is_login_outside": is_login_outside,
        "is_reveal_outside": is_reveal_outside,
        "schedule_reason": _outside_schedule_reason(alert),
        "browser": browser,
        "operating_system": operating_system,
        "device_type": device_type,
    }
    alert_label = dict(ALERT_TYPE_CHOICES).get(alert.alert_type, "Alerta de seguridad")
    if action == "LOGIN":
        subject = "CardManager | Inicio de sesión fuera del horario permitido"
    elif action == "REVEAL":
        subject = "CardManager | Revelado de información fuera del horario permitido"
    else:
        subject = f"[CardManager] {alert.get_severity_display()}: {alert_label}"
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
    """Correo automático solo para login o revelado fuera del horario permitido."""
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
    if record.alert and not automatic_alert_email_allowed(record.alert):
        record.result = NotificationRecord.FAILED
        record.safe_error_code = "AUTOMATIC_EMAIL_NOT_ALLOWED"
        record.next_attempt_at = None
        record.save(update_fields=["result", "safe_error_code", "next_attempt_at"])
        return record
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
