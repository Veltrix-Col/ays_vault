from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from integrations.zoho.exceptions import ZohoAPIError
from integrations.zoho.schemas import Organization, Page

from cotizacion_colectivos.constants import CONTACTS_PROFILE_FIELDS
from cotizacion_colectivos.profiling import (
    ContactsProfileAccumulator,
    classify_document_pattern,
    classify_lookup_structure,
    render_contacts_profile_markdown,
)


COMMAND = "cotizacion_colectivos.management.commands.colectivos_profile_contacts"
GET_ZOHO_PATCH = f"{COMMAND}.get_zoho"


class FakeOrganization:
    def get(self):
        return Organization(
            organization_id="sandbox-org",
            company_name="Sandbox",
            environment="sandbox",
        )


class PagedRecords:
    def __init__(self, pages, *, fail_on_page=None):
        self.pages = pages
        self.fail_on_page = fail_on_page
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["page"] == self.fail_on_page:
            raise ZohoAPIError("fallo seguro")
        index = kwargs["page"] - 1
        records = tuple(self.pages[index]) if index < len(self.pages) else ()
        return Page(
            records=records,
            more_records=index + 1 < len(self.pages),
            page=kwargs["page"],
            count=len(records),
        )


class FakeZoho:
    profile = "sandbox"
    environment = "sandbox"
    organization = FakeOrganization()

    def __init__(self, records):
        self.records = records


def sample_records():
    return (
        {
            "id": "secret-id-1",
            "Tipo_de_persona": "Persona natural",
            "Tipo_ID": "CC",
            "N_mero_de_ID": "12.345.678",
            "First_Name": "Nombre Confidencial",
            "Last_Name": "Apellido Confidencial",
            "Full_Name": "Nombre Confidencial Apellido Confidencial",
            "Raz_n_social": "",
            "Nombre_comercial": "",
            "Estado": "Activo",
            "Empresa": {"id": "company-secret", "name": "Empresa Confidencial"},
        },
        {
            "id": "secret-id-2",
            "Tipo_de_persona": "Persona natural",
            "Tipo_ID": "CC",
            "N_mero_de_ID": "12345678",
            "First_Name": "Otra Persona",
            "Last_Name": "Otro Apellido",
            "Full_Name": "Otra Persona Otro Apellido",
            "Estado": "",
            "Empresa": None,
        },
        {
            "id": "secret-id-3",
            "Tipo_de_persona": "Persona jurídica",
            "Tipo_ID": "NIT",
            "N_mero_de_ID": "900.123.456-7",
            "Raz_n_social": "Empresa Ultra Secreta SAS",
            "Nombre_comercial": "Marca Secreta",
            "Full_Name": "",
            "Last_Name": "",
            "Estado": "Activo",
            "Empresa": {},
        },
    )


class ContactsProfileUnitTests(SimpleTestCase):
    def test_document_patterns_are_aggregate_categories(self):
        cases = {
            "123": "digits_only",
            "123-4": "digits_with_hyphen",
            "1.234": "digits_with_points",
            "12 34": "digits_with_spaces",
            "AB123": "alphanumeric",
            "12/34": "other",
            "": "empty",
        }
        self.assertEqual(
            {value: classify_document_pattern(value) for value in cases},
            cases,
        )

    def test_lookup_reports_structure_without_values(self):
        self.assertEqual(classify_lookup_structure(None), "empty")
        self.assertEqual(classify_lookup_structure({}), "empty")
        self.assertEqual(classify_lookup_structure({"id": "secret"}), "dict_id_only")
        self.assertEqual(classify_lookup_structure({"name": "secret"}), "dict_name_only")
        self.assertEqual(
            classify_lookup_structure({"id": "secret", "name": "secret"}),
            "dict_id_and_name",
        )

    def test_aggregation_counts_coverage_patterns_duplicates_and_consistency(self):
        accumulator = ContactsProfileAccumulator()
        for record in sample_records():
            accumulator.consume(record)
        result = accumulator.result(complete=True, pages=1)

        self.assertEqual(result["person_counts"], {"legal": 1, "natural": 2})
        self.assertEqual(result["id_counts"]["natural"], {"CC": 2})
        self.assertEqual(result["coverage"]["natural"]["Estado"], {"populated": 1, "empty": 1})
        self.assertEqual(result["document_patterns"]["legal"], {"other": 1})
        self.assertEqual(
            result["duplicates_analysis_normalized"],
            [
                {
                    "person_group": "natural",
                    "id_type": "CC",
                    "repeated_documents": 1,
                    "affected_records": 2,
                }
            ],
        )
        self.assertEqual(result["duplicates_exact"], [])
        self.assertEqual(result["lookup_structures"]["dict_id_and_name"], 1)

    def test_unexpected_values_are_grouped_not_echoed(self):
        record = {
            "Tipo_de_persona": "Clasificacion Privada",
            "Tipo_ID": "SECRETO",
            "N_mero_de_ID": "987654",
        }
        accumulator = ContactsProfileAccumulator()
        accumulator.consume(record)
        rendered = render_contacts_profile_markdown(
            accumulator.result(complete=True, pages=1)
        )
        self.assertNotIn("Clasificacion Privada", rendered)
        self.assertNotIn("SECRETO", rendered)
        self.assertNotIn("987654", rendered)
        self.assertIn("Otros valores no esperados", rendered)

    def test_report_never_contains_source_values_or_hashes(self):
        accumulator = ContactsProfileAccumulator()
        for record in sample_records():
            accumulator.consume(record)
        rendered = render_contacts_profile_markdown(
            accumulator.result(complete=True, pages=1)
        )
        for secret in (
            "12.345.678",
            "900.123.456-7",
            "Nombre Confidencial",
            "Empresa Ultra Secreta SAS",
            "company-secret",
            "secret-id-1",
            "Marca Secreta",
        ):
            self.assertNotIn(secret, rendered)
        self.assertNotIn("sha256", rendered.lower())


