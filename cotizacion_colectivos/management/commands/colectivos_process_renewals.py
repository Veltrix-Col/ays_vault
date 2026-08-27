from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from cotizacion_colectivos.models import RenovacionColectiva
from cotizacion_colectivos.services.renewals import diagnose_renewal_source, process_renewal_cycles


class Command(BaseCommand):
    help = "Procesa envíos programados de renovación Colectivo de forma idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--diagnose", action="store_true", help="Muestra conteos de lectura Zoho sin sincronizar ni enviar.")
        parser.add_argument("--cycle-id", type=int, help="Procesa únicamente este ciclo local.")
        parser.add_argument("--force-due", action="store_true", help="Permite probar un ciclo futuro sólo en Sandbox y con --cycle-id.")

    def handle(self, *args, **options):
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit debe ser mayor que cero.")
        if options["cycle_id"] is not None and options["cycle_id"] < 1:
            raise CommandError("--cycle-id debe ser mayor que cero.")
        if options["force_due"] and options["cycle_id"] is None:
            raise CommandError("--force-due requiere --cycle-id.")
        if options["force_due"] and str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).strip().casefold() != "sandbox":
            raise CommandError("--force-due sólo está permitido con ZOHO_ACTIVE_PROFILE=sandbox.")
        target = None
        if options["cycle_id"] is not None:
            try:
                target = RenovacionColectiva.objects.get(pk=options["cycle_id"])
            except RenovacionColectiva.DoesNotExist as exc:
                raise CommandError("El cycle-id indicado no existe.") from exc
            self.stdout.write(
                f"Ciclo seleccionado: id={target.pk} póliza={target.masked_policy} "
                f"periodo={target.monthly_period} destinatario={target.recipient_email or 'sin correo'} "
                f"dry_run={'sí' if options['dry_run'] else 'no'}"
            )
        if options["dry_run"] and options["cycle_id"] is None:
            try:
                diagnostic = diagnose_renewal_source(limit=options["limit"] or 20)
            except Exception as exc:
                raise CommandError("No fue posible leer Polizas para el diagnóstico.") from exc
            self.stdout.write(
                "Lectura Zoho: total={total_records} colectivo={collective_records} "
                "fecha_valida={valid_expiry_records} proximos_30={next_30_days} "
                "proximos_ventana={next_window_days}".format(**diagnostic)
            )
            for example in diagnostic["examples"]:
                self.stdout.write(
                    "Ejemplo seguro: póliza={policy} ramo={branch} "
                    "vencimiento={expiry} cliente={client}".format(**example)
                )
        result = process_renewal_cycles(
            limit=options["limit"], dry_run=options["dry_run"],
            cycle_id=options["cycle_id"], force_due=options["force_due"],
        )
        if result.get("disabled"):
            self.stdout.write("Automatización mensual de Colectivos desactivada. No se generaron enlaces ni se enviaron correos.")
            return
        self.stdout.write(f"Renovaciones: procesadas={result['processed']} enviadas={result['sent']} recordatorios={result.get('reminders', 0)} sin_email={result.get('no_email', 0)} errores={result['errors']}")
        if options["dry_run"]:
            self.stdout.write("Dry-run: no se generaron links ni se enviaron correos.")
