from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from cotizacion_colectivos.excepciones_facturacion import (
    BillingException,
    BillingExceptionsOperationalError,
    find_multiple_findings,
    get_billing_exceptions,
    sample_exceptions_per_type,
)


class Command(BaseCommand):
    help = "Calcula Excepciones de Facturación directamente desde Zoho CRM (READ-only)."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True, choices=("sandbox", "production"))
        parser.add_argument("--as-of", required=True)
        parser.add_argument("--allow-production-read", action="store_true")
        parser.add_argument("--sample-limit", type=int, default=5)
        parser.add_argument("--sample-per-type", type=int)
        parser.add_argument("--show-multi-findings", action="store_true")
        parser.add_argument("--multi-findings-limit", type=int, default=10)

    def _write_exception(self, item: BillingException, *, prefix: str = "") -> None:
        values = (
            ("exception_key", item.exception_key),
            ("exception_type", item.exception_type.value),
            ("source", item.source.value),
            ("rule_code", item.rule_code),
            ("policy_id", item.policy_id),
            ("policy_number", item.policy_number),
            ("operation_id", item.operation_id),
            ("operation_name", item.operation_name),
            ("installment_number", item.installment_number),
            ("relevant_date", item.relevant_date),
            ("billing_date", item.billing_date),
            ("client_name", item.client_name),
            ("insurer", item.insurer),
            ("branch", item.branch),
            ("source_reference", item.source_reference),
            ("reason", item.reason),
        )
        for name, value in values:
            display_value = value if value not in (None, "") else "-"
            self.stdout.write(f"{prefix}{name}: {display_value}")
        self.stdout.write(f"{prefix}context:")
        for name, value in item.context:
            if any(
                sensitive in name.casefold()
                for sensitive in ("token", "secret", "password", "credential")
            ):
                continue
            self.stdout.write(f"{prefix}  {name}: {value or '-'}")

    def handle(self, *args, **options):
        try:
            as_of = date.fromisoformat(str(options["as_of"]))
        except ValueError as exc:
            raise CommandError("--as-of debe tener formato YYYY-MM-DD.") from exc
        sample_limit = options["sample_limit"]
        if sample_limit < 0 or sample_limit > 20:
            raise CommandError("--sample-limit debe estar entre 0 y 20.")
        sample_per_type = options["sample_per_type"]
        if sample_per_type is not None and not 0 <= sample_per_type <= 20:
            raise CommandError("--sample-per-type debe estar entre 0 y 20.")
        multi_findings_limit = options["multi_findings_limit"]
        if not 0 <= multi_findings_limit <= 50:
            raise CommandError("--multi-findings-limit debe estar entre 0 y 50.")
        try:
            result = get_billing_exceptions(
                profile=options["profile"],
                as_of=as_of,
                allow_production_read=options["allow_production_read"],
                progress=self.stdout.write,
            )
        except BillingExceptionsOperationalError as exc:
            raise CommandError(
                f"Excepciones de Facturación falló en {exc.stage} "
                f"({exc.code}): {exc}"
            ) from exc

        self.stdout.write("Excepciones de Facturación (Zoho CRM READ-only)")
        self.stdout.write(f"Profile: {result.profile}")
        self.stdout.write(f"As of: {result.as_of.isoformat()}")
        self.stdout.write(f"Total exceptions: {len(result.exceptions)}")
        self.stdout.write(f"Revision rows: {result.revision_rows_count}")
        self.stdout.write(
            f"Cobros faltantes results: {result.cobros_faltantes_results_count}"
        )
        self.stdout.write(f"Revision exceptions: {result.revision_count}")
        self.stdout.write(f"Missing charges: {result.missing_charges_count}")
        self.stdout.write("Volumes:")
        for name, value in vars(result.volumes).items():
            self.stdout.write(f"- {name}: {value}")
        self.stdout.write("Exception types:")
        for name, value in result.exception_counts:
            self.stdout.write(f"- {name}: {value}")
        if result.revision_diagnostics:
            self.stdout.write("Revision diagnostics:")
            for name, value in result.revision_diagnostics:
                self.stdout.write(f"- {name}: {value}")
        if result.revision_audit:
            self.stdout.write("Revision audit:")
            for name, value in result.revision_audit:
                self.stdout.write(f"- {name}: {value}")
        if result.cobros_faltantes_anomalies:
            self.stdout.write("Cobros faltantes anomalies:")
            for name, value in result.cobros_faltantes_anomalies:
                self.stdout.write(f"- {name}: {value}")
        if sample_per_type is not None:
            sample = sample_exceptions_per_type(
                result.exceptions, limit=sample_per_type
            )
            self.stdout.write(
                f"Stratified sample (max {sample_per_type} per exception type):"
            )
            for item in sample:
                self.stdout.write("-")
                self._write_exception(item, prefix="  ")
        elif sample_limit:
            self.stdout.write(f"Sample (max {sample_limit}):")
            for item in result.exceptions[:sample_limit]:
                self.stdout.write(
                    f"- {item.exception_type.value} "
                    f"policy={item.policy_number} "
                    f"operation={item.operation_name or '-'} "
                    f"date={item.relevant_date or '-'}"
                )
        if options["show_multi_findings"]:
            groups = find_multiple_findings(result.exceptions)
            shown_groups = groups[:multi_findings_limit]
            self.stdout.write(
                "Multiple findings: "
                f"{len(groups)} group(s), showing {len(shown_groups)}"
            )
            for group in shown_groups:
                self.stdout.write(
                    f"- {group.subject_type}={group.subject_id} "
                    f"policy={group.policy_number} "
                    f"findings={len(group.exceptions)}"
                )
                for item in group.exceptions:
                    self.stdout.write("  -")
                    self._write_exception(item, prefix="    ")
