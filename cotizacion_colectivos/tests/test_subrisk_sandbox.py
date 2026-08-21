from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings

from cotizacion_colectivos.services.subrisk_sandbox import (
    SUBRISK_CONFIRMATION,
    SubriskPublicationUncertain,
    build_subrisk_payload,
    build_mobility_subrisk_payload,
    create_subrisk_sandbox,
    resolve_mobility_subrisk_relation,
    resolve_policy_by_number,
    validate_lookup_id,
)
from integrations.zoho.exceptions import ZohoSDKError


def _payload():
    return build_subrisk_payload(
        policy_id="4991513000000000001",
        affiliate_contact_id="4991513000270118607",
        insured_contact_id="4991513000270118608",
        subrisk_name="TEST-SALUD-001",
        entry_date="2026-08-21",
        plan="Evoluciona",
    )


class SubriskPayloadTests(SimpleTestCase):
    def test_mobility_builder_contains_vehicle_lookup_and_closed_branch(self):
        payload = build_mobility_subrisk_payload(
            policy_id="4991513000000000001",
            affiliate_contact_id="4991513000270981010",
            insured_contact_id="4991513000270981010",
            risk_id="4991513000270982008",
            subrisk_name="TEST-MOV-SUBRISK-001",
            entry_date="2026-08-21",
        )
        self.assertEqual(payload["Riesgo"], {"id": "4991513000270982008"})
        self.assertEqual(payload["Ramo"], "Movilidad colectivo")
        self.assertEqual(payload["Contacto_facturaci_n_dividida_colectivas"], payload["Asegurado"])
        self.assertNotIn("Plan", payload)

    def test_policy_resolver_requires_exactly_one_name_match(self):
        zoho = SimpleNamespace(search=Mock())
        zoho.search.by_criteria.return_value = SimpleNamespace(records=(
            {"id": "4991513000000000001", "Name": "040006434488"},
        ))
        result = resolve_policy_by_number(policy_number="040006434488", zoho=zoho)
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["record_id"], "4991513000000000001")
        self.assertIn("Name:equals:040006434488", zoho.search.by_criteria.call_args.kwargs["criteria"])

    def test_mobility_relation_is_not_found_already_exists_or_ambiguous(self):
        zoho = SimpleNamespace(search=Mock())
        kwargs = dict(policy_id="4991513000000000001", risk_id="4991513000270982008",
                      affiliate_contact_id="4991513000270981010", insured_contact_id="4991513000270981010", zoho=zoho)
        zoho.search.by_criteria.return_value = SimpleNamespace(records=())
        self.assertEqual(resolve_mobility_subrisk_relation(**kwargs)["status"], "NOT_FOUND")
        record = {"id": "4991513000270999999", "P_liza": {"id": kwargs["policy_id"]},
                  "Riesgo": {"id": kwargs["risk_id"]},
                  "Contacto_facturaci_n_dividida_colectivas": {"id": kwargs["affiliate_contact_id"]},
                  "Asegurado": {"id": kwargs["insured_contact_id"]}}
        zoho.search.by_criteria.return_value = SimpleNamespace(records=(record,))
        self.assertEqual(resolve_mobility_subrisk_relation(**kwargs)["status"], "ALREADY_EXISTS")
        zoho.search.by_criteria.return_value = SimpleNamespace(records=(record, dict(record, id="4991513000270999998")))
        self.assertEqual(resolve_mobility_subrisk_relation(**kwargs)["status"], "AMBIGUOUS")

    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True)
    def test_mobility_command_only_resolves_one_record_without_write(self):
        zoho = SimpleNamespace(search=Mock(), records=Mock())

        def search(**kwargs):
            module = kwargs["module"]
            criteria = kwargs["criteria"]
            if module == "Polizas":
                return SimpleNamespace(records=({"id": "4991513000000000001", "Name": "040006434488"},))
            if module == "Contacts":
                return SimpleNamespace(records=({"id": criteria.split(":")[-1], "Full_Name": "synthetic"},))
            if module == "Riesgos":
                risk_id = criteria.split(":")[-1].rstrip(")")
                plate = {"4991513000270982008": "VTX001", "4991513000270990022": "VTX002",
                         "4991513000270981017": "VTX004", "4991513000270978012": "VTX005"}[risk_id]
                return SimpleNamespace(records=({"id": risk_id, "Name": "synthetic", "Placa_del_vehiculo": plate,
                                                 "Tipo_de_riesgo": "Vehículos"},))
            return SimpleNamespace(records=())

        zoho.search.by_criteria.side_effect = search
        out = __import__("io").StringIO()
        with patch("cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.get_zoho", return_value=zoho):
            call_command("colectivos_seed_mobility_subrisks", stdout=out, only="TEST-MOV-SUBRISK-001")
        self.assertIn('"planned": 1', out.getvalue())
        self.assertIn('"writes": 0', out.getvalue())
        zoho.records.create.assert_not_called()

    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True)
    def test_mobility_command_rejects_unknown_only_alias(self):
        with self.assertRaises(CommandError):
            call_command("colectivos_seed_mobility_subrisks", only="NOT-A-SCENARIO")

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_SANDBOX_WRITE_ENABLED=True,
        COLECTIVOS_MOBILITY_SUBRISK_SEED_ENABLED=True,
        COLECTIVOS_MOBILITY_SUBRISK_SEED_CONFIRMATION="SANDBOX_MOBILITY_SUBRISK_SEED",
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
        COLECTIVOS_SUBRISK_WRITE_CONFIRMATION=SUBRISK_CONFIRMATION,
    )
    def test_mobility_command_blocks_reconciled_relations_without_create(self):
        prepared = ({
            "alias": "TEST-MOV-SUBRISK-001", "policy_id": "***0001",
            "affiliate_id": "***1010", "insured_id": "***1010", "risk_id": "***2008",
            "result": "ALREADY_EXISTS", "payload": {"Name": "TEST-MOV-SUBRISK-001"},
        },)
        for result in ("ALREADY_EXISTS", "AMBIGUOUS"):
            with self.subTest(result=result), patch(
                "cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.get_zoho",
                return_value=SimpleNamespace(),
            ), patch(
                "cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.Command._resolve",
                return_value=({"status": "FOUND", "record_id": "***0001"},
                              (dict(prepared[0], result=result),)),
            ), patch(
                "cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.create_mobility_subrisk_sandbox",
            ) as create:
                out = __import__("io").StringIO()
                call_command(
                    "colectivos_seed_mobility_subrisks", stdout=out,
                    only="TEST-MOV-SUBRISK-001",
                    confirm="SANDBOX_MOBILITY_SUBRISK_SEED",
                )
                self.assertIn(f'"result": "{result}"', out.getvalue())
                self.assertIn('"writes": 0', out.getvalue())
                create.assert_not_called()

    def test_closed_payload_contains_only_confirmed_fields_and_lookups(self):
        payload = _payload()
        self.assertEqual(payload["P_liza"], {"id": "4991513000000000001"})
        self.assertEqual(payload["Asegurado"], {"id": "4991513000270118608"})
        self.assertEqual(payload["Contacto_facturaci_n_dividida_colectivas"], {"id": "4991513000270118607"})
        self.assertEqual(set(payload), {
            "Name", "P_liza", "Contacto_facturaci_n_dividida_colectivas", "Asegurado",
            "Ramo", "Estado", "Parentesco", "Fecha_ingreso_riesgo", "Plan",
        })

    def test_lookup_validation_rejects_unsafe_values_and_shapes(self):
        for value in (None, "", "abc", "123", True, [], {"id": "1"}):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_lookup_id(value, "P_liza")
        with self.assertRaises(ValidationError):
            build_subrisk_payload(
                policy_id={"id": "4991513000000000001", "name": "bad"},
                affiliate_contact_id="4991513000270118607",
                insured_contact_id="4991513000270118608",
                subrisk_name="TEST", entry_date="2026-08-21",
            )

    def test_sdk_facade_calls_create_update_and_upsert_without_changing_lookup_dicts(self):
        records = Mock()
        payload = _payload()
        records.create.return_value = "created"
        records.update.return_value = "updated"
        records.upsert.return_value = "upserted"
        records.create(module="Riesgos1", records=(payload,))
        records.update(module="Riesgos1", records=(dict(payload, id="4991513000000000099"),))
        records.upsert(module="Riesgos1", records=(payload,))
        self.assertEqual(records.create.call_args.kwargs["records"][0]["Asegurado"], payload["Asegurado"])
        self.assertEqual(records.update.call_args.kwargs["records"][0]["P_liza"], payload["P_liza"])
        self.assertEqual(records.upsert.call_args.kwargs["records"][0]["Contacto_facturaci_n_dividida_colectivas"], payload["Contacto_facturaci_n_dividida_colectivas"])


