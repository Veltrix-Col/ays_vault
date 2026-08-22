from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from integrations.zoho.exceptions import ZohoTimeoutError
from integrations.zoho.schemas import Page

from cotizacion_colectivos.management.commands.colectivos_diagnose_company_search import (
    DIAGNOSTIC_FIELDS,
)
from cotizacion_colectivos.services.mappings import CONTACT_SEARCH_FIELDS


SENSITIVE_DOCUMENT = "9001234567"
SENSITIVE_NAME = "Empresa Reservada"
SENSITIVE_ID = "1234567890123456789"


class FakeOrganization:
    def get(self):
        return SimpleNamespace(environment="production")


class FakeMetadata:
    def list_fields(self, module):
        assert module == "Contacts"
        return tuple(
            SimpleNamespace(
                api_name=api_name,
                field_label=label,
                data_type=data_type,
                pick_list_values=picklists,
            )
            for api_name, label, data_type, picklists in (
                ("N_mero_de_ID", "Número ID", "text", ()),
                ("Tipo_ID", "Tipo ID", "picklist", ({"actual_value": "NIT"},)),
                (
                    "Tipo_de_persona",
                    "Tipo de persona",
                    "picklist",
                    ({"actual_value": "Persona jurídica"},),
                ),
                ("Nombre_comercial", "Nombre comercial", "text", ()),
                ("Raz_n_social", "Razón social", "text", ()),
                ("Layout", "Diseño", "lookup", ()),
            )
        )


class FakeSearch:
    def __init__(self, *, mismatch=""):
        self.calls = []
        self.mismatch = mismatch

    def by_criteria(self, **kwargs):
        self.calls.append(kwargs)
        criteria = kwargs["criteria"]
        if self.mismatch == "timeout":
            raise ZohoTimeoutError("sensitive remote message")
        found = True
        if self.mismatch == "id_type" and "Tipo_ID:equals:NIT" in criteria:
            found = False
        if self.mismatch == "person_type" and "Tipo_de_persona:equals:Persona jurídica" in criteria:
            found = False
        record = {
            "id": SENSITIVE_ID,
            "N_mero_de_ID": SENSITIVE_DOCUMENT,
            "Tipo_ID": "NIT",
            "Tipo_de_persona": "Persona jurídica",
            "Layout": {"id": "9999999999999999999", "name": "Colectivos"},
            "Nombre_comercial": SENSITIVE_NAME,
            "Raz_n_social": SENSITIVE_NAME,
            "Estado": "Cliente",
        }
        return Page(records=(record,) if found else (), count=1 if found else 0)


class FakeZoho:
    profile = "production"
    environment = "production"
    backend_name = "sdk"

    def __init__(self, *, mismatch=""):
        self.organization = FakeOrganization()
        self.metadata = FakeMetadata()
        self.search = FakeSearch(mismatch=mismatch)


@override_settings(ZOHO_ACTIVE_PROFILE="production")
class CompanySearchDiagnosticTests(SimpleTestCase):
    def run_command(self, fake=None, **kwargs):
        stdout = StringIO()
        with patch(
            "cotizacion_colectivos.management.commands.colectivos_diagnose_company_search.get_zoho",
            return_value=fake or FakeZoho(),
        ):
            call_command(
                "colectivos_diagnose_company_search",
                profile=kwargs.pop("profile", "production"),
                document=kwargs.pop("document", SENSITIVE_DOCUMENT),
                allow_production_read=kwargs.pop("allow", True),
                stdout=stdout,
                **kwargs,
            )
        return stdout.getvalue()

    def test_rejects_non_production_profile(self):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            self.run_command(profile="sandbox")

    def test_requires_explicit_production_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--allow-production-read"):
            self.run_command(allow=False)

    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
    def test_requires_global_production_profile_for_real_service(self):
        with self.assertRaisesMessage(CommandError, "ZOHO_ACTIVE_PROFILE"):
            self.run_command()

    def test_runs_closed_variants_and_current_service(self):
        fake = FakeZoho()
        output = self.run_command(fake)
        self.assertEqual(len(fake.search.calls), 6)
        self.assertTrue(all(call["module"] == "Contacts" for call in fake.search.calls))
        self.assertTrue(all(call["limit"] == 20 for call in fake.search.calls))
        self.assertTrue(
            all(call["fields"] == DIAGNOSTIC_FIELDS for call in fake.search.calls[:5])
        )
        self.assertTrue(
            all(
                call["fields"] == CONTACT_SEARCH_FIELDS
                for call in fake.search.calls[5:]
            )
        )
        self.assertIn("A. Número ID exacto", output)
        self.assertIn("E. Consulta exacta actual del servicio", output)
        self.assertIn("F. Servicio completo", output)
        self.assertIn("Search API total: 6 llamada(s)", output)

    def test_output_never_contains_sensitive_values(self):
        output = self.run_command(FakeZoho())
        self.assertNotIn(SENSITIVE_DOCUMENT, output)
        self.assertNotIn(SENSITIVE_NAME, output)
        self.assertNotIn(SENSITIVE_ID, output)
        self.assertNotIn("9999999999999999999", output)
        self.assertIn("NIT", output)
        self.assertIn("Persona jurídica", output)
        self.assertIn("Colectivos", output)

    def test_identifies_id_type_as_eliminating_filter(self):
        output = self.run_command(FakeZoho(mismatch="id_type"))
        self.assertIn("Filtro que elimina: Tipo_ID=NIT", output)

    def test_identifies_person_type_as_eliminating_filter(self):
        output = self.run_command(FakeZoho(mismatch="person_type"))
        self.assertIn(
            "Filtro que elimina: Tipo_de_persona=Persona jurídica",
            output,
        )

    def test_api_error_is_sanitized(self):
        output = self.run_command(FakeZoho(mismatch="timeout"))
        self.assertIn("error=timeout", output)
        self.assertNotIn("sensitive remote message", output)

    def test_invalid_document_is_rejected_without_calling_zoho(self):
        with self.assertRaisesMessage(CommandError, "formato permitido"):
            self.run_command(document="123:456")
