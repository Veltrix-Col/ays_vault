from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from cotizacion_colectivos.models import RespuestaSolicitudColectivo
from cotizacion_colectivos.services.novelty_response_email import send_novelty_response_email


class Command(BaseCommand):
    help = "Envía un correo de prueba de respuesta de Novedades sin modificar la solicitud."

    def add_arguments(self, parser):
        parser.add_argument("--request-id", required=True, help="ID local o public_id de la solicitud.")
        parser.add_argument("--to", required=True, help="Destinatario explícito del correo de prueba.")

    def handle(self, *args, **options):
        recipient = str(options["to"] or "").strip()
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise CommandError("El destinatario no es un correo válido.") from exc
        value = str(options["request_id"] or "").strip()
        response = RespuestaSolicitudColectivo.objects.select_related("request").filter(request__public_id=value).order_by("-version").first()
        if response is None and value.isdigit():
            response = RespuestaSolicitudColectivo.objects.select_related("request").filter(request_id=int(value)).order_by("-version").first()
        if response is None:
            raise CommandError("No existe una respuesta de Novedades para la solicitud indicada.")
        try:
            record = send_novelty_response_email(response=response, recipient=recipient, test=True)
        except Exception as exc:
            raise CommandError("No fue posible enviar el correo de prueba.") from exc
        if record is None or getattr(record, "result", "") != "SENT":
            raise CommandError("No fue posible enviar el correo de prueba.")
        self.stdout.write(self.style.SUCCESS("Correo de prueba enviado."))
        self.stdout.write(f"Solicitud: {response.request.public_id}")
        self.stdout.write(f"Destinatario: {recipient}")
        self.stdout.write(f"Asunto: Nueva respuesta de Novedades · {response.request.client_label} · Póliza {response.request.masked_policy_reference}")
