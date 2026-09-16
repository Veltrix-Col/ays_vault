from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_exceptions.historical_replay import run_replay


class Command(BaseCommand):
    help = "Replay histórico read-only del clasificador contra una referencia V631."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--reference", required=True)
        parser.add_argument("--output", default=str(Path(settings.BASE_DIR) / "reports" / "email_exceptions_historical_replay_v631.xlsx"))

    def handle(self, *args, **options):
        dataset = Path(options["dataset"])
        reference = Path(options["reference"])
        if not dataset.is_file():
            raise CommandError(f"Dataset no encontrado: {dataset}")
        if not reference.is_file():
            raise CommandError(f"Referencia no encontrada: {reference}")
        result = run_replay(dataset, reference, Path(options["output"]))
        self.stdout.write(self.style.SUCCESS(
            f"Replay completado: {result['total_messages']} mensajes; "
            f"{len(result['comparisons'])} comparables; salida={result['output_path']}"
        ))
