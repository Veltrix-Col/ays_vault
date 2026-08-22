from importlib.metadata import PackageNotFoundError, version

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.constants import API_VERSION
from integrations.zoho.exceptions import ZohoError
from integrations.zoho.settings import ZohoSettings


class Command(BaseCommand):
    help = "Muestra información no sensible del backend Zoho activo."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default="")

    def handle(self, *args, **options):
        profile = options["profile"].strip() or None
        config = ZohoSettings.from_django(profile)
        try:
            sdk_version = version("zohocrmsdk8-0")
        except PackageNotFoundError:
            sdk_version = "no instalado"
        try:
            zoho = get_zoho(profile=profile)
            organization = zoho.organization.get()
        except ZohoError as exc:
            raise CommandError(f"Backend Zoho: ERROR ({exc.category}). {exc}") from exc
        self.stdout.write(f"Backend: {zoho.backend_name.upper()}")
        self.stdout.write(f"Perfil: {config.profile}")
        self.stdout.write(f"Entorno: {config.environment}")
        self.stdout.write("SDK: zohocrmsdk8-0")
        self.stdout.write(f"Versión: {sdk_version}")
        self.stdout.write(f"CRM API: {API_VERSION.upper()}")
        self.stdout.write(f"Organización: {organization.company_name or 'disponible'}")
        self.stdout.write("Modo: solo lectura")
        self.stdout.write(
            f"Resource path: {config.sdk_resource_path or 'pendiente'}"
        )
        self.stdout.write(
            f"Entorno reportado: {organization.environment or 'no informado'}"
        )
