from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from integrations.zoho.exceptions import ZohoTimeoutError
from integrations.zoho.schemas import Page

from cotizacion_colectivos.services.common import (
    ColectivosServiceError,
    mask_document,
    sign_record_id,
    unsign_record_id,
    colectivos_zoho,
)
from cotizacion_colectivos.services.entity_detail import EntityDetailService
from cotizacion_colectivos.services.mappings import (
    CONTACT_DETAIL_FIELDS,
    CONTACT_SEARCH_FIELDS,
    INSURED_CONTACT_ROLES,
    INSURED_RELATION_FIELDS,
)
from cotizacion_colectivos.services.search import CompanySearchService, PersonSearchService
from cotizacion_colectivos.services.policies import CONTACT_BATCH_FIELDS, PolicyService


COMPANY = {
    "id": "1234567890123456789",
    "Tipo_de_persona": "Persona jurídica",
    "Tipo_ID": "NIT",
    "N_mero_de_ID": "9001234567",
    "Nombre_comercial": "Empresa de Prueba",
    "Raz_n_social": "Empresa de Prueba SAS",
    "Estado": "Activo",
}
PERSON = {
    "id": "2234567890123456789",
    "Tipo_de_persona": "Persona natural",
    "Tipo_ID": "CC",
    "N_mero_de_ID": "1012345678",
    "First_Name": "Nombre",
    "Last_Name": "Prueba",
    "Full_Name": "Nombre Prueba",
    "Estado": "Activo",
}


