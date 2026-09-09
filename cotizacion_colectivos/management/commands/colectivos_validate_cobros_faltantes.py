from __future__ import annotations

from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.block2.cobros_faltantes import (
    OUTPUT_COLUMNS,
    build_comparison,
    calculate_from_records,
    fetch_operation_records,
    fetch_policy_records,
    read_baseline,
    select_legacy_candidate_records,
    write_comparison_csv,
)


class Command(BaseCommand):
    help = "Valida en modo READ-only el informe legacy de cobros faltantes contra Zoho CRM Production."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True)
        parser.add_argument("--baseline", required=True)
        parser.add_argument("--as-of", required=True)
        parser.add_argument("--allow-production-read", action="store_true")
        parser.add_argument("--allow-differences", action="store_true")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != "production":
            raise CommandError("Este validador admite exclusivamente --profile production.")
        if not options["allow_production_read"]:
            raise CommandError("Debe confirmar la lectura con --allow-production-read.")
        if self._production_write_enabled():
            raise CommandError(
                "ZOHO_PRODUCTION_WRITE_ENABLED debe permanecer en false para ejecutar este validador."
            )
        try:
            as_of = date.fromisoformat(str(options["as_of"]))
        except ValueError as exc:
            raise CommandError("--as-of debe tener formato YYYY-MM-DD.") from exc

        baseline_path = Path(options["baseline"])
        output_path = Path(options["output"]) if options.get("output") else None
        if output_path and output_path.resolve() == baseline_path.resolve():
            raise CommandError("--output no puede sobrescribir el archivo baseline.")
        try:
            baseline = read_baseline(baseline_path)
        except (OSError, ValueError) as exc:
            raise CommandError(f"No fue posible leer el baseline: {exc}") from exc
        if not baseline:
            raise CommandError("El baseline no contiene filas para validar.")

        try:
            zoho = get_zoho(profile="production")
            organization = zoho.organization.get()
            if (
                str(getattr(zoho, "profile", "")).strip().lower() != "production"
                or str(getattr(organization, "environment", "")).strip().lower() != "production"
            ):
                raise CommandError("Zoho no confirmo el entorno Production solicitado.")
            policies = fetch_policy_records(zoho, (row.policy_number for row in baseline))
            selected, _duplicates = select_legacy_candidate_records(policies, as_of=as_of)
            operations = fetch_operation_records(
                zoho,
                (str(policy.get("id") or "") for policy in selected.values()),
            )
        except CommandError:
            raise
        except ValueError as exc:
            raise CommandError(f"Zoho devolvio una respuesta invalida: {exc}") from exc
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible completar la lectura READ-only ({getattr(exc, 'category', 'unknown')})."
            ) from exc

        crm_results, duplicates = calculate_from_records(policies, operations, as_of=as_of)
        rows, summary = build_comparison(
            baseline,
            crm_results,
            duplicate_policy_records=duplicates,
        )
        self._write_table(rows)
        self.stdout.write("")
        self.stdout.write(f"Baseline rows: {summary.baseline_rows}")
        self.stdout.write(f"Matched: {summary.matched}")
        self.stdout.write(f"Different: {summary.different}")
        self.stdout.write(f"Missing in CRM: {summary.missing_in_crm}")
        self.stdout.write(f"Duplicate policy records: {summary.duplicate_policy_records}")
        self.stdout.write(f"Policies with anomalies: {summary.policies_with_anomalies}")

        if output_path:
            try:
                write_comparison_csv(output_path, rows)
            except OSError as exc:
                raise CommandError(f"No fue posible guardar --output: {exc}") from exc
            self.stdout.write(f"CSV guardado en: {output_path}")

        if (summary.different or summary.missing_in_crm) and not options["allow_differences"]:
            raise CommandError(
                "La validacion encontro diferencias contra Analytics; use --allow-differences solo para auditoria."
            )

    @staticmethod
    def _production_write_enabled() -> bool:
        value = getattr(settings, "ZOHO_PRODUCTION_WRITE_ENABLED", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _write_table(self, rows):
        widths = {
            column: max(len(column), *(len(self._display(row[column])) for row in rows))
            for column in OUTPUT_COLUMNS
        }
        self.stdout.write(" | ".join(column.ljust(widths[column]) for column in OUTPUT_COLUMNS))
        self.stdout.write("-+-".join("-" * widths[column] for column in OUTPUT_COLUMNS))
        for row in rows:
            self.stdout.write(
                " | ".join(self._display(row[column]).ljust(widths[column]) for column in OUTPUT_COLUMNS)
            )

    @staticmethod
    def _display(value):
        if isinstance(value, bool):
            return "YES" if value else "NO"
        return str(value)
