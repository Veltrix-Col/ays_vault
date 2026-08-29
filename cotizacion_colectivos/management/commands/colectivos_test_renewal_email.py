from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.utils import timezone

from cotizacion_colectivos.services.renewals import (
    _renewal_email_settings,
    _send_renewal_email,
    list_collective_renewals,
)


CONFIRMATION = "SANDBOX_RENEWAL_EMAIL_TEST"


class Command(BaseCommand):
    help = "Envía un único correo de prueba del flujo mensual de renovaciones en Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Único destinatario del correo de prueba.")
        parser.add_argument("--policy", required=True, help="Número o ID de la póliza a leer en Sandbox.")
        parser.add_argument("--confirm", required=True)

    def handle(self, *args, **options):
        profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).strip().casefold()
        if profile != "sandbox":
            raise CommandError("Este comando admite exclusivamente ZOHO_ACTIVE_PROFILE=sandbox.")
        if str(options["confirm"] or "").strip() != CONFIRMATION:
            raise CommandError("La confirmación no coincide con la prueba SMTP de Sandbox.")

        recipient = str(options["to"] or "").strip()
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise CommandError("El destinatario no es un correo válido.") from exc

        # Validate renewal SMTP before performing the read, so a missing
        # application password cannot result in a partial attempt.
        try:
            _renewal_email_settings()
        except Exception as exc:
            raise CommandError("El SMTP de renovaciones no está configurado.") from exc

        policy_ref = str(options["policy"] or "").strip()
        if not policy_ref:
            raise CommandError("Debe indicar una póliza de Sandbox.")
        try:
            policies = list_collective_renewals()
        except Exception as exc:
            raise CommandError("No fue posible leer la póliza en Zoho Sandbox.") from exc
        selected = next(
            (item for item in policies if item.policy == policy_ref or item.remote_id == policy_ref),
            None,
        )
        if selected is None:
            raise CommandError("La póliza indicada no es elegible o no fue encontrada en Sandbox.")

        now = timezone.now()
        expires_at = now + timedelta(days=int(getattr(settings, "COLECTIVOS_RENEWAL_LINK_TTL_DAYS", 8)))
        cycle = SimpleNamespace(
            pk=0,
            client_label=selected.client,
            masked_policy=selected.policy,
            monthly_period=selected.monthly_period,
            recipient_email=recipient,
            link_expires_at=expires_at,
        )
        base = str(getattr(settings, "COLECTIVOS_EXTERNAL_BASE_URL", "")).rstrip("/")
        # These paths are intentionally inert and are not mapped to any
        # mutating view. They preserve the real CTA presentation without
        # creating an access, request, OTP or NO_CHANGES response.
        report_url = f"{base}/sandbox-email-test/report/"
        no_changes_url = f"{base}/sandbox-email-test/no-changes/"
        try:
            _send_renewal_email(
                cycle=cycle,
                url=report_url,
                expires_at=expires_at,
                recipient=recipient,
                no_changes_url=no_changes_url,
            )
        except Exception as exc:
            raise CommandError("No fue posible enviar el correo de prueba SMTP.") from exc

        self.stdout.write("Perfil: sandbox")
        self.stdout.write(f"Destinatario: {recipient}")
        self.stdout.write(f"Remitente: {settings.COLECTIVOS_RENEWAL_EMAIL_FROM}")
        self.stdout.write(f"Póliza: {selected.policy}")
        self.stdout.write("Template: renewal_initial.html")
        self.stdout.write(self.style.SUCCESS("Resultado: enviado"))