class FakeSearch:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def by_criteria(self, **kwargs):
        self.calls.append(kwargs)
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeCoql:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def execute(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeRecords:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def get_by_id(self, **kwargs):
        self.calls.append(kwargs)
        record = self.records.get(kwargs["module"]) if isinstance(self.records, dict) and kwargs["module"] in self.records else self.records
        if isinstance(record, Exception):
            raise record
        return record


class FakeZoho:
    def __init__(self, *, search_pages=(), coql_pages=(), record=None):
        self.search = FakeSearch(search_pages)
        self.coql = FakeCoql(coql_pages)
        self.records = FakeRecords(record)


class SearchServiceTests(SimpleTestCase):
    @patch("cotizacion_colectivos.services.common.get_colectivos_zoho")
    def test_service_factory_uses_central_profile_resolver(self, get_colectivos_zoho):
        sentinel = object()
        get_colectivos_zoho.return_value = sentinel
        self.assertIs(colectivos_zoho(), sentinel)
        get_colectivos_zoho.assert_called_once_with(timings=None)

    def test_company_by_nit_uses_fixed_filters_fields_and_masks_document(self):
        zoho = FakeZoho(search_pages=(Page((COMPANY,)),))
        results = CompanySearchService(zoho).search("9001234567")
        self.assertEqual(results[0].display_name, "Empresa de Prueba")
        self.assertNotIn("9001234567", results[0].masked_document)
        call = zoho.search.calls[0]
        self.assertEqual(call["module"], "Contacts")
        self.assertEqual(call["fields"], CONTACT_SEARCH_FIELDS)
        self.assertEqual(call["limit"], 20)
        self.assertIn("Tipo_de_persona:equals:Persona jurídica", call["criteria"])
        self.assertNotIn("Tipo_ID:equals:NIT", call["criteria"])
        self.assertIn("N_mero_de_ID:equals:9001234567", call["criteria"])
        self.assertEqual(len(zoho.search.calls), 1)
        self.assertEqual(len(results), 1)

    def test_company_numeric_prefix_uses_starts_with_and_deduplicates(self):
        other = {**COMPANY, "id": "1334567890123456789", "N_mero_de_ID": "9009999999"}
        zoho = FakeZoho(search_pages=(Page(()), Page((COMPANY, other))))
        results = CompanySearchService(zoho).search("900")
        self.assertEqual(len(results), 2)
        self.assertIn("N_mero_de_ID:starts_with:900", zoho.search.calls[1]["criteria"])
        self.assertNotIn("Tipo_ID:equals:NIT", zoho.search.calls[1]["criteria"])

    def test_company_by_name_uses_only_confirmed_name_fields(self):
        zoho = FakeZoho(search_pages=(Page((COMPANY,)), Page((COMPANY,)), Page(()), Page(()), Page(()), Page(())))
        CompanySearchService(zoho).search("Empresa")
        exact = zoho.search.calls[0]["criteria"]
        self.assertIn("Tipo_de_persona:equals:Persona jurídica", exact)
        self.assertNotIn("Tipo_ID:equals:NIT", exact)
        self.assertIn("Nombre_comercial:equals:Empresa", exact)
        self.assertIn("Raz_n_social:equals:Empresa", exact)
        self.assertIn("Full_Name:equals:Empresa", exact)
        self.assertEqual(len(zoho.search.calls), 1)
        self.assertTrue(
            all(
                "Tipo_de_persona:equals:Persona jurídica" in call["criteria"]
                and "Tipo_ID:equals:NIT" not in call["criteria"]
                for call in zoho.search.calls
            )
        )
        self.assertEqual(zoho.coql.calls, [])

    def test_company_name_falls_back_to_legal_name(self):
        record = {**COMPANY, "Nombre_comercial": "", "Raz_n_social": "Razón Segura"}
        zoho = FakeZoho(search_pages=(Page(()), Page((record,))))
        self.assertEqual(CompanySearchService(zoho).search("Razón")[0].display_name, "Razón Segura")

    def test_exact_company_name_is_ranked_before_partial_match(self):
        partial = {**COMPANY, "id": "1334567890123456789", "Nombre_comercial": "Empresa Ampliada"}
        exact = {**COMPANY, "id": "1434567890123456789", "Nombre_comercial": "Empresa"}
        zoho = FakeZoho(search_pages=(Page((partial, exact)), Page((partial, exact)), Page(()), Page(()), Page(()), Page(())))
        results = CompanySearchService(zoho).search("Empresa")
        self.assertEqual(results[0].display_name, "Empresa")

    def test_person_by_document_and_name_use_closed_criteria(self):
        zoho = FakeZoho(
            search_pages=(Page((PERSON,)), Page((PERSON,)), Page(()), Page(()), Page(()), Page(()), Page(()), Page(())),
        )
        PersonSearchService(zoho).search("1012345678")
        PersonSearchService(zoho).search("Nombre")
        self.assertIn("Tipo_ID:equals:CC", zoho.search.calls[0]["criteria"])
        self.assertIn("N_mero_de_ID:equals:1012345678", zoho.search.calls[0]["criteria"])
        name_criteria = zoho.search.calls[1]["criteria"]
        self.assertIn("Full_Name:equals:Nombre", name_criteria)
        self.assertIn("First_Name:equals:Nombre", name_criteria)
        self.assertIn("Last_Name:equals:Nombre", name_criteria)
        self.assertEqual(len(zoho.search.calls), 2)
        self.assertEqual(zoho.coql.calls, [])

    def test_zero_and_multiple_results_are_bounded_to_twenty(self):
        many = tuple({**COMPANY, "id": str(10**18 + index)} for index in range(25))
        empty = FakeZoho(search_pages=(Page(()), Page(())))
        populated = FakeZoho(search_pages=(Page(many),))
        self.assertEqual(CompanySearchService(empty).search("Nada"), ())
        self.assertEqual(len(CompanySearchService(populated).search("Empresa")), 20)

    def test_unexpected_segment_is_discarded(self):
        zoho = FakeZoho(search_pages=(Page((PERSON,)), Page(()), Page(()), Page(()), Page(()), Page(())))
        self.assertEqual(CompanySearchService(zoho).search("Empresa"), ())

    def test_company_rejects_populated_non_nit_id_type_locally(self):
        record = {**COMPANY, "Tipo_ID": "CC"}
        zoho = FakeZoho(search_pages=(Page((record,)), Page(())))
        self.assertEqual(CompanySearchService(zoho).search("9001234567"), ())
        self.assertNotIn("Tipo_ID:equals:NIT", zoho.search.calls[0]["criteria"])

    def test_company_accepts_empty_id_type_after_legal_person_validation(self):
        record = {**COMPANY, "Tipo_ID": ""}
        zoho = FakeZoho(search_pages=(Page((record,)),))
        results = CompanySearchService(zoho).search("9001234567")
        self.assertEqual(len(results), 1)
        self.assertEqual(len(zoho.search.calls), 1)

    def test_company_search_logs_metrics_without_document_or_name(self):
        zoho = FakeZoho(search_pages=(Page((COMPANY,)),))
        with self.assertLogs("cotizacion_colectivos", level="INFO") as captured:
            CompanySearchService(zoho).search("9001234567")
        output = " ".join(captured.output)
        self.assertIn("operation=search_api", output)
        self.assertIn("duration_ms=", output)
        self.assertNotIn("9001234567", output)
        self.assertNotIn("Empresa de Prueba", output)

    def test_service_always_builds_configured_facade(self):
        facade = FakeZoho(search_pages=tuple(Page(()) for _ in range(6)))
        with patch("cotizacion_colectivos.services.search.colectivos_zoho", return_value=facade) as factory:
            CompanySearchService().search("Empresa")
        factory.assert_called_once()
        self.assertIn("timings", factory.call_args.kwargs)

    def test_person_exact_document_skips_prefix(self):
        zoho = FakeZoho(search_pages=(Page((PERSON,)), Page(())))
        results = PersonSearchService(zoho).search("1012345678")
        self.assertEqual(len(results), 1)
        self.assertEqual(len(zoho.search.calls), 1)

    def test_text_search_skips_prefix_when_any_exact_match_exists(self):
        zoho = FakeZoho(search_pages=(Page((COMPANY,)), Page(()), Page(())))
        results = CompanySearchService(zoho).search("Empresa")
        self.assertEqual(len(results), 1)
        self.assertEqual(len(zoho.search.calls), 1)
        self.assertTrue(all("starts_with" not in call["criteria"] for call in zoho.search.calls))

    def test_timeout_is_translated_without_internal_detail(self):
        zoho = FakeZoho(search_pages=(ZohoTimeoutError("endpoint privado"),))
        with self.assertRaisesMessage(ColectivosServiceError, "tardó demasiado") as raised:
            CompanySearchService(zoho).search("Empresa")
        self.assertNotIn("endpoint", str(raised.exception))

    def test_name_service_rejects_user_wildcards_before_coql(self):
        zoho = FakeZoho()
        with self.assertRaisesMessage(ColectivosServiceError, "no es válido"):
            CompanySearchService(zoho).search("Acme_%")
        self.assertEqual(zoho.search.calls, [])
        self.assertEqual(zoho.coql.calls, [])

    def test_document_mask_never_returns_complete_value(self):
        self.assertEqual(mask_document("1234567890"), "•••••••890")
        self.assertNotEqual(mask_document("1"), "1")


class DetailServiceTests(SimpleTestCase):
    @override_settings(ZOHO_ACTIVE_PROFILE="production")
    @patch("cotizacion_colectivos.services.entity_detail.colectivos_zoho")
    def test_detail_builds_the_same_configured_facade(self, colectivos_zoho):
        selected = FakeZoho()
        colectivos_zoho.return_value = selected
        service = EntityDetailService()
        self.assertIs(service.zoho, selected)
        self.assertEqual(service.profile, "production")
        colectivos_zoho.assert_called_once_with()

    def test_contact_detail_uses_closed_search_fallback_when_sdk_record_fails(self):
        zoho = FakeZoho(
            search_pages=(Page((COMPANY,)), Page(()), Page(())),
            record=ZohoTimeoutError("detalle interno"),
        )
        detail = EntityDetailService(zoho).company(sign_record_id(COMPANY["id"]))
        self.assertEqual(detail.display_name, COMPANY["Nombre_comercial"])
        fallback = zoho.search.calls[0]
        self.assertEqual(fallback["module"], "Contacts")
        self.assertEqual(fallback["criteria"], f"(id:equals:{COMPANY['id']})")
        self.assertEqual(fallback["fields"], CONTACT_DETAIL_FIELDS)
        self.assertEqual(fallback["limit"], 1)

    def test_company_detail_uses_fixed_contact_module_and_confirmed_relations(self):
        relation_record = {
            "id": "3234567890123456789",
            "P_liza": {"id": "4234567890123456789", "name": "POL-123456"},
            "Asegurado": {"id": COMPANY["id"], "name": "Empresa de Prueba"},
            "Riesgo": {"id": "5234567890123456789", "name": "RIESGO-9876"},
            "Estado": "Activo", "Ramo": "Vida", "Aseguradora": "Aseguradora",
            "Name": "ASEG-123", "Fecha_ingreso_riesgo": "2026-01-01",
        }
        records = {
            "Contacts": COMPANY,
            "Polizas": {"id": "4234567890123456789", "Name": "POL-123456", "Estado_de_la_p_liza": "Vigente", "Ramo": "Vida", "Aseguradora1": "Aseguradora", "Layout": {"name": "Colectivos"}},
            "Riesgos": {"id": "5234567890123456789", "Name": "RIESGO-9876", "Tipo_de_riesgo": "Vida"},
        }
        zoho = FakeZoho(search_pages=(Page((relation_record,)), Page(())), record=records)
        detail = EntityDetailService(zoho).company(sign_record_id(COMPANY["id"]))
        self.assertEqual(detail.display_name, "Empresa de Prueba")
        self.assertEqual(len(detail.insured), 1)
        self.assertEqual(len(detail.policies), 1)
        self.assertEqual(len(detail.risks), 1)
        self.assertNotIn("POL-123456", detail.policies[0].masked_reference)
        self.assertEqual(detail.policies[0].layout_category, "collective")
        self.assertEqual(detail.insured[0].entry_date, "2026-01-01")
        self.assertEqual(zoho.records.calls[0]["module"], "Contacts")
        self.assertEqual(zoho.records.calls[0]["fields"], CONTACT_DETAIL_FIELDS)
        self.assertEqual(zoho.search.calls[0]["module"], "Riesgos1")
        self.assertEqual(zoho.search.calls[0]["fields"], INSURED_RELATION_FIELDS)

    def test_direct_tomador_policies_are_separate_and_partial(self):
        direct = {"id": "4234567890123456789", "Name": "POL-123456", "Tomador_principal1": {"id": COMPANY["id"]}, "Layout": {"name": "Colectivos"}}
        zoho = FakeZoho(search_pages=(Page(()), Page((direct,))), record={"Contacts": COMPANY})
        detail = EntityDetailService(zoho).company(sign_record_id(COMPANY["id"]))
        self.assertEqual(detail.policies, ())
        self.assertEqual(len(detail.direct_policies), 1)
        self.assertEqual(detail.direct_policies[0].relationship_source, "direct_tomador")
        self.assertEqual(detail.direct_policies[0].relationship_confidence, "partial")

    def test_secondary_relation_error_does_not_block_basic_detail(self):
        zoho = FakeZoho(search_pages=(ZohoTimeoutError("private"), Page(())), record={"Contacts": COMPANY})
        detail = EntityDetailService(zoho).company(sign_record_id(COMPANY["id"]))
        self.assertEqual(detail.display_name, "Empresa de Prueba")
        self.assertTrue(detail.unavailable_relations)

    def test_person_detail_rejects_company_to_prevent_cross_type_idor(self):
        zoho = FakeZoho(record=COMPANY)
        with self.assertRaisesMessage(ColectivosServiceError, "no existe"):
            EntityDetailService(zoho).person(sign_record_id(COMPANY["id"]))
        self.assertEqual(zoho.search.calls, [])

    def test_invalid_or_tampered_token_is_rejected_before_api(self):
        zoho = FakeZoho(record=PERSON)
        with self.assertRaisesMessage(ColectivosServiceError, "no es válido"):
            EntityDetailService(zoho).person("token-manipulado")
        self.assertEqual(zoho.records.calls, [])

    def test_signed_token_is_opaque_and_round_trips(self):
        token = sign_record_id(PERSON["id"])
        self.assertNotIn(PERSON["id"], token)
        self.assertEqual(unsign_record_id(token), PERSON["id"])

    @patch("cotizacion_colectivos.services.common.signing.loads")
    def test_expired_token_is_rejected(self, loads):
        from django.core import signing

        loads.side_effect = signing.SignatureExpired("expired")
        with self.assertRaisesMessage(ColectivosServiceError, "no es válido"):
            unsign_record_id("expired-token")

    def test_person_company_lookup_is_only_displayed_when_present(self):
        record = {**PERSON, "Empresa": {"id": COMPANY["id"], "name": "Empresa relacionada"}}
        zoho = FakeZoho(search_pages=(Page(()), Page(()), Page(()), Page(())), record={"Contacts": record})
        detail = EntityDetailService(zoho).person(sign_record_id(PERSON["id"]))
        self.assertEqual(detail.company_name, "Empresa relacionada")

    def test_unknown_layout_is_tolerated(self):
        direct = {"id": "4234567890123456789", "Name": "POL-123456", "Tomador_principal1": {"id": COMPANY["id"]}}
        zoho = FakeZoho(search_pages=(Page(()), Page((direct,))), record={"Contacts": COMPANY})
        detail = EntityDetailService(zoho).company(sign_record_id(COMPANY["id"]))
        self.assertEqual(detail.direct_policies[0].layout_category, "unknown")

    def test_relations_use_only_ids_and_never_join_by_name(self):
        zoho = FakeZoho(search_pages=(Page(()), Page(()), Page(()), Page(())), record={"Contacts": PERSON})
        EntityDetailService(zoho).person(sign_record_id(PERSON["id"]))
        self.assertEqual(
            [call["criteria"] for call in zoho.search.calls[:3]],
            [f"({field}:equals:{PERSON['id']})" for field, _label in INSURED_CONTACT_ROLES],
        )
        self.assertEqual(zoho.search.calls[3]["criteria"], f"(Tomador_principal1:equals:{PERSON['id']})")
        self.assertNotIn(PERSON["Full_Name"], str(zoho.search.calls))

    def test_person_detail_resolves_insured_affiliate_and_beneficiary_roles(self):
        relations = []
        for index, (field, _label) in enumerate(INSURED_CONTACT_ROLES):
            relations.append({
                "id": str(3234567890123456789 + index),
                field: {"id": PERSON["id"], "name": "No usar para unir"},
                "Estado": "Activo",
                "Ramo": "Salud colectivo",
                "Name": f"REL-{index}",
            })
        zoho = FakeZoho(
            search_pages=(*(Page((relation,)) for relation in relations), Page(())),
            record={"Contacts": PERSON},
        )
        detail = EntityDetailService(zoho).person(sign_record_id(PERSON["id"]))
        self.assertEqual({item.role for item in detail.insured}, {"Asegurado", "Afiliado", "Beneficiario"})
        self.assertEqual(len(detail.insured), 3)
        self.assertNotIn("No usar para unir", repr(detail))

    def test_relation_fields_use_confirmed_affiliate_api_name(self):
        self.assertIn("Contacto_facturaci_n_dividida_colectivas", INSURED_RELATION_FIELDS)
        self.assertNotIn("Afiliado", INSURED_RELATION_FIELDS)

    def test_policy_group_restores_affiliate_contact_and_relationship(self):
        policy_id = "4234567890123456789"
        relation = {
            "id": "3234567890123456789",
            "Contacto_facturaci_n_dividida_colectivas": {"id": PERSON["id"], "name": "No usar"},
            "Estado": "Activo",
            "Ramo": "Salud colectivo",
            "Parentesco": "Afiliado",
            "Plan": "Plan vigente",
        }
        contact = {**PERSON, "Email": "persona@example.test", "Mobile": "3000000000"}
        zoho = FakeZoho(
            search_pages=(
                Page(({"id": policy_id, "Ramo": "Salud colectivo", "Name": "POL"},)),
                Page((relation,)),
            ),
            coql_pages=(Page((contact,)),),
            record={"Polizas": {"id": policy_id, "Ramo": "Salud colectivo", "Name": "POL"}},
        )
        detail, members = PolicyService(zoho).group(sign_record_id(policy_id, "policy"))
        self.assertEqual(detail.branch_code, "91")
        self.assertEqual(members[0].role, "Afiliado")
        self.assertEqual(members[0].relationship, "Afiliado")
        self.assertEqual(members[0].email, "persona@example.test")
        self.assertEqual(members[0].mobile, "3000000000")
        self.assertEqual(members[0].associate_name, contact["Full_Name"])
        self.assertEqual(zoho.coql.calls[0]["query"].split(" from ")[0], f"select {','.join(CONTACT_BATCH_FIELDS)}")

    def test_policy_layout_never_exposes_an_sdk_object_representation(self):
        class SDKLayoutLike:
            def get_name(self):
                return "Colectivos"

        policy_id = "4234567890123456789"
        zoho = FakeZoho(
            search_pages=(
                Page(({"id": policy_id, "Name": "POL", "Ramo": "Salud colectivo", "Layout": SDKLayoutLike()},)),
                Page(()),
            ),
            record={
                "Polizas": {
                    "id": policy_id,
                    "Name": "POL",
                    "Ramo": "Salud colectivo",
                    "Layout": SDKLayoutLike(),
                }
            },
        )
        detail, _members = PolicyService(zoho).group(sign_record_id(policy_id, "policy"))
        self.assertEqual(detail.layout_name, "Colectivos")
        self.assertEqual(detail.layout_category, "collective")
        self.assertNotIn("object at", repr(detail))
    def test_policy_hydration_fetches_each_risk_batch_only_once(self):
        policy_id = "4234567890123456789"
        contact_id = "5234567890123456781"
        risk_id = "6234567890123456781"
        relation = {
            "id": "3234567890123456789",
            "Asegurado": {"id": contact_id},
            "Riesgo": {"id": risk_id},
            "Estado": "Activo",
            "Ramo": "Salud colectivo",
        }
        zoho = FakeZoho(
            search_pages=(
                Page(({"id": policy_id, "Ramo": "Salud colectivo", "Name": "POL"},)),
                Page((relation,)),
            ),
            coql_pages=(
                Page(({"id": contact_id, "Full_Name": "Persona"},)),
                Page(({"id": risk_id, "Name": "Riesgo", "Tipo_de_riesgo": "Persona"},)),
            ),
            record={"Polizas": {"id": policy_id, "Ramo": "Salud colectivo", "Name": "POL"}},
        )
        service = PolicyService(zoho)
        detail, members = service.group(sign_record_id(policy_id, "policy"))
        self.assertEqual(len(zoho.coql.calls), 2)
        self.assertEqual(service.timings["records_queries"], 0)
        self.assertEqual(service.timings["search_queries"], 2)
        self.assertEqual(service.timings["coql_queries"], 2)
        self.assertEqual(service.timings["remote_queries"], 4)
        self.assertEqual(service.timings["insured_pages"], 1)
        self.assertEqual(service.timings["contacts_batches"], 1)
        self.assertEqual(service.timings["risks_batches"], 1)
        self.assertEqual(zoho.records.calls, [])
        self.assertEqual(zoho.search.calls[0]["module"], "Polizas")
        self.assertEqual(zoho.search.calls[0]["criteria"], f"(id:equals:{policy_id})")
        self.assertEqual(len(detail.risks), 1)
        self.assertEqual(members[0].risk_summary, "Persona")

    def test_beneficiary_member_retains_confirmed_associate_and_insured_lookups(self):
        policy_id = "4234567890123456789"
        associate_id = "5234567890123456781"
        insured_id = "5234567890123456782"
        beneficiary_id = "5234567890123456783"
        relation = {
            "id": "3234567890123456789",
            "Contacto_facturaci_n_dividida_colectivas": {"id": associate_id},
            "Asegurado": {"id": insured_id},
            "Beneficiario": {"id": beneficiary_id},
            "Estado": "Activo", "Ramo": "Salud colectivo", "Parentesco": "Hijo",
        }
        contacts = tuple(
            {
                "id": identifier, "Full_Name": label, "Tipo_ID": kind,
                "N_mero_de_ID": document,
            }
            for identifier, label, kind, document in (
                (associate_id, "Asociado", "CC", "100"),
                (insured_id, "Asegurado", "CC", "200"),
                (beneficiary_id, "Beneficiario", "TI", "300"),
            )
        )
        zoho = FakeZoho(
            search_pages=(
                Page(({"id": policy_id, "Ramo": "Salud colectivo", "Name": "POL"},)),
                Page((relation,)),
            ),
            coql_pages=(Page(contacts),),
            record={"Polizas": {"id": policy_id, "Ramo": "Salud colectivo", "Name": "POL"}},
        )
        _detail, members = PolicyService(zoho).group(sign_record_id(policy_id, "policy"))
        beneficiary = next(member for member in members if member.role == "Beneficiario")
        self.assertEqual(beneficiary.associate_document, "100")
        self.assertEqual(beneficiary.insured_document, "200")
        self.assertEqual(beneficiary.beneficiary_document, "300")
        self.assertEqual(beneficiary.relationship, "Hijo")

    def test_policy_relations_use_controlled_pagination_beyond_first_page(self):
        first = {"id": "3234567890123456781", "Estado": "Activo"}
        second = {"id": "3234567890123456782", "Estado": "Activo"}
        zoho = FakeZoho(search_pages=(
            Page(records=(first,), count=1, more_records=True),
            Page(records=(second,), count=1, more_records=False),
        ))
        service = PolicyService(zoho)
        records, truncated = service._relations("4234567890123456789")
        self.assertEqual(records, (first, second))
        self.assertFalse(truncated)
        self.assertEqual([call["page"] for call in zoho.search.calls], [1, 2])
        self.assertTrue(all(call["limit"] <= 200 for call in zoho.search.calls))

    @patch("cotizacion_colectivos.services.policies.POLICY_GROUP_LIMIT", 1200)
    @patch("cotizacion_colectivos.services.policies.POLICY_GROUP_PAGE_SIZE", 200)
    def test_policy_relations_complete_group_larger_than_legacy_thousand_limit(self):
        pages = []
        for page_number in range(6):
            size = 200 if page_number < 5 else 1
            records = tuple(
                {
                    "id": str(3_000_000_000_000_000_000 + page_number * 200 + index),
                    "Estado": "Activo",
                }
                for index in range(size)
            )
            pages.append(Page(records=records, count=size, more_records=page_number < 5))
        zoho = FakeZoho(search_pages=tuple(pages))
        records, truncated = PolicyService(zoho)._relations("4234567890123456789")
        self.assertEqual(len(records), 1001)
        self.assertFalse(truncated)
        self.assertEqual([call["page"] for call in zoho.search.calls], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(call["limit"] == 200 for call in zoho.search.calls))

    @patch("cotizacion_colectivos.services.policies.POLICY_GROUP_LIMIT", 1001)
    @patch("cotizacion_colectivos.services.policies.POLICY_GROUP_PAGE_SIZE", 200)
    def test_group_over_configured_max_fails_before_publishing_partial_data(self):
        pages = tuple(
            Page(
                records=tuple(
                    {"id": str(4_000_000_000_000_000_000 + page_number * 200 + index)}
                    for index in range(200)
                ),
                count=200,
                more_records=True,
            )
            for page_number in range(6)
        )
        with self.assertRaises(ColectivosServiceError) as raised:
            PolicyService(FakeZoho(search_pages=pages))._relations("4234567890123456789")
        self.assertEqual(raised.exception.code, "group_too_large")
        self.assertNotIn("parcial", raised.exception.message.casefold())

    def test_multiple_policy_and_risk_relations_are_fetched_in_fixed_batches(self):
        relations = tuple({
            "id": str(3234567890123456789 + index),
            "P_liza": {"id": str(4234567890123456789 + index), "name": "Referencia"},
            "Asegurado": {"id": COMPANY["id"], "name": "No usar"},
            "Riesgo": {"id": str(5234567890123456789 + index), "name": "Riesgo"},
            "Ramo": "Salud colectivo", "Estado": "Activo",
        } for index in range(2))
        policies = tuple({"id": item["P_liza"]["id"], "Name": "POL", "Ramo": "Salud colectivo"} for item in relations)
        risks = tuple({"id": item["Riesgo"]["id"], "Name": "RIESGO", "Tipo_de_riesgo": "Persona"} for item in relations)
        zoho = FakeZoho(
            search_pages=(Page(relations), Page(())),
            coql_pages=(Page(policies), Page(risks)),
            record={"Contacts": COMPANY},
        )
        detail = EntityDetailService(zoho).company(sign_record_id(COMPANY["id"], "company"))
        self.assertEqual(len(detail.policies), 2)
        self.assertEqual(len(detail.risks), 2)
        self.assertEqual(len(zoho.coql.calls), 2)
        self.assertEqual([call["module"] for call in zoho.records.calls], ["Contacts"])

    def test_no_public_write_methods_exist(self):
        for service in (CompanySearchService, PersonSearchService, EntityDetailService):
            for forbidden in ("create", "update", "delete", "upsert", "save", "write", "upload", "attach", "submit", "sync_to_zoho"):
                self.assertFalse(hasattr(service, forbidden))
