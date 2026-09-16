from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_exceptions.correlation_replay import run_correlation_replay


class Command(BaseCommand):
    help = "Replay histórico B.1 del motor de correlación, sin persistencia."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--output", default=str(Path(settings.BASE_DIR) / "reports" / "email_exceptions_correlation_replay_b1.xlsx"))

    def handle(self, *args, **options):
        dataset = Path(options["dataset"])
        if not dataset.is_file():
            raise CommandError(f"Dataset no encontrado: {dataset}")

        def report_progress(progress):
            self.stdout.write(
                f"Procesados: {progress['processed']}/{progress['total']} | "
                f"Casos simulados: {progress['simulated_cases']} | "
                f"Candidatos promedio: {progress['candidate_average']:.2f} | "
                f"Candidatos máximo: {progress['candidate_max']} | "
                f"Tiempo transcurrido: {progress['elapsed_seconds']:.1f}s | "
                f"Velocidad: {progress['messages_per_second']:.2f} msg/s"
            )

        result = run_correlation_replay(dataset, Path(options["output"]), progress_callback=report_progress)
        metrics = result["metrics"]
        self.stdout.write(self.style.SUCCESS(
            f"Replay B.1 completado: {metrics['TOTAL_MESSAGES']} mensajes; "
            f"{metrics['SIMULATED_CASES']} casos simulados; "
            f"correlación={metrics['CORRELATION_SECONDS']:.2f}s; "
            f"Excel={metrics['EXCEL_WRITE_SECONDS']:.2f}s; salida={result['output_path']}"
        ))
