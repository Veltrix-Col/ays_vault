from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from integrations.zoho.exceptions import ZohoTimeoutError

from cotizacion_colectivos.excepciones_facturacion import (
    BillingExceptionsOperationalError,
    BillingExceptionSource,
    BillingExceptionType,
    get_billing_exceptions,
)
AS_OF = date(2026, 9, 7)
POLICY_ID = "4933790000200703052"
OPERATION_ID = "4933790000300000001"
SHARED_PUBLISH_FLAGS = (
    "COLECTIVOS_TASK_PUBLISH_ENABLED",
    "COLECTIVOS_CONTACT_PUBLISH_ENABLED",
    "COLECTIVOS_RISK_PUBLISH_ENABLED",
    "COLECTIVOS_SUBRISK_PUBLISH_ENABLED",
    "COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED",
    "COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED",
)
SHARED_PUBLISH_ENTRY_POINTS = (
    "cotizacion_colectivos.services.task_publisher.get_task_publisher",
    "cotizacion_colectivos.services.task_publisher.publish_task_outbox",
    "cotizacion_colectivos.services.person_contract.get_contacts_publisher",
    "cotizacion_colectivos.services.risk_sandbox.create_sandbox_risk",
    "cotizacion_colectivos.services.subrisk_sandbox.create_subrisk_sandbox",
    "cotizacion_colectivos.services.subrisk_sandbox.create_mobility_subrisk_sandbox",
    "cotizacion_colectivos.services.individual_attachment_publisher.publish_attachment",
    "cotizacion_colectivos.services.invitation_attachment_publisher.prepare_invitation_attachment",
)


def policy(number="POL-1", record_id=POLICY_ID, **overrides):
    value = {
        "id": record_id,
        "Name": number,
        "Key": f"KEY-{number}",
        "L_nea_de_negocio": "Colectivos",
        "Estado_de_la_p_liza": "Vigente",
        "Modo_de_pago": "Fraccionado",
        "Cambio_de_intermediario": "No",
        "Ramo": "Salud colectivo",
        "P_liza_Fecha_de_inicio_vigencia": "2026-01-01",
        "P_liza_Fecha_fin_de_la_vigencia": "2026-12-31",
        "Dia_facturaci_n": 1,
        "Tipo_de_facturaci_n": "Mes corriente",
        "Fecha_2": "2026-09-01",
    }
    value.update(overrides)
    return value


def operation(**overrides):
    value = {
        "id": OPERATION_ID,
        "P_liza": {"id": POLICY_ID},
        "Name": "Cobro 2",
        "Observaciones": "Pendiente",
        "Certificado_Fecha_de_inicio_de_vigencia": "2026-09-01",
        "Fecha_de_expedici_n_de_p_liza": None,
        "N_mero_de_certificado": None,
        "Total_a_pagar_en_OP": 0,
    }
    value.update(overrides)
    return value


class FakeCoql:
    def __init__(self, *, policies=(), operations=(), risks=(), contacts=(), notes=(), policy_pages=None, fail_module=None):
        self.calls = []
        self.routes = {
            "Polizas": list(policies),
            "Opeeraciones": list(operations),
            "Riesgos1": list(risks),
            "Contacts": list(contacts),
            "Notes": list(notes),
        }
        self.policy_pages = policy_pages
        self.fail_module = fail_module

    def execute(self, query, *, offset, limit):
        module = next(
            name for name in self.routes if f" from {name} " in f" {query} "
        )
        self.calls.append({"module": module, "query": query, "offset": offset, "limit": limit})
        if module == self.fail_module:
            raise ZohoTimeoutError(
                "timeout", operation="coql", backend="sdk", module=module
            )
        if module == "Polizas" and self.policy_pages is not None:
            records, more = self.policy_pages[offset]
            return SimpleNamespace(records=tuple(records), more_records=more)
        return SimpleNamespace(records=tuple(self.routes[module]), more_records=False)


class FakeZoho:
    def __init__(self, *, profile="sandbox", **coql_options):
        self.profile = profile
        self.organization = SimpleNamespace(
            get=lambda: SimpleNamespace(environment=profile)
        )
        self.coql = FakeCoql(**coql_options)
        self.records = SimpleNamespace(
            create=Mock(side_effect=AssertionError("CREATE no permitido")),
            update=Mock(side_effect=AssertionError("UPDATE no permitido")),
            delete=Mock(side_effect=AssertionError("DELETE no permitido")),
        )
        self.attachments = SimpleNamespace(
            upload=Mock(side_effect=AssertionError("attachments no permitido")),
            delete=Mock(side_effect=AssertionError("attachments no permitido")),
        )


