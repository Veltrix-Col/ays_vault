from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from cotizacion_colectivos.excepciones_facturacion.persistence import (
    BillingRefreshAlreadyRunning,
    queue_billing_exceptions_refresh,
)


class Command(BaseCommand):
    help = (
        "Encola el refresh diario de Excepciones de Facturación para que lo "
        "procese el worker Production READ-only."
    )

    def handle(self, *args, **options):
        as_of = timezone.localdate()
        try:
            run = queue_billing_exceptions_refresh(
                as_of=as_of,
                requested_by=None,
            )
        except BillingRefreshAlreadyRunning:
            self.stdout.write(
                self.style.WARNING(
                    "Ya existe una actualización pendiente o en curso. "
                    "No se creó una nueva."
                )
            )
            self.stdout.write("Production WRITE: 0")
            return

        self.stdout.write(
            self.style.SUCCESS(
                "Actualización de Excepciones de Facturación encolada."
            )
        )
        self.stdout.write(f"Run: {run.pk}")
        self.stdout.write(f"Fecha: {run.as_of.isoformat()}")
        self.stdout.write(f"Profile: {run.profile}")
        self.stdout.write(f"Estado: {run.status}")
        self.stdout.write("Production WRITE: 0")