class SubriskSandboxWriteTests(SimpleTestCase):
    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_SANDBOX_WRITE_ENABLED=True,
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
        COLECTIVOS_SUBRISK_WRITE_CONFIRMATION=SUBRISK_CONFIRMATION,
    )
    def test_create_is_one_call_and_uses_sdk_facade_shape(self):
        zoho = SimpleNamespace(records=Mock())
        item = SimpleNamespace(succeeded=True, record_id="4991513000000000199", code="SUCCESS")
        zoho.records.create.return_value = SimpleNamespace(records=(item,))
        result = create_subrisk_sandbox(_payload(), profile="sandbox", confirmation=SUBRISK_CONFIRMATION, zoho=zoho)
        self.assertTrue(result["succeeded"])
        zoho.records.create.assert_called_once()
        self.assertEqual(zoho.records.create.call_args.kwargs["records"][0]["P_liza"], _payload()["P_liza"])

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_SANDBOX_WRITE_ENABLED=True,
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
        COLECTIVOS_SUBRISK_WRITE_CONFIRMATION=SUBRISK_CONFIRMATION,
    )
    def test_uncertain_result_is_not_retried_or_fallbacked(self):
        zoho = SimpleNamespace(records=Mock())
        error = __import__("integrations.zoho.exceptions", fromlist=["ZohoAPIError"]).ZohoAPIError("boom")
        error.request_sent = True
        error.status_code = 500
        zoho.records.create.side_effect = error
        with self.assertRaises(SubriskPublicationUncertain):
            create_subrisk_sandbox(_payload(), profile="sandbox", confirmation=SUBRISK_CONFIRMATION, zoho=zoho)
        zoho.records.create.assert_called_once()

    def test_command_defaults_to_dry_run(self):
        out = __import__("io").StringIO()
        call_command(
            "colectivos_test_subrisk_write", stdout=out,
            **{
                "policy_id": "4991513000000000001",
                "affiliate_contact_id": "4991513000270118607",
                "insured_contact_id": "4991513000270118608",
                "subrisk_name": "TEST-SALUD-001", "entry_date": "2026-08-21",
            },
        )
        self.assertIn('"mode": "dry-run"', out.getvalue())
        self.assertIn("NO se realizó WRITE", out.getvalue())

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_SANDBOX_WRITE_ENABLED=True,
        COLECTIVOS_MOBILITY_SUBRISK_SEED_ENABLED=True,
        COLECTIVOS_MOBILITY_SUBRISK_SEED_CONFIRMATION="SANDBOX_MOBILITY_SUBRISK_SEED",
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
    )
    def test_mobility_command_surfaces_sanitized_sdk_error_without_nameerror(self):
        """A failed mocked publish is reported, never retried or leaked raw data."""
        error = ZohoSDKError(
            "raw SDK message with sensitive payload",
            status_code=400,
            backend="sdk",
            operation="records.create",
            module="Riesgos1",
            sdk_exception_class="SDKException",
            sdk_code="API_EXCEPTION",
            zoho_code="INVALID_DATA",
            zoho_status="ERROR",
            detail_keys=("api_name", "message"),
            detail_field="Asegurado",
            detail_accepted_type="Lookup",
            detail_given_type="dict",
            detail_class="SDKException",
            detail_index=0,
            request_sent=True,
        )
        prepared = ({
            "alias": "TEST-MOV-SUBRISK-001",
            "policy_id": "***0001",
            "affiliate_id": "***1010",
            "insured_id": "***1010",
            "risk_id": "***2008",
            "result": "NOT_FOUND",
            "payload": {"Name": "TEST-MOV-SUBRISK-001"},
        },)
        with patch(
            "cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.get_zoho",
            return_value=SimpleNamespace(),
        ), patch(
            "cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.Command._resolve",
            return_value=({"status": "FOUND", "record_id": "***0001"}, prepared),
        ), patch(
            "cotizacion_colectivos.management.commands.colectivos_seed_mobility_subrisks.create_mobility_subrisk_sandbox",
            side_effect=error,
        ):
            with self.assertRaises(CommandError) as raised:
                call_command(
                    "colectivos_seed_mobility_subrisks",
                    confirm="SANDBOX_MOBILITY_SUBRISK_SEED",
                )
        message = str(raised.exception)
        self.assertIn("category=sdk", message)
        self.assertIn("zoho_code=INVALID_DATA", message)
        self.assertIn("field=Asegurado", message)
        self.assertIn("request_sent=true", message)
        self.assertNotIn("raw SDK message", message)
        self.assertNotIn("sensitive payload", message)
