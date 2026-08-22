from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cotizacion_colectivos.services.deadlines import process_deadlines


class Command(BaseCommand):
    help = "Procesa vencimientos y recordatorios de Colectivos de forma idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--now", help="Fecha/hora ISO 8601 para ejecución administrativa reproducible.")

    def handle(self, *args, **options):
        now = None
        if options["now"]:
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now debe ser una fecha/hora ISO 8601 válida.")
            if timezone.is_naive(now):
                now = timezone.make_aware(now)
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit debe ser mayor que cero.")
        result = process_deadlines(now=now, limit=options["limit"], dry_run=options["dry_run"])
        self.stdout.write("Procesamiento de plazos (solo conteos seguros):")
        for key, value in result.safe_dict().items():
            self.stdout.write(f"- {key}: {value}")
        if options["dry_run"]:
            self.stdout.write("Dry-run: no se realizaron cambios ni envíos.")
