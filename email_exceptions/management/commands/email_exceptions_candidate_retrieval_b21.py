from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from email_exceptions.candidate_retrieval_b21_replay import run_candidate_retrieval_b21_replay


class Command(BaseCommand):
    help = "Shadow replay B.2.1 con evidencia separada de conflictos, sin persistencia."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--output", default="reports/email_exceptions_candidate_retrieval_b21.xlsx")

    def handle(self, *args, **options):
        dataset = Path(options["dataset"])
        if not dataset.is_file():
            raise CommandError(f"Dataset no encontrado: {dataset}")

        def report_progress(progress):
            self.stdout.write(
                f"Procesados: {progress['processed']}/{progress['total']} | "
                f"Casos B.2.1: {progress['simulated_cases']} | "
                f"Candidatos decisión promedio: {progress['candidate_average']:.2f} | "
                f"Candidatos decisión máximo: {progress['candidate_max']} | "
                f"Tiempo: {progress['elapsed_seconds']:.1f}s | "
                f"Velocidad: {progress['messages_per_second']:.2f} msg/s"
            )

        result = run_candidate_retrieval_b21_replay(
            dataset,
            Path(options["output"]),
            progress_callback=report_progress,
        )
        metrics = result["metrics"]
        self.stdout.write(self.style.SUCCESS(
            f"Replay B.2.1 completado: {metrics['TOTAL_MESSAGES']} mensajes; "
            f"B1={metrics['B1_CANDIDATE_TOTAL']} candidatos; "
            f"B2={metrics['B2_CANDIDATE_TOTAL']} candidatos; "
            f"B2.1={metrics['B21_DECISION_CANDIDATE_TOTAL']} candidatos de decisión; "
            f"conflictos B1/B2.1={metrics['B1_CONFLICTS']}/{metrics['B21_CONFLICTS']}; "
            f"salida={result['output_path']}"
        ))