class ContactsProfileCommandTests(SimpleTestCase):
    @patch(GET_ZOHO_PATCH)
    def test_rejects_production_before_facade(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command(
                "colectivos_profile_contacts",
                profile="production",
                allow_real_read=True,
            )
        get_zoho.assert_not_called()

    @patch(GET_ZOHO_PATCH)
    def test_requires_explicit_confirmation(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "--allow-real-read"):
            call_command("colectivos_profile_contacts", profile="sandbox")
        get_zoho.assert_not_called()

    @patch(GET_ZOHO_PATCH)
    def test_uses_only_contacts_fixed_fields_and_controlled_pagination(self, get_zoho):
        records = PagedRecords((sample_records()[:2], sample_records()[2:]))
        get_zoho.return_value = FakeZoho(records)
        output = StringIO()
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            call_command(
                "colectivos_profile_contacts",
                profile="sandbox",
                allow_real_read=True,
                stdout=output,
            )
            report = Path(directory, "docs/cotizacion_colectivos/contacts_profile_analysis.md")
            self.assertTrue(report.exists())
            rendered = report.read_text(encoding="utf-8")

        self.assertEqual(len(records.calls), 2)
        for page_number, call in enumerate(records.calls, start=1):
            self.assertEqual(call["module"], "Contacts")
            self.assertEqual(call["fields"], CONTACTS_PROFILE_FIELDS)
            self.assertEqual(call["page"], page_number)
            self.assertLessEqual(call["limit"], 200)
        for secret in ("12.345.678", "Nombre Confidencial", "company-secret"):
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, rendered)
        get_zoho.assert_called_once_with(profile="sandbox")

    @patch(GET_ZOHO_PATCH)
    def test_api_failure_after_page_marks_partial_without_raw_data(self, get_zoho):
        records = PagedRecords((sample_records()[:1], sample_records()[1:]), fail_on_page=2)
        get_zoho.return_value = FakeZoho(records)
        output = StringIO()
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            call_command(
                "colectivos_profile_contacts",
                profile="sandbox",
                allow_real_read=True,
                stdout=output,
            )
            rendered = Path(
                directory, "docs/cotizacion_colectivos/contacts_profile_analysis.md"
            ).read_text(encoding="utf-8")
        self.assertIn("Resultado: **Parcial**", rendered)
        self.assertIn("api_error_api", rendered)
        self.assertNotIn("Nombre Confidencial", rendered)

    @patch(GET_ZOHO_PATCH)
    @patch(f"{COMMAND}.CONTACTS_PROFILE_MAX_RECORDS", 2)
    def test_internal_cap_marks_result_partial(self, get_zoho):
        records = PagedRecords((sample_records(), sample_records()))
        get_zoho.return_value = FakeZoho(records)
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            call_command(
                "colectivos_profile_contacts",
                profile="sandbox",
                allow_real_read=True,
                stdout=StringIO(),
            )
            rendered = Path(
                directory, "docs/cotizacion_colectivos/contacts_profile_analysis.md"
            ).read_text(encoding="utf-8")
        self.assertIn("Registros: **2**", rendered)
        self.assertIn("limite_defensivo_alcanzado", rendered)
        self.assertEqual(records.calls[0]["limit"], 2)

    @patch(GET_ZOHO_PATCH)
    def test_first_api_failure_is_controlled_and_writes_no_report(self, get_zoho):
        get_zoho.return_value = FakeZoho(PagedRecords(((),), fail_on_page=1))
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            with self.assertRaisesMessage(CommandError, "No fue posible perfilar"):
                call_command(
                    "colectivos_profile_contacts",
                    profile="sandbox",
                    allow_real_read=True,
                )
            self.assertFalse(
                Path(directory, "docs/cotizacion_colectivos/contacts_profile_analysis.md").exists()
            )
