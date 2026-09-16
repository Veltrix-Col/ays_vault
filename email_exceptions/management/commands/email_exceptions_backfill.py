from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from email_exceptions.backfill import run_backfill
from email_exceptions.database_safety import validate_isolated_database as _validate_isolated_database


def validate_isolated_database(alias):
    try:
        _validate_isolated_database(alias)
    except ValueError as exc:
        if str(exc) == "BACKFILL_DATABASE_ALIAS_UNKNOWN":
            raise CommandError(f"Alias de base de datos inexistente: {alias}") from exc
        raise CommandError(str(exc)) from exc


class Command(BaseCommand):
    help = "Backfill controlado de correos históricos usando el flujo productivo."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--output", default=str(Path(settings.BASE_DIR) / "reports" / "email_exceptions_backfill.xlsx"))
        parser.add_argument("--database", default="default")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true", help="Ejecuta y revierte todo al final (modo predeterminado).")
        parser.add_argument("--persist", action="store_true", help="Persiste por mensaje; requiere un alias temporal explícito.")

    def handle(self, *args, **options):
        dataset = Path(options["dataset"])
        if not dataset.is_file():
            raise CommandError(f"Dataset no encontrado: {dataset}")
        if options["limit"] is not None and options["limit"] < 0:
            raise CommandError("--limit debe ser >= 0")
        if options["offset"] < 0:
            raise CommandError("--offset debe ser >= 0")
        if options["dry_run"] and options["persist"]:
            raise CommandError("--dry-run y --persist son mutuamente excluyentes")
        persist = bool(options["persist"])
        alias = options["database"]
        if alias not in settings.DATABASES:
            raise CommandError(f"Alias de base de datos inexistente: {alias}")
        if persist and alias == "default":
            raise CommandError("BACKFILL_DATABASE_MUST_BE_ISOLATED")
        if persist:
            validate_isolated_database(alias)
        result = run_backfill(
            dataset, Path(options["output"]), database=alias,
            limit=options["limit"], offset=options["offset"],
            dry_run=not persist,
        )
        summary = result["summary"]
        self.stdout.write(self.style.SUCCESS(
            f"Backfill {'dry-run' if not persist else 'persistente'} completado: "
            f"{summary['MESSAGES_PROCESSED']} procesados, "
            f"{summary['CASES_CREATED']} casos nuevos, "
            f"{summary['EMAIL_EXCEPTIONS_CREATED']} excepciones nuevas; "
            f"salida={result['output']}"
        ))
