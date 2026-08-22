from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from cotizacion_colectivos.probe import summarize_value
from cotizacion_colectivos.tests.fakes import FakeMetadata, FakeRecords, FakeZoho


COMMAND_PATCH = (
    "cotizacion_colectivos.management.commands.colectivos_probe_data.get_zoho"
)


class ProbeCommandTests(SimpleTestCase):
    @patch(COMMAND_PATCH)
    def test_requires_explicit_confirmation(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "--allow-real-read"):
            call_command(
                "colectivos_probe_data",
                profile="sandbox",
                module="Contacts",
                fields=["Full_Name"],
            )
        get_zoho.assert_not_called()

    @patch(COMMAND_PATCH)
    def test_rejects_production_before_getting_facade(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command(
                "colectivos_probe_data",
                profile="production",
                module="Contacts",
                fields=["Full_Name"],
                allow_real_read=True,
            )
        get_zoho.assert_not_called()

    @patch(COMMAND_PATCH)
    def test_enforces_maximum_limit(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "entre 1 y 10"):
            call_command(
                "colectivos_probe_data",
                profile="sandbox",
                module="Contacts",
                fields=["Full_Name"],
                limit=11,
                allow_real_read=True,
            )
        get_zoho.assert_not_called()

    @patch(COMMAND_PATCH)
    def test_rejects_invalid_api_names_without_internal_details(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "no son validos"):
            call_command(
                "colectivos_probe_data",
                profile="sandbox",
                module="Contacts); delete",
                fields=["Full_Name"],
                allow_real_read=True,
            )
        get_zoho.assert_not_called()

    @patch(COMMAND_PATCH)
    def test_reads_only_requested_fields_and_masks_every_value(self, get_zoho):
        records = FakeRecords()
        facade = FakeZoho(metadata=FakeMetadata(), records=records)
        get_zoho.return_value = facade
        output = StringIO()
        call_command(
            "colectivos_probe_data",
            profile="sandbox",
            module="Contacts",
            fields=["Numero_de_documento,Full_Name"],
            limit=3,
            allow_real_read=True,
            stdout=output,
        )
        rendered = output.getvalue()
        self.assertNotIn("123456789", rendered)
        self.assertNotIn("Nombre Real No Debe Salir", rendered)
        self.assertIn("[texto, longitud=9]", rendered)
        self.assertEqual(
            records.calls,
            [
                {
                    "module": "Contacts",
                    "fields": ("Numero_de_documento", "Full_Name"),
                    "page": 1,
                    "limit": 3,
                }
            ],
        )
        get_zoho.assert_called_once_with(profile="sandbox")

    @patch(COMMAND_PATCH)
    def test_rejects_unknown_module(self, get_zoho):
        get_zoho.return_value = FakeZoho(
            metadata=FakeMetadata(), records=FakeRecords()
        )
        with self.assertRaisesMessage(CommandError, "no existe"):
            call_command(
                "colectivos_probe_data",
                profile="sandbox",
                module="Unknown",
                fields=["Name"],
                allow_real_read=True,
            )

    @patch(COMMAND_PATCH)
    def test_rejects_field_not_reported_by_fields_api(self, get_zoho):
        get_zoho.return_value = FakeZoho(
            metadata=FakeMetadata(), records=FakeRecords()
        )
        with self.assertRaisesMessage(CommandError, "no existen"):
            call_command(
                "colectivos_probe_data",
                profile="sandbox",
                module="Contacts",
                fields=["SecretField"],
                allow_real_read=True,
            )

    def test_summarizer_never_returns_original_values(self):
        values = (
            "correo@dominio.com",
            123456,
            {"id": "sensitive-id", "name": "Persona"},
            ["poliza-real"],
        )
        rendered = " ".join(summarize_value(value) for value in values)
        for original in ("correo@dominio.com", "123456", "sensitive-id", "poliza-real"):
            self.assertNotIn(original, rendered)