class BillingExceptionsApplicationTests(SimpleTestCase):
    def execute(self, zoho=None, **overrides):
        options = {
            "profile": "sandbox",
            "as_of": AS_OF,
            "zoho": zoho or FakeZoho(policies=[policy()]),
        }
        options.update(overrides)
        return get_billing_exceptions(**options)

    def test_executes_without_baseline_or_excel_access(self):
        with patch("openpyxl.load_workbook", side_effect=AssertionError("Excel no permitido")):
            result = self.execute()
        self.assertEqual(len(result.exceptions), 2)

    def test_orchestrates_both_engines_and_combines_sources(self):
        result = self.execute()
        self.assertEqual({item.source for item in result.exceptions}, set(BillingExceptionSource))
        self.assertEqual(
            {item.exception_type for item in result.exceptions},
            {BillingExceptionType.MISSING_OPERATION, BillingExceptionType.MISSING_CHARGE},
        )

    def test_uses_explicit_as_of(self):
        earlier = self.execute(as_of=date(2026, 8, 1))
        current = self.execute(as_of=AS_OF)
        self.assertEqual(earlier.as_of, date(2026, 8, 1))
        self.assertEqual(current.as_of, AS_OF)
        self.assertNotEqual(earlier.exceptions, current.exceptions)

    def test_result_contains_counts_diagnostics_and_volumes(self):
        result = self.execute()
        self.assertEqual(result.revision_count, 1)
        self.assertEqual(result.missing_charges_count, 1)
        self.assertEqual(result.revision_rows_count, 1)
        self.assertEqual(result.cobros_faltantes_results_count, 1)
        self.assertEqual(dict(result.source_counts), {"cobros_faltantes": 1, "revision_facturacion": 1})
        self.assertIn(("MISSING_OPERATION", 1), result.revision_diagnostics)
        self.assertEqual(result.volumes.policy_records, 1)
        self.assertEqual(result.volumes.revision_policies, 1)

    def test_order_is_deterministic_when_zoho_order_changes(self):
        first_policy = policy("POL-1", "4933790000200703051")
        second_policy = policy("POL-2", "4933790000200703052")
        first = self.execute(FakeZoho(policies=[first_policy, second_policy]))
        second = self.execute(FakeZoho(policies=[second_policy, first_policy]))
        self.assertEqual(first.exceptions, second.exceptions)
        self.assertEqual(
            [item.exception_key for item in first.exceptions],
            sorted(item.exception_key for item in first.exceptions),
        )

    def test_does_not_mutate_records_returned_by_zoho(self):
        policies = [policy()]
        operations = [operation()]
        before = deepcopy((policies, operations))
        self.execute(FakeZoho(policies=policies, operations=operations))
        self.assertEqual((policies, operations), before)

    def test_policy_universe_is_a_single_paginated_collective_query(self):
        second = policy("POL-2", "4933790000200703053")
        zoho = FakeZoho(
            policy_pages={0: ([policy()], True), 200: ([second], False)}
        )
        result = self.execute(zoho)
        policy_calls = [call for call in zoho.coql.calls if call["module"] == "Polizas"]
        self.assertEqual([call["offset"] for call in policy_calls], [0, 200])
        self.assertTrue(all(call["limit"] == 200 for call in policy_calls))
        self.assertIn("where L_nea_de_negocio = 'Colectivos'", policy_calls[0]["query"])
        self.assertEqual(result.volumes.policy_records, 2)

    def test_dependent_reads_are_batched_not_one_query_per_policy(self):
        policies = [
            policy(
                f"POL-{number}",
                f"49337900002{number:08d}",
            )
            for number in range(101)
        ]
        zoho = FakeZoho(policies=policies)
        result = self.execute(zoho)
        modules = [call["module"] for call in zoho.coql.calls]
        self.assertEqual(modules.count("Polizas"), 1)
        self.assertEqual(modules.count("Opeeraciones"), 2)
        self.assertEqual(modules.count("Riesgos1"), 2)
        self.assertNotIn("Contacts", modules)
        self.assertNotIn("Notes", modules)
        self.assertEqual(result.volumes.policy_records, 101)

    def test_every_crm_query_is_select_only(self):
        zoho = FakeZoho(policies=[policy()], operations=[operation()])
        self.execute(zoho)
        self.assertTrue(zoho.coql.calls)
        self.assertTrue(
            all(call["query"].casefold().startswith("select ") for call in zoho.coql.calls)
        )

    def test_contacts_and_notes_are_grouped_instead_of_read_per_policy(self):
        first_contact = "4933790000000000001"
        second_contact = "4933790000000000002"
        second_policy_id = "4933790000200703053"
        policies = [
            policy(Tomador_principal1={"id": first_contact}),
            policy(
                "POL-2",
                second_policy_id,
                Tomador_principal1={"id": second_contact},
            ),
        ]
        operations = [
            operation(),
            operation(
                id="4933790000300000002",
                P_liza={"id": second_policy_id},
            ),
        ]
        contacts = [
            {"id": first_contact, "Full_Name": "Cliente Uno"},
            {"id": second_contact, "Full_Name": "Cliente Dos"},
        ]
        zoho = FakeZoho(
            policies=policies,
            operations=operations,
            contacts=contacts,
        )
        self.execute(zoho)
        modules = [call["module"] for call in zoho.coql.calls]
        self.assertEqual(modules.count("Contacts"), 1)
        self.assertEqual(modules.count("Notes"), 1)

    def test_empty_universe_returns_empty_complete_result(self):
        zoho = FakeZoho()
        result = self.execute(zoho)
        self.assertEqual(result.exceptions, ())
        self.assertEqual(result.exception_counts, ())
        self.assertEqual(result.revision_rows_count, 0)
        self.assertEqual(result.cobros_faltantes_results_count, 0)
        self.assertEqual(result.volumes.policy_records, 0)
        self.assertEqual([call["module"] for call in zoho.coql.calls], ["Polizas"])

    def test_read_failure_exposes_stage_and_category(self):
        zoho = FakeZoho(policies=[policy()], fail_module="Riesgos1")
        with self.assertRaises(BillingExceptionsOperationalError) as raised:
            self.execute(zoho)
        self.assertEqual(raised.exception.code, "zoho_read_error")
        self.assertEqual(raised.exception.stage, "Riesgos1")
        self.assertIn("category=timeout", str(raised.exception))

    def test_reconstruction_failure_is_not_returned_as_empty_result(self):
        with patch(
            "cotizacion_colectivos.excepciones_facturacion.application."
            "reconstruct_revision_facturacion",
            side_effect=ValueError("contrato inválido"),
        ):
            with self.assertRaises(BillingExceptionsOperationalError) as raised:
                self.execute()
        self.assertEqual(raised.exception.code, "reconstruction_error")
        self.assertEqual(raised.exception.stage, "legacy_engines")

    def test_adaptation_failure_is_distinguished_from_reconstruction(self):
        with patch(
            "cotizacion_colectivos.excepciones_facturacion.application."
            "combine_billing_exceptions",
            side_effect=ValueError("identidad insuficiente"),
        ):
            with self.assertRaises(BillingExceptionsOperationalError) as raised:
                self.execute()
        self.assertEqual(raised.exception.code, "adaptation_error")
        self.assertEqual(raised.exception.stage, "billing_exceptions")

    def test_resolves_requested_profile_through_existing_facade_factory(self):
        zoho = FakeZoho(policies=[policy()])
        with patch(
            "cotizacion_colectivos.excepciones_facturacion.application.get_zoho",
            return_value=zoho,
        ) as factory:
            result = get_billing_exceptions(profile="sandbox", as_of=AS_OF)
        factory.assert_called_once_with(profile="sandbox")
        self.assertEqual(result.profile, "sandbox")

    def test_invalid_profile_fails_before_resolving_zoho(self):
        with self.assertRaises(BillingExceptionsOperationalError) as raised:
            get_billing_exceptions(profile="invalid", as_of=AS_OF)
        self.assertEqual(raised.exception.code, "invalid_profile")

    def test_as_of_must_be_explicit_date(self):
        with self.assertRaises(BillingExceptionsOperationalError) as raised:
            get_billing_exceptions(profile="sandbox", as_of="2026-09-07")
        self.assertEqual(raised.exception.code, "invalid_as_of")

    def test_production_requires_explicit_read_confirmation(self):
        with self.assertRaises(BillingExceptionsOperationalError) as raised:
            get_billing_exceptions(profile="production", as_of=AS_OF)
        self.assertEqual(raised.exception.code, "production_confirmation_required")

    @override_settings(ZOHO_PRODUCTION_WRITE_ENABLED=True)
    def test_production_read_is_independent_from_shared_write_guard(self):
        result = self.execute(
            FakeZoho(profile="production", policies=[policy()]),
            profile="production",
            allow_production_read=True,
        )
        self.assertEqual(result.profile, "production")

    @override_settings(**{flag: True for flag in SHARED_PUBLISH_FLAGS})
    def test_production_read_is_independent_from_shared_publish_guards(self):
        result = self.execute(
            FakeZoho(profile="production", policies=[policy()]),
            profile="production",
            allow_production_read=True,
        )
        self.assertEqual(result.profile, "production")

    @override_settings(
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
        **{flag: True for flag in SHARED_PUBLISH_FLAGS},
    )
    def test_production_refresh_path_never_writes_or_invokes_publishers(self):
        zoho = FakeZoho(profile="production", policies=[policy()])
        with ExitStack() as stack:
            publishers = [
                stack.enter_context(
                    patch(path, side_effect=AssertionError("publisher no permitido"))
                )
                for path in SHARED_PUBLISH_ENTRY_POINTS
            ]
            result = self.execute(
                zoho,
                profile="production",
                allow_production_read=True,
            )

        self.assertEqual(result.profile, "production")
        zoho.records.create.assert_not_called()
        zoho.records.update.assert_not_called()
        zoho.records.delete.assert_not_called()
        zoho.attachments.upload.assert_not_called()
        zoho.attachments.delete.assert_not_called()
        for publisher in publishers:
            publisher.assert_not_called()

    def test_facade_environment_must_match_requested_profile(self):
        zoho = FakeZoho(profile="sandbox", policies=[policy()])
        with self.assertRaises(BillingExceptionsOperationalError) as raised:
            get_billing_exceptions(
                profile="production",
                as_of=AS_OF,
                allow_production_read=True,
                zoho=zoho,
            )
        self.assertEqual(raised.exception.code, "environment_mismatch")

    def test_incomplete_operation_produces_multiple_exception_types(self):
        result = self.execute(FakeZoho(policies=[policy()], operations=[operation()]))
        self.assertEqual(
            {item.exception_type for item in result.exceptions},
            {
                BillingExceptionType.MISSING_CERTIFICATE,
                BillingExceptionType.MISSING_EXPEDITION_DATE,
                BillingExceptionType.ZERO_OPERATION_TOTAL,
            },
        )

    def test_prorroga_is_compatible_with_operational_flow(self):
        prorroga = operation(
            Name="Modificación",
            Observaciones="Prórroga septiembre",
            Certificado_Fecha_de_inicio_de_vigencia="2026-09-20",
        )
        result = self.execute(
            FakeZoho(
                policies=[policy(Fecha_2=None, Dia_facturaci_n=20)],
                operations=[prorroga],
            )
        )
        self.assertEqual(result.missing_charges_count, 0)
        self.assertTrue(result.exceptions)
        self.assertTrue(all(item.operation_id == OPERATION_ID for item in result.exceptions))

    def test_missing_charge_remains_one_aggregate_exception_per_policy(self):
        item = policy(Fecha_2="2026-07-01", Fecha_3="2026-08-01", Fecha_4="2026-09-01")
        result = self.execute(FakeZoho(policies=[item]))
        charges = [
            exception for exception in result.exceptions
            if exception.exception_type is BillingExceptionType.MISSING_CHARGE
        ]
        self.assertEqual(len(charges), 1)
        self.assertIsNone(charges[0].installment_number)

    def test_revision_and_cobros_keep_their_distinct_eligibility(self):
        not_fractioned = policy(Modo_de_pago="Otro")
        result = self.execute(FakeZoho(policies=[not_fractioned]))
        self.assertEqual(result.volumes.revision_policies, 0)
        self.assertEqual(result.volumes.cobros_faltantes_policies, 1)
