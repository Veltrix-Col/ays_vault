from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from cotizacion_colectivos.excepciones_facturacion.persistence import (
    BillingRefreshAlreadyRunning,
    execute_billing_exceptions_refresh,
    queue_billing_exceptions_refresh,
)
from cotizacion_colectivos.models import BillingExceptionRefreshRun


class Command(BaseCommand):
    help = "Procesa el snapshot local de Excepciones de Facturación desde Production READ-only."

    def add_arguments(self, parser):
        parser.add_argument("--as-of")
        parser.add_argument("--run-id", type=int)

    def handle(self, *args, **options):
        run_id = options.get("run_id")
        if run_id:
            run = BillingExceptionRefreshRun.objects.filter(pk=run_id).first()
            if run is None:
                raise CommandError("El run solicitado no existe.")
        else:
            raw_date = options.get("as_of")
            if not raw_date:
                raise CommandError("--as-of es obligatorio al crear un refresh.")
            try:
                as_of = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CommandError("--as-of debe tener formato YYYY-MM-DD.") from exc
            try:
                run = queue_billing_exceptions_refresh(as_of=as_of)
            except BillingRefreshAlreadyRunning as exc:
                raise CommandError(str(exc)) from exc
        try:
            run = execute_billing_exceptions_refresh(run.pk)
        except Exception as exc:
            raise CommandError("No fue posible completar el refresh; se conservó el snapshot anterior.") from exc
        self.stdout.write(self.style.SUCCESS("Refresh de Excepciones de Facturación completado."))
        self.stdout.write(f"Run: {run.pk}")
        self.stdout.write(f"Cases: {run.counts.get('operational_cases', 0)}")
        self.stdout.write("Production WRITE: 0")
