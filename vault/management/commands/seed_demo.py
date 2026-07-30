import hashlib
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from vault.management.commands.load_colombia_holidays import colombia_holidays
from vault.models import (
    AccessException,
    AuditEvent,
    Holiday,
    NotificationRecipient,
    NotificationRecord,
    PaymentCard,
    PolicyConfiguration,
    SecurityAlert,
    UserProfile,
)
from vault.security import audit


DEMO_USERNAMES = (
    "admin.seguridad",
    "laura.cartera",
    "andres.analista",
)

LEGACY_DEMO_USERNAMES = (
    "adminvault",
    "lidercartera",
    "analistacartera",
)

DEMO_CARD_PREFIX = "Cliente Demo "
DEMO_EMAIL_DOMAIN = "example.invalid"


def with_luhn_check_digit(prefix: str) -> str:
    """Completa un número ficticio con un dígito de control Luhn válido."""
    for digit in range(10):
        candidate = f"{prefix}{digit}"
        total = 0
        parity = len(candidate) % 2

        for index, character in enumerate(candidate):
            value = int(character)

            if index % 2 == parity:
                doubled = value * 2
                value = doubled - 9 if doubled > 9 else doubled

            total += value

        if total % 10 == 0:
            return candidate

    raise RuntimeError("No fue posible generar el número ficticio demo.")


def safe_demo_datetime(*, days_ago: int = 0, hours_ago: int = 0):
    """
    Genera una fecha ficticia en horario laboral para evitar que los eventos
    demo representen accesos fuera de horario.

    Se retrocede hasta encontrar un día de lunes a viernes y se fija a las 10:00.
    """
    local_date = timezone.localdate() - timedelta(days=days_ago)

    while local_date.weekday() >= 5:
        local_date -= timedelta(days=1)

    local_value = datetime.combine(local_date, time(hour=10))
    aware_value = timezone.make_aware(
        local_value,
        timezone.get_current_timezone(),
    )

    return aware_value - timedelta(hours=hours_ago)


