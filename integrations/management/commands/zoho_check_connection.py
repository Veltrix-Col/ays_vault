from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.constants import API_VERSION
from integrations.zoho.exceptions import ZohoError
from integrations.zoho.settings import ZohoSettings


class Command(BaseCommand):
    help = "Comprueba la conexión de solo lectura con Zoho CRM."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default="")

    def handle(self, *args, **options):
        profile = options["profile"].strip() or None
        config = ZohoSettings.from_django(profile)
        try:
            zoho = get_zoho(profile=profile)
            organization = zoho.organization.get()
        except ZohoError as exc:
            raise CommandError(
                f"Conexión Zoho: ERROR ({exc.category}). {exc}"
            ) from exc
        self.stdout.write(self.style.SUCCESS("Conexión Zoho: OK"))
        self.stdout.write(f"Perfil: {config.profile}")
        self.stdout.write(f"Entorno esperado: {config.environment}")
        self.stdout.write(f"Backend: {zoho.backend_name.upper()}")
        self.stdout.write(
            f"Organización: {organization.company_name or 'Sin nombre configurado'}"
        )
        self.stdout.write(f"API: CRM {API_VERSION.upper()}")
        self.stdout.write(f"Data Center: {organization.data_center}")
        self.stdout.write(
            f"Entorno reportado: {organization.environment or 'no informado'}"
        )
        self.stdout.write(f"Resource path: {config.sdk_resource_path}")
        self.stdout.write("Modo: solo lectura")
