from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.block2.cobros_faltantes import _lookup_id
from cotizacion_colectivos.block2.revision_facturacion import (
    CRMReadError,
    compare_with_baseline,
    fetch_contact_records,
    fetch_insured_records,
    fetch_note_records,
    fetch_operation_records,
    fetch_policy_records,
    format_read_error,
    read_baseline,
    reconstruct_revision_facturacion,
    select_relevant_operation_ids_for_notes,
    select_latest_candidate_policies,
    write_differences_csv,
)


SAFETY_FLAGS = (
    "ZOHO_PRODUCTION_WRITE_ENABLED",
    "COLECTIVOS_TASK_PUBLISH_ENABLED",
    "COLECTIVOS_CONTACT_PUBLISH_ENABLED",
    "COLECTIVOS_RISK_PUBLISH_ENABLED",
    "COLECTIVOS_SUBRISK_PUBLISH_ENABLED",
    "COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED",
    "COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED",
)


class Command(BaseCommand):
    help = "Valida por CRM READ-only el informe legacy Revision_Facturacion_Filtro_Mes_Actual."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True)
        parser.add_argument("--baseline", required=True)
        parser.add_argument("--as-of", required=True)
        parser.add_argument("--allow-production-read", action="store_true")
        parser.add_argument("--allow-differences", action="store_true")
        parser.add_argument("--output-csv", "--output", dest="output_csv")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != "production":
            raise CommandError("Este validador admite exclusivamente --profile production.")
        if not options["allow_production_read"]:
            raise CommandError("Debe confirmar la lectura con --allow-production-read.")
        enabled = [flag for flag in SAFETY_FLAGS if self._enabled(flag)]
        if enabled:
            raise CommandError(
                "Todos los guards WRITE/PUBLISH deben permanecer en false. "
                f"Guard(s) habilitado(s): {', '.join(enabled)}."
            )
        try:
            as_of = date.fromisoformat(str(options["as_of"]))
        except ValueError as exc:
            raise CommandError("--as-of debe tener formato YYYY-MM-DD.") from exc

        baseline_path = Path(options["baseline"])
        output_path = Path(options["output_csv"]) if options.get("output_csv") else None
        if output_path and output_path.resolve() == baseline_path.resolve():
            raise CommandError("--output-csv no puede sobrescribir el baseline.")
        try:
            baseline = read_baseline(baseline_path)
        except (OSError, ValueError) as exc:
            raise CommandError(f"No fue posible leer el baseline: {exc}") from exc
        if not baseline:
            raise CommandError("El baseline no contiene filas para validar.")

        read_stage = "organization"
        try:
            zoho = get_zoho(profile="production")
            organization = zoho.organization.get()
            if (
                str(getattr(zoho, "profile", "")).strip().lower() != "production"
                or str(getattr(organization, "environment", "")).strip().lower() != "production"
            ):
                raise CommandError("Zoho no confirmo el entorno Production solicitado.")

            policy_numbers = [row["Póliza"] for row in baseline]
            read_stage = "Polizas"
            self.stdout.write(
                f"[READ] Polizas: iniciando para {len(set(policy_numbers))} pólizas baseline..."
            )
            policies = fetch_policy_records(
                zoho, policy_numbers, progress=self.stdout.write,
            )
            self.stdout.write(f"[READ] Polizas: OK, {len(policies)} registros")

            selected, _duplicates = select_latest_candidate_policies(policies)
            policy_ids = sorted(
                {
                    str(policy.get("id") or "")
                    for policy in selected.values()
                    if str(policy.get("id") or "")
                }
            )
            self.stdout.write(
                f"[READ] Polizas: {len(selected)} elegibles, "
                f"{len(policy_ids)} policy IDs necesarios"
            )

            read_stage = "Opeeraciones"
            self.stdout.write(
                f"[READ] Opeeraciones: iniciando para {len(policy_ids)} policy IDs..."
            )
            operations = fetch_operation_records(
                zoho, policy_ids, progress=self.stdout.write,
            )
            self.stdout.write(
                f"[READ] Opeeraciones: OK, {len(operations)} registros"
            )

            read_stage = "Riesgos1"
            self.stdout.write(
                f"[READ] Riesgos1: iniciando para {len(policy_ids)} policy IDs..."
            )
            insured = fetch_insured_records(
                zoho, policy_ids, progress=self.stdout.write,
            )
            self.stdout.write(f"[READ] Riesgos1: OK, {len(insured)} registros")

            operation_ids = select_relevant_operation_ids_for_notes(
                selected.values(), operations, as_of=as_of,
            )
            read_stage = "Notes"
            self.stdout.write(
                f"[READ] Notes: iniciando para {len(operation_ids)} operation IDs..."
            )
            notes = fetch_note_records(
                zoho, operation_ids, progress=self.stdout.write,
            )
            self.stdout.write(f"[READ] Notes: OK, {len(notes)} registros")

            contact_ids = sorted(
                {
                    _lookup_id(policy.get("Tomador_principal1"))
                    for policy in selected.values()
                    if _lookup_id(policy.get("Tomador_principal1"))
                }
            )
            read_stage = "Contacts"
            self.stdout.write(
                f"[READ] Contacts: iniciando para {len(contact_ids)} contact IDs..."
            )
            contacts = fetch_contact_records(
                zoho, contact_ids, progress=self.stdout.write,
            )
            self.stdout.write(f"[READ] Contacts: OK, {len(contacts)} registros")
        except CommandError:
            raise
        except CRMReadError as exc:
            raise CommandError(
                "No fue posible completar la lectura READ-only: "
                f"{format_read_error(exc)}."
            ) from exc
        except ValueError as exc:
            raise CommandError(
                f"Zoho devolvio una respuesta invalida en stage={read_stage}: {exc}"
            ) from exc
        except ZohoError as exc:
            raise CommandError(
                "No fue posible completar la lectura READ-only: "
                f"stage={read_stage} {format_read_error(exc)}."
            ) from exc

        reconstructed = reconstruct_revision_facturacion(
            policies, operations, insured, contacts, notes, as_of=as_of,
        )
        comparison = compare_with_baseline(baseline, reconstructed.rows)
        summary = comparison.summary
        self.stdout.write("Revision_Facturacion_Filtro_Mes_Actual (CRM READ-only)")
        self.stdout.write(f"Baseline rows: {summary.baseline_rows}")
        self.stdout.write(f"CRM rows: {summary.crm_rows}")
        self.stdout.write(f"Matched rows: {summary.matched_rows}")
        self.stdout.write(f"Different rows: {summary.different_rows}")
        self.stdout.write(f"Missing in CRM: {summary.missing_in_crm}")
        self.stdout.write(f"Extra in CRM: {summary.extra_in_crm}")
        self.stdout.write(f"Duplicate keys: {summary.duplicate_keys}")
        self.stdout.write(f"Duplicate policy records: {reconstructed.duplicate_policy_records}")
        if reconstructed.diagnostic_counts:
            self.stdout.write("Diagnostics:")
            for code, count in reconstructed.diagnostic_counts.items():
                self.stdout.write(f"- {code}: {count}")
        if reconstructed.audit_counts:
            self.stdout.write("Audit stages:")
            for code, count in reconstructed.audit_counts.items():
                self.stdout.write(f"- {code}: {count}")
        if comparison.differences:
            self.stdout.write("Differences by columns:")
            statuses = Counter(row["STATUS"] for row in comparison.differences)
            for status, count in sorted(statuses.items()):
                self.stdout.write(f"- {status}: {count}")
            for row in comparison.differences[:20]:
                self.stdout.write(
                    f"- {row['STATUS']} key={row['KEY']} columns={row['DIFFERING_COLUMNS'] or '(row)'}"
                )
            if len(comparison.differences) > 20:
                self.stdout.write(f"- ... {len(comparison.differences) - 20} diferencia(s) adicional(es).")

        if output_path:
            try:
                write_differences_csv(output_path, comparison.differences)
            except OSError as exc:
                raise CommandError(f"No fue posible guardar --output-csv: {exc}") from exc
            self.stdout.write(f"CSV de diferencias guardado en: {output_path}")

        has_differences = bool(
            summary.different_rows or summary.missing_in_crm or summary.extra_in_crm
        )
        if has_differences and not options["allow_differences"]:
            raise CommandError(
                "La validacion encontro diferencias contra Analytics; use --allow-differences solo para auditoria."
            )

    @staticmethod
    def _enabled(name: str) -> bool:
        value = getattr(settings, name, False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
