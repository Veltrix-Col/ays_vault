from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from vault.models import PolicyEvaluationRun
from vault.policy_evaluation import evaluate_security_policies


class Command(BaseCommand):
    help = "Evalua inactividad, vencimientos, MFA, auditoria y salud de configuracion de forma idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Evalua sin crear alertas ni cambiar estados.")

    def handle(self, *args, **options):
        run = PolicyEvaluationRun.objects.create(dry_run=options["dry_run"])
        try:
            checks = evaluate_security_policies(dry_run=options["dry_run"])
            run.status = "SUCCESS"
            run.checks = checks
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "checks", "finished_at"])
            self.stdout.write(self.style.SUCCESS(f"Evaluacion completa: {checks['created']} nueva(s), {checks['existing']} existente(s)."))
        except Exception as exc:
            run.status = "FAILED"
            run.safe_error = exc.__class__.__name__
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "safe_error", "finished_at"])
            raise CommandError("La evaluacion de politicas fallo; consulte el registro seguro.") from exc
