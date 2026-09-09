from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from cotizacion_colectivos.excepciones_facturacion.persistence import (
    execute_billing_exceptions_refresh,
    recover_orphaned_billing_refreshes,
)
from cotizacion_colectivos.models import BillingExceptionRefreshRun


class Command(BaseCommand):
    help = (
        "Consume en segundo plano las actualizaciones pendientes de Excepciones "
        "de Facturación usando Production en modo de solo lectura."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true",
            help="Procesa como máximo una actualización pendiente y termina.",
        )
        parser.add_argument(
            "--poll-seconds", type=int, default=5,
            help="Segundos entre consultas cuando no existen pendientes (default: 5).",
        )

    def handle(self, *args, **options):
        once = bool(options["once"])
        poll_seconds = int(options["poll_seconds"])
        if poll_seconds < 1:
            raise CommandError("--poll-seconds debe ser mayor o igual a 1.")

        self.stdout.write("Consumidor de Excepciones de Facturación iniciado. Production WRITE: 0")
        try:
            while True:
                recovered = recover_orphaned_billing_refreshes()
                for run_id in recovered:
                    self.stderr.write(
                        self.style.WARNING(f"Run huérfano {run_id} marcado como fallido.")
                    )

                run_id = BillingExceptionRefreshRun.objects.filter(
                    profile="production",
                    status=BillingExceptionRefreshRun.Status.PENDING,
                ).order_by("requested_at", "pk").values_list("pk", flat=True).first()
                if run_id is None:
                    if once:
                        self.stdout.write("No hay actualizaciones pendientes.")
                        return
                    time.sleep(poll_seconds)
                    continue

                try:
                    run = execute_billing_exceptions_refresh(run_id)
                except ValueError:
                    current_status = BillingExceptionRefreshRun.objects.filter(
                        pk=run_id
                    ).values_list("status", flat=True).first()
                    if current_status in {
                        BillingExceptionRefreshRun.Status.RUNNING,
                        BillingExceptionRefreshRun.Status.SUCCESS,
                    }:
                        self.stdout.write(f"Run {run_id} reclamado por otro consumidor.")
                    elif once:
                        raise CommandError(
                            "No fue posible procesar la actualización; se conservó la última información válida."
                        )
                    else:
                        self.stderr.write(
                            self.style.ERROR(
                                f"Run {run_id} falló; se conservó la última información válida."
                            )
                        )
                except Exception as exc:
                    if once:
                        raise CommandError(
                            "No fue posible procesar la actualización; se conservó la última información válida."
                        ) from exc
                    self.stderr.write(
                        self.style.ERROR(
                            f"Run {run_id} falló; se conservó la última información válida."
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Run {run.pk} completado: "
                            f"{run.counts.get('operational_cases', 0)} casos operativos."
                        )
                    )

                if once:
                    return
        except KeyboardInterrupt:
            self.stdout.write("Consumidor detenido.")
