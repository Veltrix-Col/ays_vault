from django.core.management.base import BaseCommand, CommandError
from cotizacion_colectivos.services.renewals import diagnose_renewal_source, process_renewal_cycles


class Command(BaseCommand):
    help = "Procesa envíos programados de renovación Colectivo de forma idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--diagnose", action="store_true", help="Muestra conteos de lectura Zoho sin sincronizar ni enviar.")

    def handle(self, *args, **options):
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit debe ser mayor que cero.")
        if options["dry_run"]:
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
        result = process_renewal_cycles(limit=options["limit"], dry_run=options["dry_run"])
        self.stdout.write(f"Renovaciones: procesadas={result['processed']} enviadas={result['sent']} errores={result['errors']}")
        if options["dry_run"]:
            self.stdout.write("Dry-run: no se generaron links ni se enviaron correos.")
