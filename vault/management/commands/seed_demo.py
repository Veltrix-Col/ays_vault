from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
import hashlib

from vault.management.commands.load_colombia_holidays import colombia_holidays
from vault.models import AccessException, AuditEvent, Holiday, NotificationRecipient, NotificationRecord, PaymentCard, PolicyConfiguration, SecurityAlert, UserProfile
from vault.security import audit


def with_luhn_check_digit(prefix):
    for digit in range(10):
        candidate = f"{prefix}{digit}"
        total = 0
        parity = len(candidate) % 2
        for index, character in enumerate(candidate):
            value = int(character)
            if index % 2 == parity:
                value = value * 2 - 9 if value * 2 > 9 else value * 2
            total += value
        if total % 10 == 0:
            return candidate
    raise RuntimeError("No fue posible generar el dato demo.")


class Command(BaseCommand):
    help = "Crea usuarios individuales y 30 tarjetas ficticias idempotentes."

    def add_arguments(self, parser):
        parser.add_argument("--reset-demo", action="store_true", help="Elimina únicamente las tarjetas Cliente Demo antes de recrearlas.")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        users = {}
        # Preserve but disable legacy shared-style demo accounts from earlier builds.
        for legacy in User.objects.filter(username__in=["adminvault", "lidercartera", "analistacartera"]):
            legacy.is_active = False
            legacy.save(update_fields=["is_active"])
            if hasattr(legacy, "vault_profile"):
                legacy.vault_profile.active = False
                legacy.vault_profile.save(update_fields=["active"])
        people = [
            ("admin.seguridad", UserProfile.ADMIN, "Adriana", "Seguridad"),
            ("laura.cartera", UserProfile.LEADER, "Laura", "Cartera"),
            ("andres.analista", UserProfile.ANALYST, "Andrés", "Analista"),
        ]
        for username, role, first_name, last_name in people:
            user, _ = User.objects.get_or_create(username=username, defaults={"first_name": first_name, "last_name": last_name, "email": f"{username}@example.invalid"})
            user.set_password("DemoSeguro2026!")
            user.is_active = True
            user.is_staff = role == UserProfile.ADMIN
            user.is_superuser = role == UserProfile.ADMIN
            user.save()
            user.vault_profile.role = role
            user.vault_profile.active = True
            user.vault_profile.save()
            users[role] = user

        if options["reset_demo"]:
            PaymentCard.objects.filter(client_name__startswith="Cliente Demo ").delete()

        brands = ["VISA", "MC", "AMEX"]
        for index in range(1, 31):
            brand = brands[(index - 1) % len(brands)]
            if brand == "VISA":
                pan = with_luhn_check_digit(f"4111111111{index:05d}")
            elif brand == "MC":
                pan = with_luhn_check_digit(f"5555555555{index:05d}")
            else:
                pan = with_luhn_check_digit(f"3782822463{index:04d}")
            client_name = f"Cliente Demo {index:02d}"
            card = PaymentCard.objects.filter(client_name=client_name).first()
            if not card:
                card = PaymentCard(client_name=client_name, created_by=users[UserProfile.LEADER])
            card.cardholder_name = f"Titular Ficticio {index:02d}"
            card.brand = brand
            card.purpose = "Pago de obligaciones autorizadas — dato completamente ficticio"
            card.active = index % 10 != 0
            card.updated_by = users[UserProfile.LEADER]
            card.set_pan(pan)
            card.set_expiry(f"{(index % 12) + 1:02d}/{27 + (index % 4):02d}")
            if not card.encrypted_company:
                card.set_company(f"Empresa Ficticia {index:02d} S.A.S.")
            card.save()

        policy, _ = PolicyConfiguration.objects.get_or_create(singleton=1)
        policy.updated_by = users[UserProfile.ADMIN]
        policy.save(update_fields=["updated_by", "updated_at"])

        current_year = timezone.localdate().year
        for value, name in list(colombia_holidays(current_year).items())[:5]:
            Holiday.objects.update_or_create(date=value, defaults={"name": name, "national": True, "internal": False})
        Holiday.objects.update_or_create(date=timezone.localdate() + timedelta(days=20), defaults={"name": "Jornada interna demo", "national": False, "internal": True})

        now = timezone.now()
        AccessException.objects.update_or_create(
            name="Excepcion activa demo",
            defaults={"exception_type": AccessException.ALLOW, "role": UserProfile.LEADER, "starts_at": now - timedelta(hours=1), "ends_at": now + timedelta(hours=2), "operations": ["VIEW", "REVEAL", "COPY"], "reason": "Prueba controlada de continuidad operativa", "created_by": users[UserProfile.ADMIN], "status": AccessException.ACTIVE},
        )
        AccessException.objects.update_or_create(
            name="Excepcion expirada demo",
            defaults={"exception_type": AccessException.EXTEND, "starts_at": now - timedelta(days=5), "ends_at": now - timedelta(days=4), "reason": "Cierre mensual ficticio", "created_by": users[UserProfile.ADMIN], "status": AccessException.EXPIRED},
        )

        demo_events = [
            ("demo-login-old", "LOGIN", users[UserProfile.LEADER], None, now - timedelta(days=35), "LOW", "SUCCESS"),
            ("demo-reveal-old", "REVEAL", users[UserProfile.LEADER], PaymentCard.objects.filter(active=True).first(), now - timedelta(days=35), "LOW", "SUCCESS"),
            ("demo-copy-old", "COPY", users[UserProfile.ANALYST], PaymentCard.objects.filter(active=True).first(), now - timedelta(days=35), "LOW", "SUCCESS"),
            ("demo-device-new", "DEVICE_NEW", users[UserProfile.ANALYST], None, now - timedelta(hours=3), "MEDIUM", "SUCCESS"),
            ("demo-outside-hours", "LOGIN", users[UserProfile.LEADER], None, now - timedelta(hours=2), "HIGH", "SUCCESS"),
        ]
        for key, action, user, card, occurred_at, risk, result in demo_events:
            if not AuditEvent.objects.filter(metadata__demo_key=key).exists():
                audit(None, action, user=user, card=card, reason="Evento ficticio de demostracion", metadata={"demo_key": key}, risk_level=risk, result=result, occurred_at=occurred_at)

        alert_specs = [
            ("demo-alert-critical", "AUDIT_INTEGRITY_REVIEW", "CRITICAL", "Revision ficticia de integridad."),
            ("demo-alert-high", "POSSIBLE_PARALLEL_TOOL_USE", "HIGH", "El sistema no presenta actividad operativa reciente; validar adopcion del proceso."),
            ("demo-alert-medium", "NEW_DEVICE", "MEDIUM", "Dispositivo nuevo ficticio."),
            ("demo-alert-low", "HOLIDAY_UPCOMING", "LOW", "Festivo proximo."),
        ]
        for key, alert_type, severity, description in alert_specs:
            event = AuditEvent.objects.filter(metadata__demo_key=key).first()
            if not event:
                event = audit(None, "ACCESS", user=users[UserProfile.ADMIN], reason="Alerta ficticia", metadata={"demo_key": key}, risk_level=severity)
            SecurityAlert.objects.update_or_create(event=event, defaults={"alert_type": alert_type, "severity": severity, "affected_user": users[UserProfile.LEADER], "description": description, "recommendation": "Validar el caso con el responsable del proceso.", "due_at": now + timedelta(hours=24)})

        recipient, _ = NotificationRecipient.objects.update_or_create(name="Administrador demo", defaults={"email": "administrador@example.invalid", "minimum_severity": "LOW", "active": True, "is_primary": True, "updated_by": users[UserProfile.ADMIN]})
        demo_alert = SecurityAlert.objects.filter(alert_type="NEW_DEVICE").first()
        if demo_alert:
            idem = hashlib.sha256(f"demo-notification:{demo_alert.pk}".encode()).hexdigest()
            NotificationRecord.objects.update_or_create(idempotency_hash=idem, defaults={"alert": demo_alert, "notification_type": demo_alert.alert_type, "masked_recipient": "a***@example.invalid", "recipient_hash": hashlib.sha256(recipient.email.encode()).hexdigest(), "result": NotificationRecord.SENT, "attempts": 1, "backend": "console", "sent_at": now})

        self.stdout.write(self.style.SUCCESS("Demo lista: tarjetas, usuarios, politicas, festivos, excepciones, alertas y actividad ficticia."))
