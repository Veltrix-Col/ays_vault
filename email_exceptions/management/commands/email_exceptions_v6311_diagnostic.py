from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_exceptions.v6311_diagnostic import run_diagnostic


class Command(BaseCommand):
    help = "Diagnóstico histórico V631.1 sin mutar fuentes ni persistencia."

    def add_arguments(self, parser):
        parser.add_argument("--replay", required=True)
        parser.add_argument("--reference", required=True)
        parser.add_argument("--output", default=str(Path(settings.BASE_DIR) / "reports" / "email_exceptions_v6311_diagnostic.xlsx"))

    def handle(self, *args, **options):
        replay = Path(options["replay"])
        reference = Path(options["reference"])
        if not replay.is_file():
            raise CommandError(f"Replay no encontrado: {replay}")
        if not reference.is_file():
            raise CommandError(f"Referencia no encontrada: {reference}")
        result = run_diagnostic(replay, reference, Path(options["output"]))
        self.stdout.write(self.style.SUCCESS(
            f"Diagnóstico V631.1 completado: {result['total_divergences']} divergencias; "
            f"salida={result['output_path']}"
        ))