class Command(BaseCommand):
    help = (
        "Crea usuarios, tarjetas, políticas, festivos, excepciones, auditoría "
        "y alertas completamente ficticias sin realizar envíos externos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-demo",
            action="store_true",
            help=(
                "Elimina únicamente las tarjetas cuyo alias comienza por "
                f"'{DEMO_CARD_PREFIX}' antes de recrearlas."
            ),
        )

    @override_settings(
        # La ejecución del seed nunca utiliza SMTP ni Microsoft Graph.
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALERT_EMAIL_BACKEND="console",
    )
    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(
            "Creando información ficticia con notificaciones externas deshabilitadas..."
        )

        users = self._create_demo_users()

        if options["reset_demo"]:
            deleted_count, _ = PaymentCard.objects.filter(
                client_name__startswith=DEMO_CARD_PREFIX
            ).delete()

            self.stdout.write(
                f"Tarjetas demo eliminadas antes de recrear: {deleted_count}"
            )

        self._create_demo_cards(users)
        self._create_policy(users)
        self._create_holidays()
        self._create_access_exceptions(users)
        self._create_demo_audit_events(users)
        self._create_demo_alerts(users)
        self._create_inactive_demo_recipient(users)
        self._create_demo_notification_record()

        self.stdout.write(
            self.style.SUCCESS(
                "Demo lista: usuarios, tarjetas, políticas, festivos, "
                "excepciones, auditoría y alertas ficticias."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "No se enviaron correos externos. SMTP y Microsoft Graph "
                "permanecieron deshabilitados únicamente durante este comando."
            )
        )

    def _create_demo_users(self):
        User = get_user_model()
        users = {}

        # Desactiva cuentas demo compartidas heredadas.
        for legacy in User.objects.filter(username__in=LEGACY_DEMO_USERNAMES):
            update_fields = []

            if legacy.is_active:
                legacy.is_active = False
                update_fields.append("is_active")

            if update_fields:
                legacy.save(update_fields=update_fields)

            if hasattr(legacy, "vault_profile") and legacy.vault_profile.active:
                legacy.vault_profile.active = False
                legacy.vault_profile.save(update_fields=["active"])

        people = (
            (
                "admin.seguridad",
                UserProfile.ADMIN,
                "Adriana",
                "Seguridad",
            ),
            (
                "laura.cartera",
                UserProfile.LEADER,
                "Laura",
                "Cartera",
            ),
            (
                "andres.analista",
                UserProfile.ANALYST,
                "Andrés",
                "Analista",
            ),
        )

        for username, role, first_name, last_name in people:
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": f"{username}@{DEMO_EMAIL_DOMAIN}",
                },
            )

            user.first_name = first_name
            user.last_name = last_name
            user.email = f"{username}@{DEMO_EMAIL_DOMAIN}"
            user.set_password("DemoSeguro2026!")
            user.is_active = True
            user.is_staff = role == UserProfile.ADMIN
            user.is_superuser = role == UserProfile.ADMIN
            user.save()

            profile = user.vault_profile
            profile.role = role
            profile.active = True
            profile.save(update_fields=["role", "active"])

            users[role] = user

        return users

    def _create_demo_cards(self, users):
        brands = ("VISA", "MC", "AMEX")

        for index in range(1, 31):
            brand = brands[(index - 1) % len(brands)]

            if brand == "VISA":
                pan = with_luhn_check_digit(f"4111111111{index:05d}")
            elif brand == "MC":
                pan = with_luhn_check_digit(f"5555555555{index:05d}")
            else:
                pan = with_luhn_check_digit(f"3782822463{index:04d}")

            client_name = f"{DEMO_CARD_PREFIX}{index:02d}"

            card = PaymentCard.objects.filter(client_name=client_name).first()

            if card is None:
                card = PaymentCard(
                    client_name=client_name,
                    created_by=users[UserProfile.LEADER],
                )

            card.company_name = f"Empresa Ficticia {index:02d} S.A.S."
            card.cardholder_name = f"Titular Ficticio {index:02d}"
            card.brand = brand
            card.purpose = (
                "Pago de obligaciones autorizadas — dato completamente ficticio"
            )
            card.active = index % 10 != 0
            card.updated_by = users[UserProfile.LEADER]

            # Datos protegidos completamente ficticios.
            card.set_pan(pan)
            card.set_expiry(f"{(index % 12) + 1:02d}/{27 + (index % 4):02d}")
            card.set_code(f"COD-{index:04d}")

            card.save()

    def _create_policy(self, users):
        policy, _ = PolicyConfiguration.objects.get_or_create(singleton=1)
        policy.updated_by = users[UserProfile.ADMIN]
        policy.save(update_fields=["updated_by", "updated_at"])

    def _create_holidays(self):
        current_year = timezone.localdate().year

        for value, name in list(colombia_holidays(current_year).items())[:5]:
            Holiday.objects.update_or_create(
                date=value,
                defaults={
                    "name": name,
                    "national": True,
                    "internal": False,
                },
            )

        Holiday.objects.update_or_create(
            date=timezone.localdate() + timedelta(days=20),
            defaults={
                "name": "Jornada interna demo",
                "national": False,
                "internal": True,
            },
        )

    def _create_access_exceptions(self, users):
        now = timezone.now()

        AccessException.objects.update_or_create(
            name="Excepción activa demo",
            defaults={
                "exception_type": AccessException.ALLOW,
                "role": UserProfile.LEADER,
                "starts_at": now - timedelta(hours=1),
                "ends_at": now + timedelta(hours=2),
                "operations": ["VIEW", "REVEAL", "COPY"],
                "reason": "Prueba controlada de continuidad operativa",
                "created_by": users[UserProfile.ADMIN],
                "status": AccessException.ACTIVE,
            },
        )

        AccessException.objects.update_or_create(
            name="Excepción expirada demo",
            defaults={
                "exception_type": AccessException.EXTEND,
                "starts_at": now - timedelta(days=5),
                "ends_at": now - timedelta(days=4),
                "reason": "Cierre mensual ficticio",
                "created_by": users[UserProfile.ADMIN],
                "status": AccessException.EXPIRED,
            },
        )

    def _create_demo_audit_events(self, users):
        active_card = PaymentCard.objects.filter(
            client_name__startswith=DEMO_CARD_PREFIX,
            active=True,
        ).first()

        # Los LOGIN y REVEAL se ubican en horario laboral ficticio para no
        # representar eventos que deban disparar alertas externas.
        demo_events = (
            (
                "demo-login-old",
                "LOGIN",
                users[UserProfile.LEADER],
                None,
                safe_demo_datetime(days_ago=35),
                "LOW",
                "SUCCESS",
            ),
            (
                "demo-reveal-old",
                "REVEAL",
                users[UserProfile.LEADER],
                active_card,
                safe_demo_datetime(days_ago=35, hours_ago=1),
                "LOW",
                "SUCCESS",
            ),
            (
                "demo-copy-old",
                "COPY",
                users[UserProfile.ANALYST],
                active_card,
                safe_demo_datetime(days_ago=35, hours_ago=2),
                "LOW",
                "SUCCESS",
            ),
            (
                "demo-device-new",
                "DEVICE_NEW",
                users[UserProfile.ANALYST],
                None,
                safe_demo_datetime(days_ago=1),
                "MEDIUM",
                "SUCCESS",
            ),
            (
                "demo-access-review",
                "ACCESS",
                users[UserProfile.LEADER],
                None,
                safe_demo_datetime(days_ago=1, hours_ago=1),
                "MEDIUM",
                "SUCCESS",
            ),
        )

        for (
            key,
            action,
            user,
            card,
            occurred_at,
            risk,
            result,
        ) in demo_events:
            if AuditEvent.objects.filter(metadata__demo_key=key).exists():
                continue

            audit(
                None,
                action,
                user=user,
                card=card,
                reason="Evento ficticio de demostración",
                metadata={
                    "demo_key": key,
                    "demo": True,
                    "external_notification_allowed": False,
                },
                risk_level=risk,
                result=result,
            )

    def _create_demo_alerts(self, users):
        now = timezone.now()

        alert_specs = (
            (
                "demo-alert-critical",
                "AUDIT_INTEGRITY_REVIEW",
                "CRITICAL",
                "Revisión ficticia de integridad.",
            ),
            (
                "demo-alert-high",
                "POSSIBLE_PARALLEL_TOOL_USE",
                "HIGH",
                (
                    "Actividad ficticia para validar la visualización del "
                    "Centro de Control."
                ),
            ),
            (
                "demo-alert-medium",
                "NEW_DEVICE",
                "MEDIUM",
                "Dispositivo nuevo ficticio.",
            ),
            (
                "demo-alert-low",
                "HOLIDAY_UPCOMING",
                "LOW",
                "Festivo próximo ficticio.",
            ),
        )

        for key, alert_type, severity, description in alert_specs:
            event = AuditEvent.objects.filter(metadata__demo_key=key).first()

            if event is None:
                event = audit(
                            None,
                            "ACCESS",
                            user=users[UserProfile.ADMIN],
                            reason="Alerta ficticia",
                            metadata={
                                "demo_key": key,
                                "demo": True,
                                "external_notification_allowed": False,
                            },
                            risk_level=severity,
                            result="SUCCESS",
                        )

            SecurityAlert.objects.update_or_create(
                event=event,
                defaults={
                    "alert_type": alert_type,
                    "severity": severity,
                    "affected_user": users[UserProfile.LEADER],
                    "description": description,
                    "recommendation": (
                        "Validar el caso ficticio con el responsable del proceso."
                    ),
                    "due_at": now + timedelta(hours=24),
                },
            )

    def _create_inactive_demo_recipient(self, users):
        """
        Conserva un registro visual para probar la pantalla de destinatarios,
        pero nunca queda activo ni primario.
        """
        NotificationRecipient.objects.update_or_create(
            name="Administrador demo",
            defaults={
                "email": f"administrador@{DEMO_EMAIL_DOMAIN}",
                "minimum_severity": "LOW",
                "active": False,
                "is_primary": False,
                "updated_by": users[UserProfile.ADMIN],
            },
        )

    def _create_demo_notification_record(self):
        """
        Crea únicamente el registro visual de una entrega ficticia.

        No llama al servicio de notificaciones ni a ningún backend externo.
        """
        alert = SecurityAlert.objects.filter(
            event__metadata__demo_key="demo-alert-high"
        ).first()
        if alert is None:
            return

        recipient = f"administrador@{DEMO_EMAIL_DOMAIN}"
        idempotency_hash = hashlib.sha256(
            b"cardmanager-seed-demo-notification-v1"
        ).hexdigest()
        NotificationRecord.objects.update_or_create(
            idempotency_hash=idempotency_hash,
            defaults={
                "alert": alert,
                "notification_type": "DEMO_NOTIFICATION",
                "masked_recipient": "a***********@example.invalid",
                "recipient_hash": hashlib.sha256(recipient.encode()).hexdigest(),
                "sent_at": timezone.now(),
                "result": NotificationRecord.SENT,
                "safe_error_code": "",
                "attempts": 1,
                "backend": "demo-console",
                "external_id": "demo-no-external-delivery",
            },
        )
